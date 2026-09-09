import asyncio
import contextvars
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .browser_login import BrowserLogin
from .models import Schedule, Settings
from .player_data import PlayerDataClient
from .runner import RunService
from .security import Vault
from .storage import Store


LEAGUE_ID = 656212638
SEASON = 2026
_runtime: contextvars.ContextVar["ManagerRuntime | None"] = contextvars.ContextVar(
    "fieldhouse_manager_runtime", default=None,
)


@dataclass
class ManagerRuntime:
    team_id: int
    store: Store
    vault: Vault
    browser_login: BrowserLogin
    service: RunService
    mutation_lock: asyncio.Lock


class RuntimeProxy:
    def __init__(self, attribute: str):
        self.attribute = attribute

    def __getattr__(self, name):
        runtime = _runtime.get()
        if runtime is None:
            raise RuntimeError("A verified ESPN team session is required")
        return getattr(getattr(runtime, self.attribute), name)


def bind_runtime(runtime: ManagerRuntime):
    return _runtime.set(runtime)


def reset_runtime(token):
    _runtime.reset(token)


def current_runtime() -> ManagerRuntime:
    runtime = _runtime.get()
    if runtime is None:
        raise RuntimeError("A verified ESPN team session is required")
    return runtime


class RuntimeRegistry:
    def __init__(self, data_dir: Path, vault_factory=Vault,
                 player_data: PlayerDataClient | None = None, espn_factory=None,
                 public_mode: bool = False):
        self.data_dir = data_dir
        self.vault_factory = vault_factory
        self.player_data = player_data
        self.espn_factory = espn_factory
        self.public_mode = public_mode
        self.runtimes: dict[int, ManagerRuntime] = {}
        self.lock = asyncio.Lock()

    def _path(self, team_id: int) -> Path:
        if not self.public_mode:
            if team_id != 4:
                raise ValueError("Local mode supports only the configured team")
            return self.data_dir / "harness.sqlite3"
        return self.data_dir / "teams" / f"team-{team_id}.sqlite3"

    def _build(self, team_id: int) -> ManagerRuntime:
        store = Store(self._path(team_id))
        vault = self.vault_factory(store)
        browser_login = BrowserLogin(store, vault)
        kwargs = {
            "credentials_busy": lambda: browser_login.active,
            "player_data": self.player_data,
            "league_id": LEAGUE_ID,
            "team_id": team_id,
            "season": SEASON,
        }
        if self.espn_factory is not None:
            kwargs["espn_factory"] = self.espn_factory
        service = RunService(store, vault, **kwargs)
        return ManagerRuntime(team_id, store, vault, browser_login, service, asyncio.Lock())

    async def startup(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.public_mode:
            teams_dir = self.data_dir / "teams"
            teams_dir.mkdir(parents=True, exist_ok=True)
            team_ids = []
            for path in teams_dir.glob("team-*.sqlite3"):
                try:
                    team_id = int(path.stem.removeprefix("team-"))
                except ValueError:
                    continue
                if team_id > 0:
                    team_ids.append(team_id)
        else:
            team_ids = [4]
        for team_id in team_ids:
            runtime = self.runtimes.get(team_id)
            if runtime is None:
                runtime = self._build(team_id)
                self.runtimes[team_id] = runtime
            self._recover(runtime.store)
            if runtime.service.scheduler_task is None:
                runtime.service.scheduler_task = asyncio.create_task(runtime.service.scheduler())

    async def get(self, team_id: int) -> ManagerRuntime:
        if type(team_id) is not int or team_id <= 0:
            raise ValueError("Invalid ESPN team")
        existing = self.runtimes.get(team_id)
        if existing is not None:
            return existing
        async with self.lock:
            existing = self.runtimes.get(team_id)
            if existing is not None:
                return existing
            runtime = self._build(team_id)
            self._recover(runtime.store)
            runtime.service.scheduler_task = asyncio.create_task(runtime.service.scheduler())
            self.runtimes[team_id] = runtime
            return runtime

    @staticmethod
    def _recover(store: Store):
        store.recover()
        if store.get("settings") is None:
            store.set("settings", Settings().model_dump())
        if store.get("schedule") is None:
            store.set("schedule", {**Schedule().model_dump(), "next_run_at": None})
        schedule = store.get("schedule")
        if not schedule.get("enabled"):
            return
        parsed = Schedule.model_validate({
            key: schedule.get(key, field.default)
            for key, field in Schedule.model_fields.items()
        })
        now_utc = datetime.now(timezone.utc)
        try:
            next_run = datetime.fromisoformat(schedule.get("next_run_at", ""))
            if next_run.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            next_run = now_utc + timedelta(minutes=parsed.interval_minutes)
            store.event("schedule_recovered", {
                "detail": "Invalid next-run time was replaced without replaying missed work",
            })
        if next_run <= now_utc:
            missed = next_run
            while next_run <= now_utc:
                next_run += timedelta(minutes=parsed.interval_minutes)
            store.event("schedule_missed", {
                "missed_at": missed.isoformat(),
                "detail": "Obsolete missed runs were not replayed after restart",
            })
        store.set("schedule", {**parsed.model_dump(), "next_run_at": next_run.isoformat()})

    async def shutdown(self):
        await asyncio.gather(*(
            runtime.browser_login.shutdown() for runtime in self.runtimes.values()
        ), return_exceptions=True)
        await asyncio.gather(*(
            runtime.service.shutdown() for runtime in self.runtimes.values()
        ), return_exceptions=True)

    def repair_schedulers(self):
        for runtime in self.runtimes.values():
            task = runtime.service.scheduler_task
            if task is not None and task.done():
                runtime.store.event("scheduler_restarted", {
                    "detail": "The manager scheduler stopped unexpectedly and was restarted",
                })
                runtime.service.scheduler_task = asyncio.create_task(runtime.service.scheduler())
