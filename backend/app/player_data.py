"""Optional factual NFL metadata from public ESPN read APIs (no credentials).

Primary sources verified with anonymous HTTPS GETs on 2026-09-09:
https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=100
https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/24/roster
https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/4038941

Only identity, jersey, position, team and image fields are retained. No news,
outlooks, editorial text or public injury/points data replace fantasy truth.
Public metadata describes the current public roster, not a historical season.
Image consumers must allow only HTTPS a.espncdn.com, a1.espncdn.com and
a2.espncdn.com, and provide a fallback if an official asset is unavailable.
"""

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx


SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ATHLETES = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes"
INJURIES = f"{SITE}/injuries"
IMAGE_HOSTS = frozenset(("a.espncdn.com", "a1.espncdn.com", "a2.espncdn.com"))
METADATA_FIELDS = ("jersey", "pro_team_name", "pro_team_abbreviation",
                   "headshot_url", "team_logo_url")
MAX_RESPONSE_BYTES = 1_500_000
MAX_INJURY_RESPONSE_BYTES = 8_000_000
MAX_TEAMS = 40
MAX_ROSTER_PLAYERS = 200
MAX_SNAPSHOT_PLAYERS = 100
MAX_CACHE_ENTRIES = 160
MAX_PENDING = 64
TEAM_TTL = 3600
ROSTER_TTL = 900
DETAIL_TTL = 300
RESEARCH_TTL = 300
FAILURE_TTL = 30


class PlayerDataError(Exception):
    """Safe public-data failure without response bodies or private data."""


def _text(value, limit=160):
    return value[:limit] if isinstance(value, str) and value.strip() else None


def _id(value):
    if type(value) is int:
        return value if 0 < value < 1_000_000_000 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,8}", value):
        return int(value)
    return None


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _image(value):
    if not isinstance(value, str) or len(value) > 1024:
        return None
    if any(ord(char) < 33 for char in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in IMAGE_HOSTS
                or url.username or url.password or url.port not in (None, 443)
                or not url.path.startswith("/i/") or url.fragment):
            return None
    except ValueError:
        return None
    return value


def _jersey(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,2}", value) else None


def _web_url(value):
    if not isinstance(value, str) or len(value) > 1024:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname
                or not (url.hostname == "espn.com" or url.hostname.endswith(".espn.com"))
                or url.username or url.password or url.port not in (None, 443) or url.fragment):
            return None
    except ValueError:
        return None
    return value


def _news(raw):
    articles = raw.get("news", raw.get("articles")) if isinstance(raw, dict) else None
    if not isinstance(articles, list):
        return []
    result = []
    for article in articles[:8]:
        if not isinstance(article, dict) or not _text(article.get("headline"), 240):
            continue
        links = article.get("links")
        web = links.get("web") if isinstance(links, dict) else None
        result.append({
            "headline": _text(article["headline"], 240),
            "description": _text(article.get("description"), 500),
            "published_at": _text(article.get("published") or article.get("lastModified"), 60),
            "url": _web_url(web.get("href")) if isinstance(web, dict) else None,
            "premium": article.get("premium") if type(article.get("premium")) is bool else None,
        })
    return result


def _statistics(raw):
    statistics = raw.get("statistics") if isinstance(raw, dict) else None
    if not isinstance(statistics, dict):
        return None
    labels, splits = statistics.get("labels"), statistics.get("splits")
    if not isinstance(labels, list) or not isinstance(splits, list):
        return None
    regular = next(
        (split for split in splits if isinstance(split, dict)
         and split.get("displayName") == "Regular Season"),
        None,
    )
    values = regular.get("stats") if isinstance(regular, dict) else None
    if not isinstance(values, list):
        return None
    return {
        "label": _text(statistics.get("displayName"), 80),
        "regular_season": {
            str(label)[:20]: str(value)[:30]
            for label, value in list(zip(labels[:30], values[:30]))
            if isinstance(label, str) and isinstance(value, (str, int, float))
        },
    }


def _injury_map(raw):
    groups = raw.get("injuries") if isinstance(raw, dict) else None
    if not isinstance(groups, list):
        raise PlayerDataError("ESPN injury report has an unsupported schema")
    result = {}
    for group in groups[:40]:
        if isinstance(group, dict) and isinstance(group.get("athlete"), dict):
            injuries = [group]
        else:
            injuries = group.get("injuries") if isinstance(group, dict) else None
        if not isinstance(injuries, list):
            continue
        for injury in injuries[:150]:
            athlete = injury.get("athlete") if isinstance(injury, dict) else None
            identifier = _id(athlete.get("id")) if isinstance(athlete, dict) else None
            if identifier is None:
                continue
            details = injury.get("details")
            details = details if isinstance(details, dict) else {}
            result.setdefault(identifier, []).append({
                "status": _text(injury.get("status"), 80),
                "date": _text(injury.get("date"), 60),
                "short_comment": _text(injury.get("shortComment"), 300),
                "detail": _text(injury.get("longComment"), 500),
                "practice_status": _text(
                    details.get("practiceStatus") or injury.get("practiceStatus"), 80,
                ),
                "type": _text(details.get("type") or injury.get("type"), 80),
            })
    return result


def _team(raw):
    if not isinstance(raw, dict) or _id(raw.get("id")) is None or not _text(raw.get("displayName")):
        raise PlayerDataError("ESPN public team metadata has an unsupported schema")
    logos = raw.get("logos")
    logos = logos[:10] if isinstance(logos, list) else []
    logo = next((image for item in logos if isinstance(item, dict)
                 if (image := _image(item.get("href")))), None)
    return {
        "pro_team_id": _id(raw["id"]),
        "pro_team_name": _text(raw["displayName"]),
        "pro_team_abbreviation": _text(raw.get("abbreviation"), 12),
        "team_logo_url": logo or _image(raw.get("logo")),
    }


def _athlete(raw):
    if not isinstance(raw, dict) or _id(raw.get("id")) is None or not _text(raw.get("displayName")):
        raise PlayerDataError("ESPN public player metadata has an unsupported schema")
    position = raw.get("position")
    headshot = raw.get("headshot")
    return {
        "player_id": _id(raw["id"]),
        "name": _text(raw["displayName"]),
        "position": _text(position.get("abbreviation"), 12) if isinstance(position, dict) else None,
        "jersey": _jersey(raw.get("jersey")),
        "headshot_url": _image(headshot.get("href")) if isinstance(headshot, dict) else None,
    }


class PlayerDataClient:
    """App-lifetime bounded caches with coalesced refreshes and failure backoff."""

    def __init__(self):
        self._cache = OrderedDict()
        self._pending = {}
        self._requests = asyncio.Semaphore(4)

    async def aclose(self):
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _get(self, url, max_response_bytes=None):
        # All callers construct URLs from fixed origins and validated integer IDs.
        if max_response_bytes is None:
            max_response_bytes = MAX_RESPONSE_BYTES
        try:
            async with asyncio.timeout(20):
                async with self._requests:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(12, connect=5), follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                            if response.status_code != 200:
                                raise PlayerDataError(f"ESPN public metadata HTTP {response.status_code}")
                            size = response.headers.get("content-length", "")
                            if size.isdecimal() and int(size) > max_response_bytes:
                                raise PlayerDataError("ESPN public metadata exceeded the size limit")
                            body = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=65_536):
                                if len(body) + len(chunk) > max_response_bytes:
                                    raise PlayerDataError("ESPN public metadata exceeded the size limit")
                                body.extend(chunk)
            data = json.loads(body)
        except (TimeoutError, httpx.TimeoutException):
            raise PlayerDataError("ESPN public metadata request timed out") from None
        except httpx.HTTPError:
            raise PlayerDataError("ESPN public metadata network request failed") from None
        except (ValueError, RecursionError):
            raise PlayerDataError("ESPN public metadata returned invalid JSON") from None
        if not isinstance(data, dict):
            raise PlayerDataError("ESPN public metadata has an unsupported schema")
        return data

    async def _load(self, key):
        kind, identifier = key
        if kind == "teams":
            url, ttl = f"{SITE}/teams?limit=100", TEAM_TTL
        elif kind == "roster":
            url, ttl = f"{SITE}/teams/{identifier}/roster", ROSTER_TTL
        elif kind == "overview":
            url, ttl = f"{ATHLETES}/{identifier}/overview", RESEARCH_TTL
        elif kind == "team_injuries":
            url, ttl = f"{INJURIES}?team={identifier}", RESEARCH_TTL
        elif kind == "team_news":
            url, ttl = f"{SITE}/teams/{identifier}/news", RESEARCH_TTL
        else:
            url, ttl = f"{ATHLETES}/{identifier}", DETAIL_TTL
        try:
            data = await self._get(
                url,
                MAX_INJURY_RESPONSE_BYTES if kind == "team_injuries" else MAX_RESPONSE_BYTES,
            )
            if kind == "teams":
                try:
                    teams = data["sports"][0]["leagues"][0]["teams"]
                except (KeyError, IndexError, TypeError):
                    raise PlayerDataError("ESPN public team catalogue has an unsupported schema") from None
                if not isinstance(teams, list) or not 1 <= len(teams) <= MAX_TEAMS:
                    raise PlayerDataError("ESPN public team catalogue has an unsupported size")
                parsed = {}
                for item in teams:
                    team = _team(item.get("team") if isinstance(item, dict) else None)
                    if team["pro_team_id"] in parsed:
                        raise PlayerDataError("ESPN public team catalogue contains duplicate teams")
                    parsed[team["pro_team_id"]] = team
            elif kind == "roster":
                team = data.get("team")
                if not isinstance(team, dict) or _id(team.get("id")) != identifier:
                    raise PlayerDataError("ESPN public roster does not match the requested team")
                groups = data.get("athletes")
                if not isinstance(groups, list) or not 1 <= len(groups) <= 10:
                    raise PlayerDataError("ESPN public roster has an unsupported schema")
                parsed = {}
                count = 0
                for group in groups:
                    items = group.get("items") if isinstance(group, dict) else None
                    if not isinstance(items, list):
                        raise PlayerDataError("ESPN public roster has an unsupported schema")
                    count += len(items)
                    if count > MAX_ROSTER_PLAYERS:
                        raise PlayerDataError("ESPN public roster exceeded the player limit")
                    for item in items:
                        athlete = _athlete(item)
                        if athlete["player_id"] in parsed:
                            raise PlayerDataError("ESPN public roster contains duplicate players")
                        parsed[athlete["player_id"]] = athlete
            elif kind == "athlete":
                raw = data.get("athlete")
                parsed = _athlete(raw)
                if parsed["player_id"] != identifier:
                    raise PlayerDataError("ESPN public player does not match the requested ID")
                team = raw.get("team")
                if team is not None:
                    parsed.update(_team(team))
                parsed = {**dict.fromkeys(METADATA_FIELDS), "pro_team_id": None, **parsed}
            elif kind == "overview":
                parsed = {"news": _news(data), "statistics": _statistics(data)}
            elif kind == "team_injuries":
                parsed = _injury_map(data)
            else:
                parsed = {"news": _news(data), "statistics": None}
            entry = (time.monotonic() + ttl, parsed, None,
                     {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat()})
        except PlayerDataError as error:
            entry = (time.monotonic() + FAILURE_TTL, None, str(error), None)
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return entry

    async def _cached(self, kind, identifier=0):
        key = (kind, identifier)
        entry = self._cache.get(key)
        if entry is None or entry[0] <= time.monotonic():
            task = self._pending.get(key)
            if task is None:
                if len(self._pending) >= MAX_PENDING:
                    raise PlayerDataError("ESPN public metadata is busy; retry later")
                task = asyncio.create_task(self._load(key))
                self._pending[key] = task
                task.add_done_callback(lambda completed: self._pending.pop(key, None))
            entry = await asyncio.shield(task)
        else:
            self._cache.move_to_end(key)
        if entry[2]:
            raise PlayerDataError(entry[2])
        return entry[1], entry[3]

    async def enrich_snapshot(self, snapshot: dict) -> dict:
        """Preserve fantasy fields; failure is explicit and never blocks league reads."""
        result = deepcopy(snapshot)
        warnings = result.setdefault("presentation_warnings", [])
        result["presentation_sources"] = []
        roster = result.get("roster")
        if not isinstance(roster, list) or len(roster) > MAX_SNAPSHOT_PLAYERS or any(
                not isinstance(player, dict) for player in roster):
            warnings.append("Public player metadata unavailable: unsupported fantasy roster size or schema")
            return result
        for player in roster:
            player.update(dict.fromkeys(METADATA_FIELDS))
        if not roster:
            return result
        try:
            teams, source = await self._cached("teams")
        except PlayerDataError as error:
            warnings.append(str(error))
            return result
        result["presentation_sources"].append(deepcopy(source))
        team_ids = {_id(player.get("pro_team_id")) for player in roster
                    if type(player.get("player_id")) is int and player["player_id"] > 0}
        team_ids = sorted(identifier for identifier in team_ids if identifier in teams)

        async def get_roster(identifier):
            try:
                return identifier, await self._cached("roster", identifier), None
            except PlayerDataError as error:
                return identifier, None, str(error)

        public_rosters = {}
        for identifier, loaded, error in await asyncio.gather(*(get_roster(i) for i in team_ids)):
            if error:
                warnings.append(f"{teams[identifier]['pro_team_name']} player details unavailable: {error}")
            else:
                public_rosters[identifier] = loaded[0]
                result["presentation_sources"].append(deepcopy(loaded[1]))
        for player in roster:
            identifier = player.get("player_id")
            team_id = _id(player.get("pro_team_id"))
            # Fantasy D/ST IDs use -16000 minus the NFL team ID; never request
            # a negative ID from an athlete API or use it as a jersey number.
            if team_id is None and type(identifier) is int and -16040 <= identifier < -16000:
                team_id = -identifier - 16000
            team = teams.get(team_id)
            if team:
                player.update({key: team[key] for key in
                               ("pro_team_name", "pro_team_abbreviation", "team_logo_url")})
            athlete = public_rosters.get(team_id, {}).get(identifier) if type(identifier) is int else None
            if athlete:
                player.update({key: athlete[key] for key in ("jersey", "headshot_url")})
        return result

    async def player_detail(self, player_id: int) -> dict:
        """Fetch selected-player metadata, current NFL stats, news and injury reports."""
        if type(player_id) is not int:
            raise PlayerDataError("Invalid ESPN player ID")
        if -16040 <= player_id < -16000:
            team_id = -player_id - 16000
            teams, source = await self._cached("teams")
            if team_id not in teams:
                raise PlayerDataError("ESPN public defense team was not found")
            team = teams[team_id]
            result = {**dict.fromkeys(METADATA_FIELDS), **team, "player_id": player_id,
                      "name": f"{team['pro_team_name']} D/ST", "position": "D/ST"}
            source_url = f"https://www.espn.com/nfl/team/_/id/{team_id}"
            research_kind, research_id = "team_news", team_id
        elif _id(player_id):
            result, source = await self._cached("athlete", player_id)
            source_url = f"https://www.espn.com/nfl/player/_/id/{player_id}"
            research_kind, research_id = "overview", player_id
        else:
            raise PlayerDataError("Invalid ESPN player ID")
        warnings = []
        sources = [deepcopy(source)]
        research = {"news": [], "statistics": None}
        injuries = []
        try:
            research, research_source = await self._cached(research_kind, research_id)
            sources.append(deepcopy(research_source))
        except PlayerDataError as error:
            warnings.append(str(error))
        if player_id > 0 and _id(result.get("pro_team_id")) is not None:
            try:
                injury_data, injury_source = await self._cached(
                    "team_injuries", result["pro_team_id"],
                )
                injuries = injury_data.get(player_id, [])
                sources.append(deepcopy(injury_source))
            except PlayerDataError as error:
                warnings.append(str(error))
        return {
            **deepcopy(result),
            "source_url": source_url,
            "sources": sources,
            "fetched_at": source["fetched_at"],
            "facts": [],
            "stats": research["statistics"],
            "news": research["news"],
            "injuries": deepcopy(injuries),
            "presentation_warnings": warnings,
            "metadata_note": (
                "Current public ESPN metadata, NFL stats, news and injury reports; "
                "fantasy status and points come from your league."
            ),
        }

    async def research_roster(self, snapshot: dict) -> dict:
        """Collect bounded ESPN news, NFL stats and injuries for every roster row."""
        roster = snapshot.get("roster") if isinstance(snapshot, dict) else None
        if not isinstance(roster, list) or len(roster) > MAX_SNAPSHOT_PLAYERS:
            raise PlayerDataError("Deep research requires a supported ESPN roster snapshot")
        if any(not isinstance(player, dict) for player in roster):
            raise PlayerDataError("Deep research received a malformed ESPN roster")
        team_ids = sorted({
            team_id for player in roster
            if (team_id := _id(player.get("pro_team_id"))) is not None
        })

        async def load_injuries(team_id):
            try:
                return team_id, await self._cached("team_injuries", team_id), None
            except PlayerDataError as error:
                return team_id, None, str(error)

        injury_data, injury_sources, injury_warnings = {}, [], []
        for team_id, loaded, error in await asyncio.gather(
                *(load_injuries(team_id) for team_id in team_ids)):
            if error:
                injury_warnings.append(f"Team {team_id} injury report unavailable: {error}")
            else:
                injury_data.update(loaded[0])
                injury_sources.append(deepcopy(loaded[1]))

        async def load(player):
            identifier = player.get("player_id")
            team_id = _id(player.get("pro_team_id"))
            if type(identifier) is not int or identifier == 0:
                return None
            if identifier > 0:
                kind, research_id = "overview", identifier
            elif -16040 <= identifier < -16000 and team_id is not None:
                kind, research_id = "team_news", team_id
            else:
                return {
                    "player_id": identifier,
                    "name": _text(player.get("name")) or "Unknown player",
                    "error": "No supported ESPN research source is available for this roster entry",
                }
            try:
                research, source = await self._cached(kind, research_id)
                return {
                    "player_id": identifier,
                    "name": _text(player.get("name")) or "Unknown player",
                    "position": _text(player.get("position"), 12),
                    "lineup_slot_id": player.get("slot_id") if type(player.get("slot_id")) is int else None,
                    "fantasy_projection": _number(player.get("projected_points")),
                    "fantasy_points": _number(player.get("actual_points")),
                    "fantasy_injury_status": _text(player.get("injury_status"), 80),
                    "percent_started": _number(player.get("percent_started")),
                    "season_outlook": _text(player.get("season_outlook"), 1200),
                    "injury_reports": deepcopy(injury_data.get(identifier, [])),
                    "news": deepcopy(research["news"]),
                    "nfl_statistics": deepcopy(research["statistics"]),
                    "source": deepcopy(source),
                }
            except PlayerDataError as error:
                return {
                    "player_id": identifier,
                    "name": _text(player.get("name")) or "Unknown player",
                    "error": str(error),
                }

        players = [item for item in await asyncio.gather(*(load(player) for player in roster))
                   if item is not None]
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Every supported player on the current ESPN fantasy roster",
            "players": players,
            "warnings": injury_warnings,
            "sources": injury_sources,
            "limitations": [
                "ESPN may publish practice participation inside injury comments or player news",
                "At most eight current ESPN news items are retained per player or defense",
                "Missing reports mean ESPN returned no matching item, not that a player is healthy",
            ],
        }
