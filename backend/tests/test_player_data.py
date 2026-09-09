import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app import player_data as module
from app.player_data import PlayerDataClient, PlayerDataError, METADATA_FIELDS


def team(identifier=24):
    return {"id": str(identifier), "displayName": "Los Angeles Chargers",
            "abbreviation": "LAC",
            "logos": [{"href": "https://a.espncdn.com/i/teamlogos/nfl/500/lac.png"}]}


def athlete(identifier=4038941):
    return {"id": str(identifier), "displayName": "Justin Herbert", "jersey": "10",
            "position": {"abbreviation": "QB"},
            "headshot": {"href": f"https://a.espncdn.com/i/headshots/nfl/players/full/{identifier}.png"}}


def catalogue():
    return {"sports": [{"leagues": [{"teams": [{"team": team()}]}]}]}


def public_roster():
    return {"team": {"id": "24"}, "athletes": [{"position": "offense", "items": [athlete()]}]}


def overview():
    return {
        "statistics": {
            "displayName": "2026 Stats", "labels": ["CMP", "ATT"],
            "splits": [{"displayName": "Regular Season", "stats": ["22", "31"]}],
        },
        "news": [{
            "headline": "Herbert practices in full",
            "description": "The quarterback completed Wednesday practice.",
            "published": "2026-09-09T14:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/nfl/story/_/id/123"}},
            "premium": False,
        }],
    }


def injuries():
    return {
        "injuries": [{"injuries": [{
            "status": "Questionable", "date": "2026-09-09T12:00:00Z",
            "shortComment": "Shoulder", "longComment": "Limited in practice.",
            "details": {"practiceStatus": "Limited"},
            "athlete": {"id": "4038941"},
        }]}],
    }


def snapshot():
    return {"roster": [{"player_id": 4038941, "name": "Fantasy name", "position": "QB",
                       "pro_team_id": 24, "slot_id": 0, "eligible_slots": [0, 7, 20],
                       "injury_status": "QUESTIONABLE", "projected_points": 19.3,
                       "actual_points": None}], "scoring_period_id": 1, "fetched_at": "fantasy-time"}


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def install(handler=None):
        async def respond(request):
            requests.append(request)
            assert request.method == "GET"
            assert "cookie" not in request.headers
            assert "authorization" not in request.headers
            if handler:
                value = handler(request)
                if asyncio.iscoroutine(value):
                    value = await value
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
            if request.url.path.endswith("/roster"):
                data = public_roster()
            elif request.url.path.endswith("/overview"):
                data = overview()
            elif request.url.path.endswith("/injuries"):
                data = injuries()
            elif "/athletes/" in request.url.path:
                data = {"athlete": {**athlete(), "team": team()}}
            else:
                data = catalogue()
            return httpx.Response(200, json=data)

        def factory(**kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            assert kwargs["timeout"].read <= 12
            return original(transport=httpx.MockTransport(respond), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return requests
    return install


def test_enrich_preserves_fantasy_truth_and_input(transport):
    requests = transport()
    original = snapshot()
    before = deepcopy(original)
    result = asyncio.run(PlayerDataClient().enrich_snapshot(original))
    assert original == before
    for key, value in original["roster"][0].items():
        assert result["roster"][0][key] == value
    player = result["roster"][0]
    assert player["jersey"] == "10"
    assert player["pro_team_name"] == "Los Angeles Chargers"
    assert player["pro_team_abbreviation"] == "LAC"
    assert result["fetched_at"] == "fantasy-time"
    assert result["presentation_warnings"] == []
    assert len(result["presentation_sources"]) == len(requests) == 2
    assert all("fetched_at" in source for source in result["presentation_sources"])


def test_cache_reuse_concurrent_coalescing_and_no_shared_output(transport):
    requests = transport()

    async def run():
        client = PlayerDataClient()
        results = await asyncio.gather(*(client.enrich_snapshot(snapshot()) for _ in range(12)))
        results[0]["roster"][0]["jersey"] = "99"
        again = await client.enrich_snapshot(snapshot())
        details = await asyncio.gather(*(client.player_detail(4038941) for _ in range(8)))
        details[0]["sources"][0]["url"] = "changed"
        detail = await client.player_detail(4038941)
        assert detail["player_id"] == 4038941
        assert detail["facts"] == []
        assert all(key in detail for key in
                   ("name", "pro_team_id", *METADATA_FIELDS))
        assert detail["sources"][0]["url"].startswith("https://site.web.api.espn.com/")
        assert again["roster"][0]["jersey"] == "10"
        assert detail["stats"]["regular_season"] == {"CMP": "22", "ATT": "31"}
        assert detail["news"][0]["headline"] == "Herbert practices in full"
        assert detail["injuries"][0]["practice_status"] == "Limited"
        assert len(requests) == 5
    asyncio.run(run())


def test_deep_research_checks_every_rostered_player_with_bounded_sources(transport):
    requests = transport()
    data = snapshot()
    data["roster"].append({
        **data["roster"][0], "player_id": 4038942, "name": "Second Player",
    })

    async def run():
        result = await PlayerDataClient().research_roster(data)
        assert result["scope"].startswith("Every supported player")
        assert [item["player_id"] for item in result["players"]] == [4038941, 4038942]
        assert result["players"][0]["news"][0]["headline"] == "Herbert practices in full"
        assert result["players"][0]["injury_reports"][0]["practice_status"] == "Limited"
        assert result["players"][0]["nfl_statistics"]["regular_season"]["CMP"] == "22"
        assert result["players"][1]["injury_reports"] == []
        assert result["limitations"]
        assert len(requests) == 3

    asyncio.run(run())


@pytest.mark.parametrize("identifier,team_id", [(-16001, 1), (-16024, 24)])
def test_signed_defense_ids_use_teams_not_athletes(transport, identifier, team_id):
    def handler(request):
        return {"sports": [{"leagues": [{"teams": [{"team": {
            **team(team_id), "displayName": "Verified NFL Team"}}]}]}]}
    requests = transport(handler)

    async def run():
        client = PlayerDataClient()
        data = snapshot()
        data["roster"][0].update(player_id=identifier, position="D/ST", pro_team_id=team_id)
        enriched = await client.enrich_snapshot(data)
        detail = await client.player_detail(identifier)
        assert enriched["roster"][0]["player_id"] == identifier
        assert enriched["roster"][0]["jersey"] is None
        assert detail["jersey"] is detail["headshot_url"] is None
        assert detail["pro_team_id"] == team_id
        assert detail["name"] == "Verified NFL Team D/ST"
        assert detail["source_url"] == f"https://www.espn.com/nfl/team/_/id/{team_id}"
        assert len(requests) == 2
    asyncio.run(run())


@pytest.mark.parametrize("jersey", [None, "", 10, 4038941, "4038941", "unknown", "123"])
def test_unknown_jersey_never_fabricated(transport, jersey):
    transport(lambda request: {"athlete": {**athlete(), "jersey": jersey}})
    detail = asyncio.run(PlayerDataClient().player_detail(4038941))
    assert detail["jersey"] is None
    assert detail["pro_team_id"] is None
    assert detail["pro_team_name"] is None
    assert detail["stats"] is None


@pytest.mark.parametrize("url", [
    "http://a.espncdn.com/i/photo.png", "https://evil.test/i/photo.png",
    "https://a.espncdn.com.evil.test/i/photo.png",
    "https://a.espncdn.com@evil.test/i/photo.png",
    "https://user:pass@a.espncdn.com/i/photo.png",
    "https://a.espncdn.com:444/i/photo.png",
    "https://a.espncdn.com\\@evil.test/i/photo.png",
    "https://a.espncdn.com/i/photo.png\n",
    "https://a.espncdn.com/not-an-image", "data:image/png;base64,AAAA",
    "https://a.espncdn.com/i/" + "a" * 1024,
])
def test_image_allowlist_rejects_unsafe_urls(transport, url):
    transport(lambda request: {"athlete": {**athlete(), "headshot": {"href": url},
                                         "team": {**team(), "logos": [{"href": url}]}}})
    detail = asyncio.run(PlayerDataClient().player_detail(4038941))
    assert detail["headshot_url"] is detail["team_logo_url"] is None


@pytest.mark.parametrize("host", ["a.espncdn.com", "a1.espncdn.com", "a2.espncdn.com"])
def test_official_image_hosts_allowed(transport, host):
    url = f"https://{host}/i/headshots/nfl/players/full/4038941.png"
    transport(lambda request: {"athlete": {**athlete(), "headshot": {"href": url}}})
    assert asyncio.run(PlayerDataClient().player_detail(4038941))["headshot_url"] == url


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
    httpx.Response(503, text="private upstream response"),
    httpx.Response(200, text="<html>private upstream response</html>"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={}),
    httpx.ConnectError("private upstream response"),
    httpx.ReadTimeout("private upstream response"),
])
def test_enrichment_failure_is_explicit_preserves_roster_and_is_cached(transport, response):
    requests = transport(lambda request: response)

    async def run():
        client = PlayerDataClient()
        for _ in range(2):
            original = snapshot()
            result = await client.enrich_snapshot(original)
            assert result["presentation_warnings"]
            assert result["presentation_sources"] == []
            assert "private upstream" not in json.dumps(result)
            for key, value in original["roster"][0].items():
                assert result["roster"][0][key] == value
            assert all(result["roster"][0][key] is None for key in METADATA_FIELDS)
        assert len(requests) == 1
    asyncio.run(run())


@pytest.mark.parametrize("data", [
    {}, {"athlete": {}}, {"athlete": {**athlete(), "id": "55"}},
    {"athlete": {**athlete(), "team": []}},
    {"athlete": {**athlete(), "displayName": None}},
])
def test_detail_schema_failures(transport, data):
    transport(lambda request: data)
    with pytest.raises(PlayerDataError):
        asyncio.run(PlayerDataClient().player_detail(4038941))


@pytest.mark.parametrize("data", [
    {}, {"team": {"id": "1"}, "athletes": []},
    {"team": {"id": "24"}, "athletes": {}},
    {"team": {"id": "24"}, "athletes": [{"items": "wrong"}]},
    {"team": {"id": "24"}, "athletes": [{"items": [{}]}]},
    {"team": {"id": "24"}, "athletes": [{"items": [athlete(), athlete()]}]},
])
def test_roster_schema_failure_is_not_missing_optional_data(transport, data):
    transport(lambda request: data if request.url.path.endswith("/roster") else catalogue())
    result = asyncio.run(PlayerDataClient().enrich_snapshot(snapshot()))
    assert result["presentation_warnings"]
    assert result["roster"][0]["jersey"] is None
    assert result["roster"][0]["pro_team_name"] == "Los Angeles Chargers"
    assert len(result["presentation_sources"]) == 1


def test_absent_player_and_empty_optional_fields_are_not_transport_errors(transport):
    transport(lambda request: {"team": {"id": "24"}, "athletes": [{"items": []}]}
              if request.url.path.endswith("/roster") else catalogue())
    result = asyncio.run(PlayerDataClient().enrich_snapshot(snapshot()))
    assert result["presentation_warnings"] == []
    assert result["roster"][0]["jersey"] is None
    assert result["roster"][0]["headshot_url"] is None


@pytest.mark.parametrize("identifier", [0, -1, -16000, -16041, 1_000_000_000, True,
                                      "4038941", "https://evil.test"])
def test_invalid_ids_never_make_requests(transport, identifier):
    requests = transport()
    with pytest.raises(PlayerDataError, match="Invalid"):
        asyncio.run(PlayerDataClient().player_detail(identifier))
    assert requests == []


def test_response_and_roster_bounds(transport, monkeypatch):
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 100)
    transport(lambda request: httpx.Response(200, content=b"x" * 101))
    with pytest.raises(PlayerDataError, match="size limit"):
        asyncio.run(PlayerDataClient().player_detail(4038941))
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 1_500_000)
    transport(lambda request: {"team": {"id": "24"},
                              "athletes": [{"items": [athlete(i + 1) for i in range(201)]}]}
              if request.url.path.endswith("/roster") else catalogue())
    result = asyncio.run(PlayerDataClient().enrich_snapshot(snapshot()))
    assert "player limit" in result["presentation_warnings"][0]


def test_snapshot_bound_does_not_truncate_fantasy_data(transport):
    requests = transport()
    data = snapshot()
    data["roster"] *= 101
    result = asyncio.run(PlayerDataClient().enrich_snapshot(data))
    assert result["roster"] == data["roster"]
    assert result["presentation_warnings"]
    assert requests == []


def test_cache_expiry_and_size_bounds(transport, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "MAX_CACHE_ENTRIES", 2)
    requests = transport(lambda request: {"athlete": athlete(int(request.url.path.rsplit("/", 1)[1]))})

    async def run():
        client = PlayerDataClient()
        await client._cached("athlete", 1)
        await client._cached("athlete", 1)
        assert len(requests) == 1
        now[0] += module.DETAIL_TTL + 1
        await client._cached("athlete", 1)
        assert len(requests) == 2
        await client._cached("athlete", 2)
        await client._cached("athlete", 3)
        assert len(client._cache) == 2
    asyncio.run(run())


def test_concurrency_bounded_and_editorial_not_retained(transport):
    active = maximum = 0

    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.005)
        active -= 1
        identifier = int(request.url.path.rsplit("/", 1)[1])
        return {"athlete": {**athlete(identifier), "displayName": "x" * 1000,
                            "news": "Never return editorial", "status": "public status",
                            "statsSummary": {"value": "unverified"}}}
    transport(handler)

    async def run():
        results = await asyncio.gather(*(client.player_detail(i) for i in range(1, 13)))
        assert maximum <= 4
        assert all(len(result["name"]) == 160 for result in results)
        assert "Never return editorial" not in json.dumps(results)
        assert "public status" not in json.dumps(results)
    client = PlayerDataClient()
    asyncio.run(run())


def test_streamed_response_without_length_is_bounded(transport, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 70_000
            yield b"x" * 70_000

    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 100_000)
    transport(lambda request: httpx.Response(200, stream=Stream()))
    with pytest.raises(PlayerDataError, match="size limit"):
        asyncio.run(PlayerDataClient().player_detail(4038941))


def test_pending_work_is_bounded_and_waiter_cancellation_is_isolated(transport, monkeypatch):
    monkeypatch.setattr(module, "MAX_PENDING", 1)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            started.set()
            await release.wait()
            return {"athlete": athlete()}
        requests = transport(handler)
        client = PlayerDataClient()
        first = asyncio.create_task(client._cached("athlete", 4038941))
        await started.wait()
        second = asyncio.create_task(client._cached("athlete", 4038941))
        await asyncio.sleep(0)
        with pytest.raises(PlayerDataError, match="busy"):
            await client._cached("athlete", 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)[0]["jersey"] == "10"
        assert len(requests) == 1
    asyncio.run(run())


def test_expired_success_not_used_to_hide_refresh_failure(transport, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    fail = [False]
    requests = transport(lambda request: httpx.Response(503) if fail[0]
                         else {"athlete": athlete()})

    async def run():
        client = PlayerDataClient()
        await client._cached("athlete", 4038941)
        now[0] += module.DETAIL_TTL + 1
        fail[0] = True
        for _ in range(2):
            with pytest.raises(PlayerDataError, match="503"):
                await client._cached("athlete", 4038941)
        assert len(requests) == 2
        now[0] += module.FAILURE_TTL + 1
        fail[0] = False
        assert (await client._cached("athlete", 4038941))[0]["jersey"] == "10"
        assert len(requests) == 3
    asyncio.run(run())


@pytest.mark.parametrize("data", [
    {"sports": []},
    {"sports": [{"leagues": [{"teams": {}}]}]},
    {"sports": [{"leagues": [{"teams": [{"team": team()}] * 41}]}]},
    {"sports": [{"leagues": [{"teams": [{"team": team()}, {"team": team()}]}]}]},
])
def test_catalogue_schema_and_size_failures_are_explicit(transport, data):
    transport(lambda request: data)
    result = asyncio.run(PlayerDataClient().enrich_snapshot(snapshot()))
    assert result["presentation_warnings"]
    assert result["presentation_sources"] == []
