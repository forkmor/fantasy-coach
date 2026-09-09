import asyncio
from copy import deepcopy
from datetime import datetime
import json

import httpx
import pytest

from app.espn import CAPABILITIES, EspnClient, EspnError, EspnUnknownOutcome


SWID = "{01234567-89AB-CDEF-0123-456789ABCDEF}"
OTHER = "{11111111-2222-3333-4444-555555555555}"


def player(identifier=101):
    return {"playerPoolEntry": {"status": "ONTEAM", "onTeamId": 4, "lineupLocked": False,
        "rosterLocked": False, "player": {
        "id": identifier, "fullName": "Football Player", "defaultPositionId": 2,
        "eligibleSlots": [2, 20, 23], "proTeamId": 1, "droppable": True,
        "stats": [{"seasonId": 2026, "scoringPeriodId": 1, "statSplitTypeId": 1,
                   "statSourceId": 1, "appliedTotal": 12.5}],
        "manager": "Private Manager", "ownerId": OTHER,
    }}, "lineupSlotId": 2}


def league():
    return {
        "id": 656212638, "seasonId": 2026, "scoringPeriodId": 1,
        "members": [{"id": SWID, "displayName": "Private Manager", "email": "private@example.test"}],
        "teams": [{"id": 4, "name": "Test Football", "owners": [SWID],
                   "roster": {"entries": [player()]},
                   "transactionCounter": {"acquisitionBudgetSpent": 20},
                   "record": {"overall": {"wins": 0, "losses": 0}},
                   "primaryOwner": SWID, "email": "private@example.test"}],
        "settings": {
            "name": "Private league", "owner": SWID,
            "acquisitionSettings": {"acquisitionBudget": 100, "isUsingAcquisitionBudget": True,
                                     "waiverProcessDays": [2, 3], "manager": "Private Manager"},
            "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "20": 6},
                               "lineupLocktimeType": "INDIVIDUAL_GAME"},
            "scoringSettings": {"scoringType": "H2H_POINTS",
                                "scoringItems": [{"statId": 53, "points": 1,
                                                  "pointsOverrides": {"16": 0},
                                                  "email": "private@example.test"}]},
            "tradeSettings": {"deadlineDate": 1790000000000},
        },
    }


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def install(responses):
        queue = iter(responses)

        def handler(request):
            requests.append(request)
            response = next(queue)
            if isinstance(response, Exception):
                raise response
            return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

        def factory(**kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            return original(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return requests
    return install


def client():
    return EspnClient("PRIVATE_S2", SWID)


def assert_private_free(result):
    encoded = json.dumps(result)
    for private in (SWID, OTHER, "PRIVATE_S2", "Private Manager", "private@example.test", "primaryOwner"):
        assert private not in encoded


def test_snapshot_schema_privacy_and_unknown_fields(transport):
    requests = transport([league()])
    snapshot = asyncio.run(client().snapshot())
    assert snapshot["team"]["id"] == 4
    assert snapshot["ownership_verified"] is True
    assert datetime.fromisoformat(snapshot["fetched_at"]).utcoffset().total_seconds() == 0
    assert snapshot["budget"] == {"total": 100, "spent": 20, "remaining": 80}
    roster = snapshot["roster"][0]
    assert roster["player_id"] == 101
    assert roster["position"] == "RB"
    assert roster["eligible_slots"] == [2, 20, 23]
    assert roster["projected_points"] == 12.5
    assert roster["actual_points"] is None
    assert roster["injury_status"] == "UNKNOWN"
    assert roster["locked"] is False
    assert roster["roster_locked"] is False
    assert roster["lock_status"] == "UNLOCKED"
    assert snapshot["settings"]["scoringSettings"]["scoringItems"][0]["pointsOverrides"] == {"16": 0}
    assert snapshot["settings"]["scheduleSettings"] is None
    assert snapshot["settings"]["acquisitionSettings"]["waiverProcessDays"] == [2, 3]
    assert_private_free(snapshot)
    request = requests[0]
    assert str(request.url).startswith(
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/656212638?"
    )
    assert request.method == "GET"
    assert request.url.params.get_list("view") == ["mTeam", "mRoster", "mSettings"]
    assert "espn_s2=PRIVATE_S2" in request.headers["cookie"]
    assert f"SWID={SWID}" in request.headers["cookie"]


def test_discovers_the_single_team_owned_by_the_connected_swid(transport):
    data = league()
    data["teams"].insert(0, {"id": 2, "name": "Another Team", "owners": [OTHER]})
    transport([data])
    result = asyncio.run(EspnClient("PRIVATE_S2", SWID, team_id=1).discover_owned_team())
    assert result == {"team_id": 4, "team_name": "Test Football"}


def test_missing_budget_and_projection_not_invented(transport):
    data = league()
    data["settings"] = {}
    data["teams"][0]["transactionCounter"] = {}
    del data["teams"][0]["roster"]["entries"][0]["playerPoolEntry"]["player"]["stats"]
    transport([data])
    snapshot = asyncio.run(client().snapshot())
    assert snapshot["budget"] == {"total": None, "spent": None, "remaining": None}
    assert snapshot["roster"][0]["projected_points"] is None


@pytest.mark.parametrize("change", ["wrong_owner", "missing_owner", "missing_team", "missing_teams",
                                   "missing_period", "wrong_league", "wrong_season", "missing_roster"])
def test_unverified_context_fails_closed(transport, change):
    data = league()
    if change == "wrong_owner":
        data["teams"][0]["owners"] = [OTHER]
    elif change == "missing_owner":
        del data["teams"][0]["owners"]
    elif change == "missing_team":
        data["teams"][0]["id"] = 3
    elif change == "missing_teams":
        del data["teams"]
    elif change == "missing_period":
        del data["scoringPeriodId"]
    elif change == "wrong_league":
        data["id"] = 1
    elif change == "wrong_season":
        data["seasonId"] = 2025
    else:
        del data["teams"][0]["roster"]
    transport([data])
    with pytest.raises(EspnError) as caught:
        asyncio.run(client().snapshot())
    assert_private_free(str(caught.value))


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
def test_http_errors_safe_no_redirect(transport, status):
    requests = transport([httpx.Response(status, text="PRIVATE_S2 private@example.test",
                                         headers={"Location": "https://evil.test"})])
    with pytest.raises(EspnError, match=f"HTTP {status}") as caught:
        asyncio.run(client().snapshot())
    assert_private_free(str(caught.value))
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("PRIVATE_S2"), httpx.ConnectError("PRIVATE_S2"),
    httpx.Response(200, text="<html>PRIVATE_S2</html>"), httpx.Response(200, json=[]),
])
def test_invalid_json_and_network_safe(transport, failure):
    transport([failure])
    with pytest.raises(EspnError) as caught:
        asyncio.run(client().snapshot())
    assert_private_free(str(caught.value))


def test_available_players_filters_and_bounds(transport):
    available = deepcopy(player()["playerPoolEntry"])
    available["status"], available["onTeamId"] = "FREEAGENT", 0
    requests = transport([league(), {"players": [available, available, available]}])
    result = asyncio.run(client().available_players("rb", 2))
    assert len(result["players"]) == 2
    assert result["players"][0]["availability_status"] == "FREEAGENT"
    assert result["players"][0]["slot_id"] is None
    assert result["complete_pool"] is False
    filters = json.loads(requests[1].headers["x-fantasy-filter"])["players"]
    assert filters["filterStatus"]["value"] == ["FREEAGENT", "WAIVERS"]
    assert filters["filterSlotIds"]["value"] == [2]
    assert filters["limit"] == 2
    assert requests[1].url.params["view"] == "kona_player_info"
    assert requests[1].url.params["scoringPeriodId"] == "1"
    assert_private_free(result)


def test_league_overview_normalizes_standings_and_matchups_without_members(transport):
    data = league()
    data["status"] = {"currentMatchupPeriod": 1, "finalScoringPeriod": 17, "isActive": True}
    data["teams"].append({
        "id": 5, "name": "Other Team", "owners": [OTHER], "playoffSeed": 1, "waiverRank": 2,
        "record": {"overall": {"wins": 1, "losses": 0, "ties": 0,
                                "pointsFor": 120.5, "pointsAgainst": 99.25}},
        "transactionCounter": {"acquisitions": 2, "drops": 1, "trades": 0},
        "roster": {"entries": []},
    })
    data["teams"][0]["playoffSeed"] = 2
    data["schedule"] = [{
        "id": 1, "matchupPeriodId": 1, "winner": "UNDECIDED",
        "home": {"teamId": 4, "totalPoints": 10.5, "totalProjectedPoints": 100.25},
        "away": {"teamId": 5, "totalPoints": 9.5, "totalProjectedPoints": 98.0},
    }]
    requests = transport([data])
    result = asyncio.run(client().league_overview())
    assert result["standings"][0]["name"] == "Other Team"
    assert result["standings"][1]["is_my_team"] is True
    assert result["matchups"][0]["home"]["team_id"] == 4
    assert result["league"] == {
        "team_count": 2, "current_matchup_period": 1,
        "final_scoring_period": 17, "is_active": True,
    }
    assert requests[0].url.params.get_list("view") == [
        "mTeam", "mRoster", "mSettings", "mMatchup", "mStatus",
    ]
    assert_private_free(result)


def test_lineup_swap_preview_and_execute_use_atomic_write_and_readback(transport):
    before = league()
    replacement = player(202)
    replacement["playerPoolEntry"]["player"]["fullName"] = "Bench Player"
    replacement["lineupSlotId"] = 20
    before["teams"][0]["roster"]["entries"].append(replacement)
    after = deepcopy(before)
    after["teams"][0]["roster"]["entries"][0]["lineupSlotId"] = 20
    after["teams"][0]["roster"]["entries"][1]["lineupSlotId"] = 2
    requests = transport([before, before, httpx.Response(200, json={"status": "EXECUTED"}), after])
    espn = client()
    preview = asyncio.run(espn.preview_lineup_swap(101, 202))
    result = asyncio.run(espn.execute_lineup_swap(preview))
    assert result["status"] == "completed"
    post = requests[2]
    assert str(post.url) == (
        "https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/"
        "seasons/2026/segments/0/leagues/656212638/transactions"
    )
    payload = json.loads(post.content)
    assert payload["type"] == "ROSTER"
    assert payload["executionType"] == "EXECUTE"
    assert payload["memberId"] == SWID
    assert payload["items"] == [
        {"playerId": 202, "type": "LINEUP", "fromLineupSlotId": 20, "toLineupSlotId": 2},
        {"playerId": 101, "type": "LINEUP", "fromLineupSlotId": 2, "toLineupSlotId": 20},
    ]
    assert all("fromTeamId" not in item and "toTeamId" not in item for item in payload["items"])


def test_lineup_readback_error_after_post_is_unknown_not_failed(transport):
    before = league()
    replacement = player(202)
    replacement["playerPoolEntry"]["player"]["fullName"] = "Bench Player"
    replacement["lineupSlotId"] = 20
    before["teams"][0]["roster"]["entries"].append(replacement)
    transport([
        before, before, httpx.Response(200, json={"status": "EXECUTED"}),
        httpx.ConnectError("readback failed"),
    ])
    espn = client()
    preview = asyncio.run(espn.preview_lineup_swap(101, 202))
    with pytest.raises(EspnUnknownOutcome, match="readback failed"):
        asyncio.run(espn.execute_lineup_swap(preview))


def test_verified_drop_request_contract_and_readback(transport):
    before = league()
    after = league()
    after["teams"][0]["roster"]["entries"] = []
    requests = transport([
        before,
        before,
        httpx.Response(200, json={"status": "EXECUTED"}),
        after,
    ])
    espn = client()
    preview = asyncio.run(espn.preview_add_drop(drop_player_id=101))
    result = asyncio.run(espn.execute_add_drop(preview))
    assert result["status"] == "completed"
    assert result["operation"] == "drop"
    request = requests[2]
    assert request.method == "POST"
    assert str(request.url).endswith(
        "/seasons/2026/segments/0/leagues/656212638/transactions"
    )
    assert json.loads(request.content) == {
        "isLeagueManager": False,
        "teamId": 4,
        "type": "ROSTER",
        "memberId": SWID,
        "scoringPeriodId": 1,
        "executionType": "EXECUTE",
        "items": [{"playerId": 101, "type": "DROP", "fromTeamId": 4}],
    }


def test_verified_free_agent_add_request_contract_and_readback(transport):
    before = league()
    before["teams"][0]["roster"]["entries"] = []
    available = deepcopy(player()["playerPoolEntry"])
    available["status"], available["onTeamId"] = "FREEAGENT", 0
    after = league()
    requests = transport([
        before, before, {"players": [available]},
        before, before, {"players": [available]},
        httpx.Response(200, json={"status": "EXECUTED"}), after,
    ])
    espn = client()
    preview = asyncio.run(espn.preview_add_drop(add_player_id=101))
    result = asyncio.run(espn.execute_add_drop(preview))
    assert result["status"] == "completed"
    assert result["operation"] == "add"
    request = requests[6]
    assert json.loads(request.content) == {
        "isLeagueManager": False,
        "teamId": 4,
        "type": "FREEAGENT",
        "scoringPeriodId": 1,
        "executionType": "EXECUTE",
        "items": [{"playerId": 101, "type": "ADD", "toTeamId": 4}],
    }
    assert "memberId" not in json.loads(request.content)


def test_add_drop_preview_rejects_unverified_shapes_and_players(transport):
    data = league()
    data["teams"][0]["roster"]["entries"][0]["playerPoolEntry"]["player"]["droppable"] = False
    requests = transport([data])
    with pytest.raises(EspnError, match="does not confirm"):
        asyncio.run(client().preview_add_drop(drop_player_id=101))
    assert len(requests) == 1
    for kwargs in ({}, {"add_player_id": 101, "drop_player_id": 202},
                   {"add_player_id": True}):
        with pytest.raises(EspnError):
            asyncio.run(client().preview_add_drop(**kwargs))


@pytest.mark.parametrize("change,error", [
    ("locked", "locked"),
    ("ineligible", "not eligible"),
    ("not_bench", "no longer on the bench"),
])
def test_lineup_swap_fails_closed_before_write(transport, change, error):
    data = league()
    replacement = player(202)
    replacement["playerPoolEntry"]["player"]["fullName"] = "Bench Player"
    replacement["lineupSlotId"] = 20
    data["teams"][0]["roster"]["entries"].append(replacement)
    if change == "locked":
        replacement["playerPoolEntry"]["lineupLocked"] = True
    elif change == "ineligible":
        replacement["playerPoolEntry"]["player"]["eligibleSlots"] = [20]
    else:
        replacement["lineupSlotId"] = 23
    requests = transport([data])
    with pytest.raises(EspnError, match=error):
        asyncio.run(client().preview_lineup_swap(101, 202))
    assert len(requests) == 1


@pytest.mark.parametrize("position,limit", [("BAD", 25), (None, 0), (None, 51), (None, True), ([], 2)])
def test_player_search_invalid_input_no_network(transport, position, limit):
    requests = transport([])
    with pytest.raises(EspnError):
        asyncio.run(client().available_players(position, limit))
    assert not requests


def test_transactions_scoped_sanitized_and_honestly_incomplete(transport):
    transaction = {
        "teamId": 4, "id": "internal-id", "type": "WAIVER", "status": "EXECUTED",
        "memberId": SWID, "manager": "Private Manager", "email": "private@example.test",
        "scoringPeriodId": 1, "processDate": 1780000000000, "bidAmount": 5,
        "items": [{"type": "ADD", "playerId": 101, "ownerId": OTHER}],
    }
    other = {**transaction, "teamId": 3}
    requests = transport([league(), {"transactions": [other, transaction]}])
    result = asyncio.run(client().transactions())
    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["items"] == [{"type": "ADD", "playerId": 101}]
    assert result["pending_trades"] is None
    assert result["pending_waivers"] is None
    assert result["complete"] is False
    assert result["available"] is True
    assert requests[1].url.params["view"] == "mTransactions2"
    assert_private_free(result)


def test_missing_transaction_view_is_not_empty_history(transport):
    transport([league(), {}])
    result = asyncio.run(client().transactions())
    assert result["available"] is False
    assert result["complete"] is False
    assert result["pending_waivers"] is None


def test_connection_verifies_ownership(transport):
    transport([league()])
    assert "ownership verified" in asyncio.run(client().test_connection())


def test_scope_and_credentials_validation():
    for kwargs in ({"league_id": 1}, {"team_id": 0}, {"team_id": -1},
                   {"season": 2025}, {"team_id": 4.0}):
        with pytest.raises(EspnError):
            EspnClient("cookie", SWID, **kwargs)
    assert EspnClient("cookie", SWID, team_id=3).team_id == 3
    for s2, swid in (("", SWID), ("cookie\n", SWID), ("cookie; injected=x", SWID),
                     ("cookie", ""), ("cookie", SWID[:-1]), ("cookie", "https://evil.test")):
        with pytest.raises(EspnError):
            EspnClient(s2, swid)


def test_capabilities_enable_only_verified_executors():
    assert len(CAPABILITIES) == 8
    instance = client()
    enabled = [capability["action"] for capability in CAPABILITIES if capability["enabled"]]
    assert enabled == ["set_lineup", "add_drop"]
    assert all(capability["reason"] for capability in CAPABILITIES)
    assert hasattr(instance, "execute_lineup_moves")
    assert hasattr(instance, "execute_add_drop")
    assert all(not hasattr(instance, action) for action in (
        "waiver_submit", "waiver_cancel",
        "trade_propose", "trade_accept", "trade_reject", "trade_cancel",
    ))


def test_negative_defense_player_ids_survive_all_read_normalization(transport):
    # ESPN fixture evidence: cwendt94/espn-api commit cec2935d9d94a3ab88dd6eff0cf5a4fdc0e80d2f,
    # tests/football/unit/data/league_players_2018.json: Falcons D/ST has ID -16001.
    entry = player(-16001)
    entry["lineupSlotId"] = 16
    entry["playerPoolEntry"]["player"].update({
        "fullName": "Falcons D/ST", "defaultPositionId": 16,
        "eligibleSlots": [16, 20, 21], "proTeamId": 1,
    })
    data = league()
    data["teams"][0]["roster"]["entries"] = [entry]
    available = deepcopy(entry["playerPoolEntry"])
    available.update({"status": "WAIVERS", "onTeamId": 0})
    transport([
        data, data, {"players": [available]}, data,
        {"transactions": [{"teamId": 4, "type": "WAIVER",
                           "items": [{"type": "ADD", "playerId": -16001}]}]},
    ])

    async def scenario():
        instance = client()
        snapshot = await instance.snapshot()
        defense = snapshot["roster"][0]
        assert defense["player_id"] == -16001
        assert defense["position"] == "D/ST"
        assert defense["eligible_slots"] == [16, 20, 21]
        assert defense["slot_id"] == 16
        available = await instance.available_players("D/ST")
        assert available["players"][0]["player_id"] == -16001
        transactions = await instance.transactions()
        assert transactions["transactions"][0]["items"][0]["playerId"] == -16001

    asyncio.run(scenario())
