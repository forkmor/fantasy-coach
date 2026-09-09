import asyncio
import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import ActionInput, Policy, Schedule
from app.policy import check_policy
from app.security import InstanceLock, LocalSession, SecretError, Vault, password_hash
from app.storage import ConflictError, Store, now
from app.tools import ToolError, ToolRegistry


def snapshot():
    return {"fetched_at": now(), "league_id": 656212638, "team_id": 4, "season": 2026,
            "scoring_period_id": 1, "roster": [
                {"player_id": 1, "eligible_slots": [0, 20], "slot_id": 0},
                {"player_id": 2, "eligible_slots": [2, 20], "slot_id": 20},
            ]}


def test_policy_protected_and_foreign_players(policy_data):
    policy = Policy(**policy_data)
    denials = check_policy(ActionInput(action="add_drop", reason="test", drop_player_id=1), policy, snapshot())
    assert any("protected" in text for text in denials)
    denials = check_policy(ActionInput(action="add_drop", reason="test", drop_player_id=999), policy, snapshot())
    assert any("not on" in text for text in denials)


def test_policy_bid_trade_and_eligibility(policy_data):
    policy = Policy(**policy_data)
    assert check_policy(ActionInput(action="waiver_submit", reason="test", add_player_id=3, bid=11),
                        policy, snapshot())
    assert check_policy(ActionInput(action="set_lineup", reason="test",
                                    moves=[{"player_id": 2, "slot_id": 0}]), policy, snapshot())
    action = ActionInput(action="trade_propose", reason="test", other_team_id=3,
                         outgoing_player_ids=[2], incoming_player_ids=[3])
    assert check_policy(action, policy, snapshot()) == []
    policy.trade_values["3"] = 1
    assert any("value-loss" in item for item in check_policy(action, policy, snapshot()))


def test_unknown_and_stale_scope_blocks(policy_data):
    action = ActionInput(action="add_drop", reason="test", drop_player_id=2)
    state = snapshot()
    state["fetched_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state["team_id"] = 3
    assert len(check_policy(action, Policy(**policy_data), state)) == 2


@pytest.mark.parametrize("payload", [
    {"action": "add_drop", "reason": "test"},
    {"action": "trade_accept", "reason": "test", "transaction_id": "a", "outgoing_player_ids": [2]},
    {"action": "waiver_submit", "reason": "test", "add_player_id": 3},
    {"action": "set_lineup", "reason": "test", "moves": [{"player_id": 2, "slot_id": 2}] * 2},
    {"action": "add_drop", "reason": "test", "add_player_id": True},
])
def test_action_schema_rejects_malformed(payload):
    with pytest.raises(ValidationError):
        ActionInput.model_validate(payload)


def test_policy_requires_explicit_consistent_limits(policy_data):
    del policy_data["max_bid"]
    with pytest.raises(ValidationError):
        Policy(**policy_data)
    with pytest.raises(ValidationError):
        Schedule(timezone="Not/AZone")


def test_defense_player_ids_are_not_rejected(policy_data):
    policy_data["protected_player_ids"] = [-16001]
    policy_data["trade_values"]["-16001"] = 10.0
    policy = Policy(**policy_data)
    state = snapshot()
    state["roster"].append({"player_id": -16001, "eligible_slots": [16, 20], "slot_id": 20})
    lineup = ActionInput(action="set_lineup", reason="Start defense",
                         moves=[{"player_id": -16001, "slot_id": 16}])
    assert check_policy(lineup, policy, state) == []
    drop = ActionInput(action="add_drop", reason="Drop defense", drop_player_id=-16001)
    assert any("protected" in item for item in check_policy(drop, policy, state))
    assert ActionInput(action="waiver_submit", reason="Claim defense", add_player_id=-16002, bid=0)


@pytest.mark.parametrize("payload", [
    {"action": "add_drop", "reason": "test", "add_player_id": 0},
    {"action": "set_lineup", "reason": "test", "moves": [{"player_id": 0, "slot_id": 16}]},
    {"action": "trade_propose", "reason": "test", "other_team_id": 2,
     "outgoing_player_ids": [0], "incoming_player_ids": [3]},
])
def test_zero_player_ids_are_invalid(payload):
    with pytest.raises(ValidationError):
        ActionInput(**payload)


def test_store_persistence_overlap_and_recovery(tmp_path, policy_data):
    path = tmp_path / "db.sqlite"
    store = Store(path)
    store.set_policy(policy_data)
    config = {"provider": "grok", "model": "test", "mode": "dry_run", "paused": False}
    store.set("settings", config)
    run = store.create_run(config, "test", 2)
    with pytest.raises(ConflictError, match="active"):
        store.create_run(config, "test", 2)
    intent = store.add_action(run["id"], {"action": "add_drop"}, "submitting", {}, 1, "test")
    reopened = Store(path)
    assert reopened.get("policy") == policy_data
    reopened.recover()
    assert reopened.run(run["id"])["status"] == "interrupted"
    assert reopened.action(intent["id"])["status"] == "unknown"
    assert reopened.active_run() is None
    reopened.create_run(config, "second", 2)
    with pytest.raises(ConflictError, match="Daily"):
        reopened.create_run(config, "third", 2)


def test_os_lock_prevents_competing_backend(tmp_path):
    first, second = InstanceLock(tmp_path / "lock"), InstanceLock(tmp_path / "lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


@pytest.mark.skipif(os.name != "nt", reason="Windows secure storage")
def test_dpapi_encryption_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    vault = Vault(store)
    secret = "test-key-not-a-real-credential"
    vault.put("grok", secret)
    assert secret.encode() not in store.secret("grok")
    assert Vault(Store(store.path)).get("grok") == secret
    assert secret.encode() not in store.path.read_bytes()


def test_portable_vault_and_password_authentication(tmp_path, monkeypatch):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv("HARNESS_VAULT_KEY", key)
    store = Store(tmp_path / "db.sqlite")
    vault = Vault(store)
    vault.put("grok", "portable-test-secret")
    assert store.secret("grok").startswith(b"fieldhouse-v1:")
    assert b"portable-test-secret" not in store.path.read_bytes()
    assert Vault(Store(store.path)).get("grok") == "portable-test-secret"

    monkeypatch.setenv(
        "HARNESS_VAULT_KEY",
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
    )
    with pytest.raises(SecretError, match="could not be decrypted"):
        Vault(Store(store.path)).get("grok")

    digest = password_hash("a-long-owner-password")
    session = LocalSession(password_digest=digest)
    assert session.valid_login("a-long-owner-password")
    assert not session.valid_login("a-different-password")
    assert "a-long-owner-password" not in digest

    sessions = tmp_path / "sessions.sqlite3"
    persistent = LocalSession(password_digest=digest, database=sessions)
    record = persistent.create()
    persistent.bind_team(record.cookie, 7)
    restored = LocalSession(password_digest=digest, database=sessions).get(record.cookie)
    assert restored is not None and restored.team_id == 7
    assert record.cookie.encode() not in sessions.read_bytes()
    persistent.revoke(record.cookie)
    assert persistent.get(record.cookie) is None


def test_dry_run_never_submits(tmp_path, policy_data):
    class FakeEspn:
        async def snapshot(self):
            return snapshot()

    store = Store(tmp_path / "db.sqlite")
    store.set_policy(policy_data)
    settings = {"provider": "grok", "model": "test", "mode": "dry_run", "paused": False}
    store.set("settings", settings)
    run = store.create_run(settings, "test", 5)
    store.update_run(run["id"], status="running")
    tools = ToolRegistry(store, FakeEspn, run["id"])

    async def exercise():
        action = {"action": "add_drop", "reason": "test", "drop_player_id": 2}
        intent = await tools.call("plan_action", action)
        assert intent["status"] == "dry_run"
        assert intent["result"]["submitted"] is False
        assert intent["result"]["unresolved_checks"]
        duplicate = await tools.call("plan_action", action)
        assert duplicate["id"] == intent["id"]
        with pytest.raises(ToolError, match="proposal-only"):
            await tools.call("execute_action", {"intent_id": intent["id"]})
        with pytest.raises(ToolError, match="Unknown"):
            await tools.call("http_request", {"url": "https://example.com"})
        store.set("settings", {**settings, "paused": True})
        with pytest.raises(ToolError, match="paused"):
            await tools.call("get_team", {})

    asyncio.run(exercise())
    assert len(store.actions()) == 1
    assert all(not action["result"]["submitted"] for action in store.actions())


def test_live_agent_executes_only_verified_lineup_swap(tmp_path, policy_data):
    class FakeEspn:
        executed = []

        async def snapshot(self):
            return {"fetched_at": now(), "league_id": 656212638, "team_id": 4, "season": 2026,
                    "scoring_period_id": 1, "roster": [
                        {"player_id": 1, "eligible_slots": [0, 20], "slot_id": 0},
                        {"player_id": 2, "eligible_slots": [0, 20], "slot_id": 20},
                    ]}

        async def preview_lineup_moves(self, moves):
            self.executed.append(("preview", moves))
            return {"validated": True}

        async def execute_lineup_swap(self, preview):
            self.executed.append(("execute", preview))
            return {"status": "completed", "verified_at": now()}

    store = Store(tmp_path / "db.sqlite")
    policy_data["allowed_actions"] = ["set_lineup", "trade_propose"]
    store.set_policy(policy_data)
    settings = {"provider": "grok", "model": "test", "mode": "live", "paused": False}
    store.set("settings", settings)
    run = store.create_run(settings, "test", 5)
    store.update_run(run["id"], status="running")
    espn = FakeEspn()
    tools = ToolRegistry(store, lambda: espn, run["id"])

    async def exercise():
        intent = await tools.call("plan_action", {
            "action": "set_lineup", "reason": "Start the preferred quarterback",
            "moves": [{"player_id": 1, "slot_id": 20}, {"player_id": 2, "slot_id": 0}],
        })
        completed = await tools.call("execute_action", {"intent_id": intent["id"]})
        assert completed["status"] == "completed"
        assert completed["result"]["submitted"] is True
        trade = await tools.call("plan_action", {
            "action": "trade_propose", "reason": "test", "other_team_id": 3,
            "outgoing_player_ids": [2], "incoming_player_ids": [3],
        })
        with pytest.raises(ToolError, match="not verified"):
            await tools.call("execute_action", {"intent_id": trade["id"]})

    asyncio.run(exercise())
    assert espn.executed == [
        ("preview", [{"player_id": 1, "slot_id": 20}, {"player_id": 2, "slot_id": 0}]),
        ("execute", {"validated": True}),
    ]
