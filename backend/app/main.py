import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from .browser_login import BrowserLogin
from .espn import CAPABILITIES, EspnClient, EspnError, EspnUnknownOutcome
from .models import (
    ActionInput, AddDropConfirmInput, AddDropPreviewInput, CredentialsInput,
    EspnConnectInput, LineupSwapConfirmInput, LineupSwapPreviewInput, LoginInput, Policy,
    RunInput, Schedule, Settings, TestConnectionInput,
)
from .policy import check_acquisition_limits, check_policy
from .providers import ProviderClient, ProviderError
from .player_data import PlayerDataClient, PlayerDataError
from .runner import RunService
from .security import InstanceLock, LocalSession, SecretError, Vault
from .storage import ConflictError, Store
from .tenancy import (
    LEAGUE_ID, SEASON, RuntimeProxy, RuntimeRegistry, bind_runtime, current_runtime,
    reset_runtime,
)


ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: Path | None = None, launch_token: str | None = None,
               vault_factory=Vault, player_data_factory=PlayerDataClient,
               espn_factory=None) -> FastAPI:
    data_dir = data_dir or Path(os.environ.get("HARNESS_DATA_DIR", ROOT / "data"))
    public_url = os.environ.get("HARNESS_PUBLIC_URL", "").rstrip("/")
    public_mode = bool(public_url)
    if public_mode:
        parsed_public_url = urlsplit(public_url)
        if (parsed_public_url.scheme != "https" or not parsed_public_url.hostname
                or parsed_public_url.username or parsed_public_url.password
                or parsed_public_url.path or parsed_public_url.query or parsed_public_url.fragment):
            raise RuntimeError("HARNESS_PUBLIC_URL must be an HTTPS origin without a path")
        password_digest = os.environ.get("HARNESS_PASSWORD_HASH")
        if not password_digest:
            raise RuntimeError("HARNESS_PASSWORD_HASH is required for public deployment")
    else:
        parsed_public_url = None
        password_digest = None
    session = LocalSession(
        None if public_mode else launch_token or os.environ.get("HARNESS_LAUNCH_TOKEN"),
        password_digest=password_digest,
        default_team_id=None if public_mode else 4,
        database=data_dir / "sessions.sqlite3" if public_mode else None,
    )
    espn_factory = espn_factory or EspnClient
    player_data = player_data_factory()
    registry = RuntimeRegistry(
        data_dir, vault_factory=vault_factory, player_data=player_data,
        espn_factory=espn_factory, public_mode=public_mode,
    )
    if not public_mode:
        registry.runtimes[4] = registry._build(4)
    store = RuntimeProxy("store")
    vault = RuntimeProxy("vault")
    browser_login = RuntimeProxy("browser_login")
    service = RuntimeProxy("service")
    instance_lock = InstanceLock(data_dir / "instance.lock")
    failed_logins: dict[str, list[float]] = {}
    global_failed_logins: list[float] = []
    espn_connect_attempts: dict[str, list[float]] = {}
    port = os.environ.get("HARNESS_PORT", "8765")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {public_url} if public_mode else {f"http://{host}" for host in allowed_hosts}
    if parsed_public_url is not None:
        allowed_hosts.add(parsed_public_url.netloc)
    if not public_mode and os.environ.get("HARNESS_DEV") == "1":
        allowed_origins.update({"http://127.0.0.1:5173", "http://localhost:5173"})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        instance_lock.acquire()
        try:
            await registry.startup()
            yield
        finally:
            await registry.shutdown()
            await player_data.aclose()
            instance_lock.release()

    app = FastAPI(title="Fantasy Football Harness", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    default_runtime = registry.runtimes.get(4)
    app.state.store = default_runtime.store if default_runtime else None
    app.state.service = default_runtime.service if default_runtime else None
    app.state.vault = default_runtime.vault if default_runtime else None
    app.state.session = session
    app.state.browser_login = default_runtime.browser_login if default_runtime else None
    app.state.registry = registry

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "Invalid local host"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed"}, status_code=403)
            if request.headers.get("origin") not in allowed_origins:
                return JSONResponse({"detail": "A trusted local origin is required"}, status_code=403)
            length = request.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > 131072):
                return JSONResponse({"detail": "Request body is too large"}, status_code=413)
            body = await request.body()
            if len(body) > 131072:
                return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        path = request.url.path
        runtime_token = None
        if path.startswith("/api/") and path not in {"/api/login", "/api/health"}:
            record = session.get(request.cookies.get("harness_session"))
            if record is None:
                return JSONResponse({
                    "detail": "Enter the league password to sign in"
                    if public_mode else "Open the local launch link to sign in",
                }, status_code=401)
            request.state.manager_session = record
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), record.csrf):
                    return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
            if public_mode and record.team_id is None and path not in {
                "/api/session", "/api/state", "/api/espn/connect", "/api/logout",
            }:
                return JSONResponse({
                    "detail": "Connect ESPN and verify ownership of your league team first",
                }, status_code=409)
            if record.team_id is not None:
                runtime_token = bind_runtime(await registry.get(record.team_id))
        try:
            response = await call_next(request)
        finally:
            if runtime_token is not None:
                reset_runtime(runtime_token)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://a.espncdn.com https://a1.espncdn.com https://a2.espncdn.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if public_mode:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response echoes rejected inputs, which could contain credentials.
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                  for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    async def expected_error(request, exc):
        status = 409 if isinstance(exc, ConflictError) else 502
        if isinstance(exc, SecretError):
            status = 400
        return JSONResponse({"detail": str(exc)}, status_code=status)

    for error_class in (ConflictError, SecretError, ProviderError, EspnError, PlayerDataError):
        app.add_exception_handler(error_class, expected_error)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        logging.getLogger("harness").error("Request failed (%s)", type(exc).__name__)
        return JSONResponse({"detail": "Internal application error; check local configuration"}, status_code=500)

    @app.get("/api/health")
    async def health():
        registry.repair_schedulers()
        return {"status": "ok", "deployment": "public" if public_mode else "local"}

    @app.post("/api/login")
    async def login(body: LoginInput, request: Request):
        clock = time.monotonic()
        client_key = request.client.host if request.client else "unknown"
        global_failed_logins[:] = [
            stamp for stamp in global_failed_logins if clock - stamp < 60
        ]
        if len(global_failed_logins) >= 50:
            raise HTTPException(429, "Too many login attempts; wait one minute")
        for key in list(failed_logins):
            failed_logins[key] = [
                stamp for stamp in failed_logins[key] if clock - stamp < 60
            ]
            if not failed_logins[key]:
                failed_logins.pop(key, None)
        attempts = [stamp for stamp in failed_logins.get(client_key, []) if clock - stamp < 60]
        failed_logins[client_key] = attempts
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many login attempts; wait one minute")
        if not session.valid_login(body.token):
            attempts.append(clock)
            global_failed_logins.append(clock)
            raise HTTPException(401, "Invalid league password" if public_mode else "Invalid local launch token")
        failed_logins.pop(client_key, None)
        record = session.create()
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "harness_session", record.cookie, httponly=True, secure=public_mode,
            samesite="strict", path="/", max_age=43_200,
        )
        return response

    @app.get("/api/session")
    async def get_session(request: Request):
        record = request.state.manager_session
        return {
            "csrf_token": record.csrf,
            "team_id": record.team_id,
            "espn_connected": record.team_id is not None,
        }

    @app.post("/api/logout")
    async def logout(request: Request):
        session.revoke(request.cookies.get("harness_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            "harness_session", path="/", secure=public_mode,
            httponly=True, samesite="strict",
        )
        return response

    @app.get("/api/state")
    async def state(request: Request):
        record = request.state.manager_session
        if record.team_id is None:
            return {
                "onboarding_required": True,
                "manager": None,
                "league": {"league_id": LEAGUE_ID, "season": SEASON},
            }
        return {
            "onboarding_required": False,
            "manager": store.get("manager_profile", {
                "team_id": record.team_id,
                "team_name": f"Team {record.team_id}",
            }),
            "settings": Settings.model_validate(
                store.get("settings", Settings().model_dump()),
            ).model_dump(),
            "credentials": store.credential_status(),
            "browser_automation_ready": store.secret("espn_browser_state") is not None,
            "browser_login_available": not public_mode,
            "policy": store.get("policy"),
            "schedule": store.get("schedule", {**Schedule().model_dump(), "next_run_at": None}),
            "capabilities": CAPABILITIES,
            "active_run": store.active_run(),
            "espn_login": browser_login.state(),
        }

    @app.post("/api/espn/connect")
    async def connect_espn(body: EspnConnectInput, request: Request):
        record = request.state.manager_session
        clock = time.monotonic()
        attempts = [
            stamp for stamp in espn_connect_attempts.get(record.cookie, [])
            if clock - stamp < 300
        ]
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many ESPN connection attempts; wait five minutes")
        attempts.append(clock)
        espn_connect_attempts[record.cookie] = attempts
        probe = espn_factory(
            body.espn_s2, body.swid, league_id=LEAGUE_ID, team_id=1, season=SEASON,
        )
        profile = await probe.discover_owned_team()
        team_id = profile["team_id"]
        if record.team_id is not None and record.team_id != team_id:
            raise ConflictError("Sign out before connecting a different ESPN team")
        runtime = await registry.get(team_id)
        if runtime.store.active_run():
            raise ConflictError("Stop the active coach before replacing ESPN credentials")
        verified = espn_factory(
            body.espn_s2, body.swid,
            league_id=LEAGUE_ID, team_id=team_id, season=SEASON,
        )
        await verified.test_connection()
        runtime.vault.put_many({"espn_s2": body.espn_s2, "swid": body.swid})
        runtime.store.set("manager_profile", {
            **profile, "league_id": LEAGUE_ID, "season": SEASON,
        })
        runtime.store.event("espn_credentials_connected", {
            "team_id": team_id, "ownership_verified": True,
        })
        session.bind_team(record.cookie, team_id)
        espn_connect_attempts.pop(record.cookie, None)
        return {
            "ok": True,
            "manager": runtime.store.get("manager_profile"),
            "detail": f"ESPN ownership verified for {profile['team_name']}",
        }

    def require_idle():
        if store.active_run():
            raise ConflictError("Stop the active run before changing provider settings or credentials")
        if browser_login.active:
            raise ConflictError("Finish or cancel ESPN browser sign-in before changing settings or credentials")

    @app.get("/api/espn-login")
    async def espn_login_state():
        return browser_login.state()

    @app.post("/api/espn-login/start")
    async def espn_login_start():
        require_idle()
        if public_mode:
            raise ConflictError(
                "Browser-assisted ESPN sign-in is available only on the local Windows app; "
                "enter ESPN cookies through the encrypted credential form"
            )
        return await browser_login.start()

    @app.post("/api/espn-login/finish")
    async def espn_login_finish():
        return await browser_login.finish()

    @app.post("/api/espn-login/cancel")
    async def espn_login_cancel():
        return await browser_login.cancel()

    @app.post("/api/credentials")
    async def save_credentials(body: CredentialsInput):
        require_idle()
        if public_mode and body.name in {"espn_s2", "swid"}:
            raise ConflictError("Use the verified ESPN connection form to replace ESPN credentials")
        vault.put(body.name, body.value)
        store.event("credential_updated", {"name": body.name})
        return {"ok": True}

    @app.delete("/api/credentials/{name}")
    async def delete_credentials(name: str):
        require_idle()
        if name not in {"grok", "gemini", "espn_s2", "swid"}:
            raise HTTPException(404, "Unknown credential")
        if public_mode and name in {"espn_s2", "swid"}:
            raise ConflictError("ESPN credentials are managed together through the verified connection")
        store.delete_secret(name)
        if name in {"espn_s2", "swid"}:
            store.delete_secret("espn_browser_state")
        store.event("credential_removed", {"name": name})
        return {"ok": True}

    @app.post("/api/settings")
    async def save_settings(body: Settings):
        require_idle()
        if body.mode == "live":
            policy_data = store.get("policy")
            if policy_data is None:
                raise ConflictError("Save Coach settings before enabling live mode")
            allowed = Policy.model_validate(policy_data).allowed_actions
            enabled = {item["action"] for item in CAPABILITIES if item["enabled"]}
            if not enabled.intersection(allowed):
                raise ConflictError("None of the permitted actions has a verified live executor")
        store.set("settings", body.model_dump())
        store.event("settings_changed", body.model_dump())
        return body

    @app.post("/api/pause")
    async def pause():
        settings = store.get("settings", Settings().model_dump())
        settings["paused"] = True
        store.set("settings", settings)
        active = store.active_run()
        if active:
            service.cancel(active["id"])
        store.event("management_paused", {"detail": "All new agent tool calls are blocked"})
        return {"ok": True}

    @app.post("/api/policy")
    async def save_policy(body: Policy):
        version = store.set_policy(body.model_dump())
        # A lowered run budget must not leave an existing run using its earlier allowance.
        active = store.active_run()
        if active:
            service.cancel(active["id"])
        return {"ok": True, "version": version}

    @app.post("/api/schedule")
    async def save_schedule(body: Schedule):
        if body.enabled:
            service.ready()
        value = {**body.model_dump(), "next_run_at": None}
        if body.enabled:
            value["next_run_at"] = (
                datetime.now(timezone.utc) + timedelta(minutes=body.interval_minutes)
            ).isoformat()
        store.set("schedule", value)
        store.event("schedule_changed", value)
        return value

    @app.post("/api/connections/test")
    async def test_connection(body: TestConnectionInput):
        require_idle()
        if body.target == "espn":
            detail = await service.espn().test_connection()
        else:
            settings = Settings.model_validate(store.get("settings", Settings().model_dump()))
            if body.target != settings.provider:
                raise ConflictError("Select this provider and its model before testing its connection")
            if not settings.model:
                raise ConflictError("Configure a model ID before testing")
            detail = await ProviderClient(body.target, vault.get(body.target), settings.model).test_connection()
        store.event("connection_test", {"target": body.target, "ok": True})
        return {"ok": True, "detail": detail}

    @app.get("/api/team")
    async def team():
        snapshot = await service.espn().snapshot()
        snapshot = await player_data.enrich_snapshot(snapshot)
        store.set("last_snapshot", snapshot)
        return snapshot

    @app.get("/api/players/available")
    async def available_players(position: str | None = None, limit: int = 50):
        return await service.espn().available_players(position, limit)

    @app.get("/api/league")
    async def league():
        return await service.espn().league_overview()

    @app.post("/api/lineup/swap/preview")
    async def preview_lineup_swap(body: LineupSwapPreviewInput):
        require_idle()
        if store.unresolved_live_action():
            raise ConflictError(
                "A previous live action has an unresolved outcome. Check ESPN before preparing another swap."
            )
        policy_data = store.get("policy")
        if policy_data is None or "set_lineup" not in Policy.model_validate(policy_data).allowed_actions:
            raise ConflictError("Enable lineup changes in Coach settings before preparing a live swap")
        preview = await service.espn().preview_lineup_swap(
            body.starter_player_id, body.bench_player_id,
        )
        fingerprint = "|".join(str(value) for value in (
            preview["scoring_period_id"],
            preview["starter"]["player_id"], preview["starter"]["from_slot_id"],
            preview["bench"]["player_id"], preview["bench"]["from_slot_id"],
        ))
        action = store.add_action(
            "owner", {
                "action": "set_lineup",
                "starter_player_id": body.starter_player_id,
                "bench_player_id": body.bench_player_id,
                "preview": preview,
            },
            "pending_confirmation",
            {"detail": "Waiting for the owner to confirm this exact ESPN lineup swap"},
            store.get("policy_version", 0),
            fingerprint,
        )
        store.event("lineup_swap_previewed", {
            "action_id": action["id"],
            "starter": preview["starter"]["name"],
            "bench": preview["bench"]["name"],
        })
        return {"operation_id": action["id"], "status": action["status"], **preview}

    @app.post("/api/lineup/swap/confirm")
    async def confirm_lineup_swap(body: LineupSwapConfirmInput):
        require_idle()
        async with current_runtime().mutation_lock:
            action = store.action(body.operation_id)
            if action is None or action["action"] != "set_lineup" or action["run_id"] != "owner":
                raise HTTPException(404, "Lineup confirmation not found")
            created = datetime.fromisoformat(action["created_at"])
            if datetime.now(timezone.utc) - created > timedelta(minutes=5):
                if action["status"] == "pending_confirmation":
                    store.update_action(
                        action["id"], expected_status="pending_confirmation", status="expired",
                        result={"detail": "Confirmation expired; prepare a fresh lineup preview"},
                    )
                raise ConflictError("This lineup confirmation expired; prepare and review it again")
            if action["status"] != "pending_confirmation":
                raise ConflictError(f"This lineup operation is already {action['status']}")
            policy_data = store.get("policy")
            if policy_data is None or "set_lineup" not in Policy.model_validate(policy_data).allowed_actions:
                raise ConflictError("Lineup permission was removed after preview")
            store.update_action(
                action["id"], expected_status="pending_confirmation", status="submitting",
                result={"detail": "Submitting once to ESPN; automatic mutation retries are disabled"},
            )
            try:
                result = await service.espn().execute_lineup_swap(action["payload"]["preview"])
            except EspnUnknownOutcome as exc:
                store.update_action(action["id"], expected_status="submitting", status="unknown",
                                    result={"detail": str(exc)})
                store.event("lineup_swap_unknown", {"action_id": action["id"]})
                return JSONResponse(
                    {"operation_id": action["id"], "status": "unknown", "detail": str(exc)},
                    status_code=202,
                )
            except EspnError as exc:
                store.update_action(action["id"], expected_status="submitting", status="failed",
                                    result={"detail": str(exc)})
                store.event("lineup_swap_failed", {"action_id": action["id"]})
                raise
            store.update_action(action["id"], expected_status="submitting", status="completed",
                                result=result)
            store.event("lineup_swap_completed", {"action_id": action["id"], **result})
            return {"operation_id": action["id"], **result}

    @app.post("/api/roster/change/preview")
    async def preview_roster_change(body: AddDropPreviewInput):
        require_idle()
        if store.unresolved_live_action():
            raise ConflictError(
                "A previous live action has an unresolved outcome. Check ESPN before preparing another change."
            )
        policy_data = store.get("policy")
        if policy_data is None:
            raise ConflictError("Complete Coach settings before preparing a live roster change")
        policy = Policy.model_validate(policy_data)
        action_input = ActionInput(
            action="add_drop",
            reason="Direct owner roster change",
            add_player_id=body.add_player_id,
            drop_player_id=body.drop_player_id,
        )
        snapshot = await service.espn().snapshot()
        denials = check_policy(action_input, policy, snapshot, service.team_id)
        denials.extend(check_acquisition_limits(
            action_input, policy, await service.espn().transactions(),
        ))
        if denials:
            raise ConflictError("; ".join(dict.fromkeys(denials)))
        preview = await service.espn().preview_add_drop(
            body.add_player_id, body.drop_player_id,
        )
        action = store.add_action(
            "owner",
            {"action": "add_drop", "request": action_input.model_dump(), "preview": preview},
            "pending_confirmation",
            {"detail": "Waiting for the owner to confirm this exact ESPN roster change"},
            store.get("policy_version", 0),
            f"{preview['operation']}|{(preview.get('add') or preview.get('drop'))['player_id']}",
        )
        store.event("roster_change_previewed", {
            "action_id": action["id"],
            "operation": preview["operation"],
            "player": (preview.get("add") or preview.get("drop"))["name"],
        })
        return {"operation_id": action["id"], "status": action["status"], **preview}

    @app.post("/api/roster/change/confirm")
    async def confirm_roster_change(body: AddDropConfirmInput):
        require_idle()
        async with current_runtime().mutation_lock:
            action = store.action(body.operation_id)
            if action is None or action["action"] != "add_drop" or action["run_id"] != "owner":
                raise HTTPException(404, "Roster-change confirmation not found")
            created = datetime.fromisoformat(action["created_at"])
            if datetime.now(timezone.utc) - created > timedelta(minutes=5):
                if action["status"] == "pending_confirmation":
                    store.update_action(
                        action["id"], expected_status="pending_confirmation", status="expired",
                        result={"detail": "Confirmation expired; prepare a fresh roster-change preview"},
                    )
                raise ConflictError("This roster-change confirmation expired; prepare and review it again")
            if action["status"] != "pending_confirmation":
                raise ConflictError(f"This roster change is already {action['status']}")
            if action["policy_version"] != store.get("policy_version", 0):
                raise ConflictError("Coach policy changed after this roster change was previewed")
            policy_data = store.get("policy")
            if policy_data is None:
                raise ConflictError("Coach policy is unavailable")
            policy = Policy.model_validate(policy_data)
            action_input = ActionInput.model_validate(action["payload"]["request"])
            snapshot = await service.espn().snapshot()
            denials = check_policy(action_input, policy, snapshot, service.team_id)
            denials.extend(check_acquisition_limits(
                action_input, policy, await service.espn().transactions(),
            ))
            if denials:
                store.update_action(
                    action["id"], expected_status="pending_confirmation", status="blocked",
                    result={"detail": "; ".join(dict.fromkeys(denials))},
                )
                raise ConflictError("; ".join(dict.fromkeys(denials)))
            store.update_action(
                action["id"], expected_status="pending_confirmation", status="submitting",
                result={"detail": "Submitting once to ESPN; automatic mutation retries are disabled"},
            )
            try:
                result = await service.espn().execute_add_drop(action["payload"]["preview"])
            except EspnUnknownOutcome as exc:
                store.update_action(action["id"], expected_status="submitting", status="unknown",
                                    result={"detail": str(exc)})
                store.event("roster_change_unknown", {"action_id": action["id"]})
                return JSONResponse(
                    {"operation_id": action["id"], "status": "unknown", "detail": str(exc)},
                    status_code=202,
                )
            except EspnError as exc:
                store.update_action(action["id"], expected_status="submitting", status="failed",
                                    result={"detail": str(exc)})
                store.event("roster_change_failed", {"action_id": action["id"]})
                raise
            store.update_action(action["id"], expected_status="submitting", status="completed",
                                result=result)
            store.event("roster_change_completed", {"action_id": action["id"], **result})
            return {"operation_id": action["id"], **result}

    @app.get("/api/players/{player_id}")
    async def player_detail(player_id: int):
        snapshot = store.get("last_snapshot")
        if not snapshot or not any(player.get("player_id") == player_id for player in snapshot.get("roster", [])):
            raise HTTPException(404, "Player not found in your loaded roster. Refresh your team first.")
        return await player_data.player_detail(player_id)

    @app.get("/api/runs")
    async def runs():
        return store.runs()

    @app.post("/api/runs")
    async def start_run(body: RunInput):
        return service.start(body.objective)

    @app.get("/api/runs/{identifier}")
    async def run_detail(identifier: str):
        run = store.run(identifier)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @app.post("/api/runs/{identifier}/cancel")
    async def cancel_run(identifier: str):
        service.cancel(identifier)
        return {"ok": True}

    @app.get("/api/actions")
    async def actions():
        return store.actions()

    @app.get("/api/activity")
    async def activity():
        import json
        with store.connect() as db:
            return [{**dict(row), "data": json.loads(row["data"])} for row in db.execute(
                "SELECT * FROM events WHERE run_id IS NULL ORDER BY id DESC LIMIT 100")]

    frontend = ROOT / "frontend" / "dist"

    @app.get("/{path:path}")
    async def ui(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        requested = (frontend / path).resolve()
        if not requested.is_relative_to(frontend.resolve()):
            raise HTTPException(404, "Not found")
        if requested.is_file():
            return FileResponse(requested)
        index = frontend / "index.html"
        if not index.is_file():
            return JSONResponse({"detail": "Frontend not built. Run setup.ps1 before starting."}, status_code=503)
        return FileResponse(index)

    return app
