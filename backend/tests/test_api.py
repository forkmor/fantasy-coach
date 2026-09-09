from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.security import password_hash


class FakeVault:
    def __init__(self, store):
        self.store = store

    def put(self, name, value):
        self.store.put_secret(name, b"fake-ciphertext")

    def get(self, name):
        return "test-only"

    def put_many(self, values):
        for name in values:
            self.store.put_secret(name, b"fake-ciphertext")


def login(client):
    response = client.post("/api/login", json={"token": "local-test"},
                           headers={"Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
    csrf = client.get("/api/session").json()["csrf_token"]
    return {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": csrf}


def test_local_auth_csrf_host_and_masking(tmp_path):
    app = create_app(tmp_path, "local-test", vault_factory=FakeVault)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/health", headers={"Host": "evil.test"}).status_code == 403
        headers = login(client)
        assert client.post("/api/pause", json={}).status_code == 403
        assert client.post("/api/pause", json={}, headers={"Origin": headers["Origin"]}).status_code == 403
        assert client.post("/api/pause", json={}, headers=headers).status_code == 200
        response = client.post("/api/credentials", json={"name": "grok", "value": "my-secret"}, headers=headers)
        assert response.status_code == 200
        state = client.get("/api/state")
        assert state.json()["credentials"]["grok"] is True
        assert "my-secret" not in state.text
        assert "fake-ciphertext" not in state.text
        image_policy = state.headers["content-security-policy"]
        assert "https://a.espncdn.com" in image_policy
        assert "https://a1.espncdn.com" in image_policy
        assert "script-src 'self'" in image_policy
        assert "*" not in image_policy
        response = client.post("/api/credentials", json={"name": "grok", "value": "my-secret\n"}, headers=headers)
        assert response.status_code == 422
        assert "my-secret" not in response.text


def test_public_password_https_cookie_and_origin_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_PUBLIC_URL", "https://fantasy-coach.tech")
    monkeypatch.setenv("HARNESS_PASSWORD_HASH", password_hash("correct-horse-fieldhouse"))
    app = create_app(tmp_path, vault_factory=FakeVault)
    with TestClient(app, base_url="https://fantasy-coach.tech") as client:
        assert client.get("/api/health").json() == {"status": "ok", "deployment": "public"}
        landing = client.get("/", headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        })
        assert landing.status_code == 200
        assert "<!doctype html>" in landing.text.lower()
        blocked_cross_site = client.post(
            "/api/login", json={"token": "correct-horse-fieldhouse"},
            headers={
                "Origin": "https://fantasy-coach.tech",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert blocked_cross_site.status_code == 403
        denied = client.post(
            "/api/login", json={"token": "wrong-password"},
            headers={"Origin": "https://fantasy-coach.tech"},
        )
        assert denied.status_code == 401
        blocked_origin = client.post(
            "/api/login", json={"token": "correct-horse-fieldhouse"},
            headers={"Origin": "https://evil.test"},
        )
        assert blocked_origin.status_code == 403
        response = client.post(
            "/api/login", json={"token": "correct-horse-fieldhouse"},
            headers={"Origin": "https://fantasy-coach.tech"},
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        csrf = client.get("/api/session").json()["csrf_token"]
        state = client.get("/api/state").json()
        assert state["onboarding_required"] is True
        assert state["manager"] is None
        assert client.post(
            "/api/espn-login/start", json={},
            headers={"Origin": "https://fantasy-coach.tech", "X-CSRF-Token": csrf},
        ).status_code == 409
        assert client.get("/api/state").headers["strict-transport-security"].startswith("max-age=")


def test_public_sessions_are_bound_to_verified_espn_teams(tmp_path, monkeypatch):
    class FakeEspn:
        def __init__(self, espn_s2, swid, league_id, team_id, season):
            self.swid = swid
            self.team_id = team_id

        async def discover_owned_team(self):
            team_id = 2 if self.swid == "{TEAM-TWO}" else 3
            return {"team_id": team_id, "team_name": f"Manager {team_id}"}

        async def test_connection(self):
            expected = 2 if self.swid == "{TEAM-TWO}" else 3
            assert self.team_id == expected
            return "verified"

    monkeypatch.setenv("HARNESS_PUBLIC_URL", "https://fantasy-coach.tech")
    monkeypatch.setenv("HARNESS_PASSWORD_HASH", password_hash("correct-horse-fieldhouse"))
    app = create_app(tmp_path, vault_factory=FakeVault, espn_factory=FakeEspn)
    origin = {"Origin": "https://fantasy-coach.tech"}
    with TestClient(app, base_url="https://fantasy-coach.tech") as client:
        client.post("/api/login", json={"token": "correct-horse-fieldhouse"}, headers=origin).raise_for_status()
        first_cookie = client.cookies.get("harness_session")
        first_csrf = client.get("/api/session").json()["csrf_token"]
        first_headers = {**origin, "X-CSRF-Token": first_csrf}
        client.post("/api/espn/connect", json={
            "espn_s2": "first-cookie", "swid": "{TEAM-TWO}",
        }, headers=first_headers).raise_for_status()
        client.post("/api/credentials", json={
            "name": "grok", "value": "first-provider-key",
        }, headers=first_headers).raise_for_status()

        client.cookies.clear()
        client.post("/api/login", json={"token": "correct-horse-fieldhouse"}, headers=origin).raise_for_status()
        second_cookie = client.cookies.get("harness_session")
        second_csrf = client.get("/api/session").json()["csrf_token"]
        second_headers = {**origin, "X-CSRF-Token": second_csrf}
        client.post("/api/espn/connect", json={
            "espn_s2": "second-cookie", "swid": "{TEAM-THREE}",
        }, headers=second_headers).raise_for_status()
        client.post("/api/credentials", json={
            "name": "gemini", "value": "second-provider-key",
        }, headers=second_headers).raise_for_status()
        second_state = client.get("/api/state").json()
        assert second_state["manager"]["team_id"] == 3
        assert second_state["credentials"]["grok"] is False
        assert second_state["credentials"]["gemini"] is True

        client.cookies.clear()
        client.cookies.set("harness_session", first_cookie)
        first_state = client.get("/api/state").json()
        assert first_state["manager"]["team_id"] == 2
        assert first_state["credentials"]["grok"] is True
        assert first_state["credentials"]["gemini"] is False
        assert second_cookie != first_cookie


def test_live_mode_and_incomplete_setup_blocked(tmp_path, policy_data):
    app = create_app(tmp_path, "local-test", vault_factory=FakeVault)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = login(client)
        response = client.post("/api/settings", json={
            "provider": "grok", "model": "test", "mode": "live", "paused": False,
        }, headers=headers)
        assert response.status_code == 409
        capabilities = client.get("/api/state").json()["capabilities"]
        assert next(item for item in capabilities if item["action"] == "set_lineup")["enabled"] is True
        assert next(item for item in capabilities if item["action"] == "add_drop")["enabled"] is True
        assert all(not item["enabled"] for item in capabilities
                   if item["action"] not in {"set_lineup", "add_drop"})
        assert client.post("/api/runs", json={}, headers=headers).status_code == 409
        assert client.post("/api/policy", json=policy_data, headers=headers).status_code == 200
        assert client.get("/api/state").json()["policy"] == policy_data
        assert client.post("/api/schedule", json={
            "enabled": True, "timezone": "America/Chicago", "interval_minutes": 60,
        }, headers=headers).status_code == 409


def test_saved_settings_survive_restart(tmp_path, policy_data):
    for attempt in range(2):
        app = create_app(tmp_path, "local-test", vault_factory=FakeVault)
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            headers = login(client)
            if attempt == 0:
                client.post("/api/policy", json=policy_data, headers=headers).raise_for_status()
                client.post("/api/schedule", json={
                    "enabled": False, "timezone": "America/New_York", "interval_minutes": 30,
                }, headers=headers).raise_for_status()
            state = client.get("/api/state").json()
            assert state["policy"] == policy_data
            assert state["schedule"]["timezone"] == "America/New_York"
            assert state["schedule"]["enabled"] is False


def test_restart_preserves_future_schedule_and_skips_obsolete_missed_run(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    app = create_app(tmp_path, "local-test", vault_factory=FakeVault)
    app.state.store.set("schedule", {
        "enabled": True, "timezone": "UTC", "interval_minutes": 60, "next_run_at": future,
    })
    with TestClient(app, base_url="http://127.0.0.1:8765"):
        assert app.state.store.get("schedule")["next_run_at"] == future

    past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    app.state.store.set("schedule", {
        "enabled": True, "timezone": "UTC", "interval_minutes": 60, "next_run_at": past,
    })
    restarted = create_app(tmp_path, "local-test", vault_factory=FakeVault)
    with TestClient(restarted, base_url="http://127.0.0.1:8765"):
        next_run = datetime.fromisoformat(restarted.state.store.get("schedule")["next_run_at"])
        assert next_run > datetime.now(timezone.utc)
        with restarted.state.store.connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='schedule_missed'"
            ).fetchone()[0] == 1


def test_browser_login_routes_are_owner_only_and_block_competing_setup(tmp_path):
    from test_browser_login import fake_browser

    app = create_app(tmp_path, "local-test", vault_factory=FakeVault)
    factory, _, _, _, _ = fake_browser()
    app.state.browser_login.browser_factory = factory
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/espn-login").status_code == 401
        headers = login(client)
        assert client.post("/api/espn-login/start", json={}).status_code == 403
        result = client.post("/api/espn-login/start", json={}, headers=headers)
        assert result.status_code == 200 and result.json()["active"]
        state = client.get("/api/state").json()
        assert state["espn_login"]["status"] == "waiting"
        assert client.post("/api/credentials", json={"name": "grok", "value": "synthetic"},
                           headers=headers).status_code == 409
        assert client.post("/api/runs", json={}, headers=headers).status_code == 409
        assert client.post("/api/espn-login/cancel", json={}, headers=headers).json()["status"] == "cancelled"
