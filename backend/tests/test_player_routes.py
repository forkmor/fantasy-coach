from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import now
from test_api import FakeVault, login


def test_player_routes_require_session_and_loaded_roster(tmp_path):
    snapshot = {"fetched_at": now(), "team_id": 4, "league_id": 656212638, "season": 2026,
                "roster": [{"player_id": 4038941, "name": "Justin Herbert", "projected_points": 19.25}],
                "scoring_period_id": 1}
    enriched = {**snapshot, "roster": [{**snapshot["roster"][0], "jersey": "10",
                 "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/4038941.png"}]}
    data = SimpleNamespace(enrich_snapshot=AsyncMock(return_value=enriched),
                           player_detail=AsyncMock(return_value={"player_id": 4038941, "jersey": "10"}),
                           aclose=AsyncMock())
    app = create_app(tmp_path, "local-test", FakeVault, lambda: data)
    app.state.service.espn = lambda: SimpleNamespace(snapshot=AsyncMock(return_value=snapshot))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/players/4038941").status_code == 401
        login(client)
        assert client.get("/api/players/4038941").status_code == 404
        roster = client.get("/api/team")
        assert roster.status_code == 200
        assert roster.json()["roster"][0]["jersey"] == "10"
        assert roster.json()["roster"][0]["projected_points"] == 19.25
        detail = client.get("/api/players/4038941")
        assert detail.json() == {"player_id": 4038941, "jersey": "10"}
        assert client.get("/api/players/99999").status_code == 404
        data.player_detail.assert_awaited_once_with(4038941)
    data.aclose.assert_awaited_once()


def test_league_and_owner_confirmed_lineup_routes(tmp_path, policy_data):
    preview = {
        "scoring_period_id": 1,
        "starter": {"player_id": 101, "name": "Starter", "from_slot_id": 2, "to_slot_id": 20},
        "bench": {"player_id": 202, "name": "Bench", "from_slot_id": 20, "to_slot_id": 2},
    }
    espn = SimpleNamespace(
        league_overview=AsyncMock(return_value={"standings": [{"team_id": 4, "name": "Mine"}]}),
        preview_lineup_swap=AsyncMock(return_value=preview),
        execute_lineup_swap=AsyncMock(return_value={"status": "completed", "verified_at": now()}),
    )
    data = SimpleNamespace(aclose=AsyncMock())
    app = create_app(tmp_path, "local-test", FakeVault, lambda: data)
    app.state.service.espn = lambda: espn
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = login(client)
        assert client.get("/api/league").json()["standings"][0]["name"] == "Mine"
        blocked = client.post("/api/lineup/swap/preview", json={
            "starter_player_id": 101, "bench_player_id": 202,
        }, headers=headers)
        assert blocked.status_code == 409
        client.post("/api/policy", json=policy_data, headers=headers).raise_for_status()
        prepared = client.post("/api/lineup/swap/preview", json={
            "starter_player_id": 101, "bench_player_id": 202,
        }, headers=headers)
        assert prepared.status_code == 200
        operation_id = prepared.json()["operation_id"]
        assert prepared.json()["starter"]["name"] == "Starter"
        confirmed = client.post("/api/lineup/swap/confirm", json={
            "operation_id": operation_id,
        }, headers=headers)
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "completed"
        espn.execute_lineup_swap.assert_awaited_once_with(preview)
        repeated = client.post("/api/lineup/swap/confirm", json={
            "operation_id": operation_id,
        }, headers=headers)
        assert repeated.status_code == 409
        assert app.state.store.action(operation_id)["status"] == "completed"


def test_owner_confirmed_drop_route(tmp_path, policy_data):
    snapshot = {
        "fetched_at": now(), "team_id": 4, "league_id": 656212638, "season": 2026,
        "scoring_period_id": 1,
        "roster": [{"player_id": 101, "name": "Droppable", "eligible_slots": [20]}],
    }
    preview = {
        "scoring_period_id": 1, "operation": "drop",
        "drop": {"player_id": 101, "name": "Droppable"},
    }
    espn = SimpleNamespace(
        snapshot=AsyncMock(return_value=snapshot),
        transactions=AsyncMock(return_value={
            "available": True, "history_truncated": False, "transactions": [],
        }),
        preview_add_drop=AsyncMock(return_value=preview),
        execute_add_drop=AsyncMock(return_value={
            "status": "completed", "operation": "drop", "player_id": 101,
            "verified_at": now(), "scoring_period_id": 1,
        }),
    )
    data = SimpleNamespace(aclose=AsyncMock())
    app = create_app(tmp_path, "local-test", FakeVault, lambda: data)
    app.state.service.espn = lambda: espn
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = login(client)
        client.post("/api/policy", json=policy_data, headers=headers).raise_for_status()
        prepared = client.post("/api/roster/change/preview", json={
            "drop_player_id": 101,
        }, headers=headers)
        assert prepared.status_code == 200
        operation_id = prepared.json()["operation_id"]
        confirmed = client.post("/api/roster/change/confirm", json={
            "operation_id": operation_id,
        }, headers=headers)
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "completed"
        espn.execute_add_drop.assert_awaited_once_with(preview)
