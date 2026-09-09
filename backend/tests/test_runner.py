import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.runner as runner_module
from app.runner import RunService
from app.storage import ConflictError, Store, now


class FakeVault:
    def get(self, name):
        return "synthetic-key"


class FakeEspn:
    async def snapshot(self):
        return {"fetched_at": now(), "team_id": 4, "league_id": 656212638, "season": 2026,
                "roster": [], "scoring_period_id": 1}


def configured_store(tmp_path, policy_data, provider="grok", deep_research=False):
    store = Store(tmp_path / "db.sqlite")
    store.set_policy(policy_data)
    store.set("settings", {
        "provider": provider, "model": "test", "mode": "dry_run",
        "deep_research": deep_research, "paused": False,
    })
    for credential in (provider, "espn_s2", "swid"):
        store.put_secret(credential, b"synthetic-encrypted")
    return store


@pytest.mark.parametrize("provider", ["grok", "gemini"])
def test_bounded_agent_tool_loop(tmp_path, policy_data, monkeypatch, provider):
    class FakeProvider:
        def __init__(self, *args):
            self.calls = 0

        async def complete(self, messages, tools, max_output_tokens):
            self.calls += 1
            assert max_output_tokens == 1024
            assert "synthetic-key" not in str(messages)
            if self.calls == 1:
                call = {"id": "call-1", "name": "get_policy", "arguments": {}}
                return {"message": {"role": "assistant", "content": None, "tool_calls": []},
                        "text": "", "tool_calls": [call], "usage": {"output_tokens": 10}}
            assert messages[-1]["role"] == "tool"
            return {"message": {"role": "assistant", "content": "No live changes made."},
                    "text": "No live changes made.", "tool_calls": [], "usage": {"output_tokens": 6}}

    monkeypatch.setattr(runner_module, "ProviderClient", FakeProvider)
    store = configured_store(tmp_path, policy_data, provider)
    service = RunService(store, FakeVault())
    service.espn = FakeEspn

    async def exercise():
        run = service.start("Review team")
        await service.tasks[run["id"]]
        result = store.run(run["id"])
        assert result["status"] == "completed"
        assert result["summary"] == "No live changes made."
        assert result["usage"]["requests"] == 2
        assert result["usage"]["tool_calls"] == 1
        assert len(result["events"]) == 2
        assert store.actions() == []

    asyncio.run(exercise())


def test_cancel_before_task_starts(tmp_path, policy_data):
    store = configured_store(tmp_path, policy_data)
    service = RunService(store, FakeVault())
    service.espn = FakeEspn

    async def exercise():
        run = service.start("Review")
        service.cancel(run["id"])
        await service.shutdown()
        assert store.run(run["id"])["status"] == "cancelled"
        assert store.active_run() is None

    asyncio.run(exercise())


def test_deep_research_is_collected_and_sent_to_coach(tmp_path, policy_data, monkeypatch):
    class FakeResearch:
        async def research_roster(self, snapshot):
            assert snapshot["team_id"] == 4
            return {"players": [{"player_id": 101, "name": "Starter",
                                 "news": [{"headline": "Practiced in full"}]}]}

    class FakeProvider:
        def __init__(self, *args):
            pass

        async def complete(self, messages, tools, max_output_tokens):
            payload = messages[1]["content"]
            assert "Practiced in full" in payload
            assert '"deep_research":{"players"' in payload
            return {"message": {"role": "assistant", "content": "Start Starter."},
                    "text": "Start Starter.", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(runner_module, "ProviderClient", FakeProvider)
    store = configured_store(tmp_path, policy_data, deep_research=True)
    service = RunService(store, FakeVault(), player_data=FakeResearch())
    service.espn = FakeEspn

    async def exercise():
        run = service.start("Set my best lineup")
        await service.tasks[run["id"]]
        result = store.run(run["id"])
        assert result["status"] == "completed"
        event = next(event for event in result["events"]
                     if event["kind"] == "deep_research_completed")
        assert event["data"] == {"players_checked": 1, "complete": True}

    asyncio.run(exercise())


def test_unresolved_live_action_blocks_new_runs(tmp_path, policy_data):
    store = configured_store(tmp_path, policy_data)
    store.add_action(
        "owner", {"action": "set_lineup"}, "unknown",
        {"submitted": True}, 1, "unknown-live-action",
    )
    service = RunService(store, FakeVault())
    with pytest.raises(ConflictError, match="unresolved outcome"):
        service.ready()


def test_provider_errors_do_not_become_success(tmp_path, policy_data, monkeypatch):
    class FakeProvider:
        def __init__(self, *args):
            pass

        async def complete(self, *args):
            raise runner_module.ProviderError("Provider authentication failed")

    monkeypatch.setattr(runner_module, "ProviderClient", FakeProvider)
    store = configured_store(tmp_path, policy_data)
    service = RunService(store, FakeVault())
    service.espn = FakeEspn

    async def exercise():
        run = service.start("Review")
        await service.tasks[run["id"]]
        assert store.run(run["id"])["status"] == "failed"
        assert "authentication" in store.run(run["id"])["error"]

    asyncio.run(exercise())


def test_scheduler_records_skip_without_overlapping(tmp_path, policy_data, monkeypatch):
    store = configured_store(tmp_path, policy_data)
    schedule = {"enabled": True, "timezone": "America/Chicago", "interval_minutes": 30,
                "next_run_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()}
    store.set("schedule", schedule)
    store.create_run(store.get("settings"), "Existing run", 5)
    service = RunService(store, FakeVault())
    ticks = 0

    async def sleep_once(seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(runner_module.asyncio, "sleep", sleep_once)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await service.scheduler()

    asyncio.run(exercise())
    assert len(store.runs()) == 1
    assert datetime.fromisoformat(store.get("schedule")["next_run_at"]) > datetime.now(timezone.utc)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='schedule_skipped'").fetchone()[0] == 1


def test_scheduler_survives_malformed_persisted_state(tmp_path, policy_data, monkeypatch):
    store = configured_store(tmp_path, policy_data)
    store.set("schedule", {"enabled": True, "next_run_at": "not-a-date"})
    service = RunService(store, FakeVault())
    ticks = 0

    async def sleep_once(seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(runner_module.asyncio, "sleep", sleep_once)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.scheduler())
    with store.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='schedule_error'"
        ).fetchone()[0] == 1
