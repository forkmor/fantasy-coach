"""Read-only ESPN adapter; community evidence is not a verified write contract.

Read URL, cookies and views:
https://github.com/cwendt94/espn-api/blob/master/espn_api/requests/espn_requests.py
https://github.com/cwendt94/espn-api/blob/master/espn_api/requests/constant.py
Normalization: espn_api/base_league.py, football/{league,team,player,transaction}.py
"""

import asyncio
from datetime import datetime, timezone
import json
import math
import re

import httpx


class EspnError(Exception):
    """A safe ESPN error; never includes cookies, owner IDs or response bodies."""


class EspnUnknownOutcome(EspnError):
    """The write may have reached ESPN and must not be retried automatically."""


CAPABILITIES = [
    {
        "action": "set_lineup",
        "enabled": True,
        "reason": "Verified for exact eligible starter/bench or IR swaps with lock checks and readback",
    },
    {
        "action": "add_drop",
        "enabled": True,
        "reason": "Verified for one unlocked free-agent add or one droppable roster-player drop with readback",
    },
    *[
        {"action": action, "enabled": False,
         "reason": "Authenticated ESPN write contract has not been verified"}
        for action in ("waiver_submit", "waiver_cancel",
                       "trade_propose", "trade_accept", "trade_reject", "trade_cancel")
    ],
]

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST",
             6: "DT", 7: "DE", 8: "LB", 9: "CB", 10: "S"}
SLOTS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17,
         "FLEX": 23, "OP": 7, "DT": 8, "DE": 9, "LB": 10, "DL": 11,
         "CB": 12, "S": 13, "DB": 14, "DP": 15}


def _number(value):
    return value if type(value) in (int, float) and abs(value) <= 1e15 and math.isfinite(value) else None


def _integer(value):
    return value if type(value) is int else None


def _text(value, maximum=160):
    return value[:maximum] if isinstance(value, str) else None


def _fields(source, fields):
    source = source if isinstance(source, dict) else {}
    result = {}
    for field in fields:
        value = source.get(field)
        if value is None or isinstance(value, (str, bool)):
            result[field] = _text(value) if isinstance(value, str) else value
        else:
            result[field] = _number(value)
    return result


def _numeric_map(value, limit=150):
    if not isinstance(value, dict):
        return None
    return {str(key): _number(number) for key, number in list(value.items())[:limit]
            if re.fullmatch(r"-?\d{1,8}", str(key))}


class EspnClient:
    def __init__(self, espn_s2: str, swid: str, league_id: int = 656212638,
                 team_id: int = 4, season: int = 2026):
        if (league_id, season) != (656212638, 2026) or type(team_id) is not int or team_id <= 0:
            raise EspnError("ESPN access is restricted to league 656212638 for the 2026 season")
        if (not isinstance(espn_s2, str) or not espn_s2
                or any(ord(char) < 33 or ord(char) > 126 or char in ';,"\\' for char in espn_s2)):
            raise EspnError("Configure a valid ESPN espn_s2 cookie")
        if not isinstance(swid, str) or not re.fullmatch(
            r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?", swid,
        ) or swid.startswith("{") != swid.endswith("}"):
            raise EspnError("Configure a valid ESPN SWID cookie")
        self._espn_s2 = espn_s2
        self._swid = "{" + swid.strip("{}").upper() + "}"
        self.league_id, self.team_id, self.season = league_id, team_id, season
        self._url = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
                     f"/seasons/{season}/segments/0/leagues/{league_id}")
        self._write_url = ("https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl"
                           f"/seasons/{season}/segments/0/leagues/{league_id}/transactions")

    async def discover_owned_team(self) -> dict:
        data = await self._get(["mTeam", "mRoster", "mSettings"])
        teams = data.get("teams")
        if not isinstance(teams, list):
            raise EspnError("ESPN did not return league teams; check both cookies and league membership")
        matches = [
            team for team in teams
            if isinstance(team, dict)
            and type(team.get("id")) is int
            and team["id"] > 0
            and isinstance(team.get("owners"), list)
            and any(
                isinstance(owner, str)
                and owner.strip("{}").upper() == self._swid.strip("{}")
                for owner in team["owners"]
            )
        ]
        if not matches:
            raise EspnError("These ESPN credentials do not own a team in this Fieldhouse league")
        if len(matches) != 1:
            raise EspnError("These ESPN credentials match multiple teams; automatic team selection is unsafe")
        team = matches[0]
        name = team.get("name")
        if not isinstance(name, str) or not name.strip():
            name = " ".join(
                value.strip() for value in (team.get("location"), team.get("nickname"))
                if isinstance(value, str) and value.strip()
            ) or f"Team {team['id']}"
        return {"team_id": team["id"], "team_name": name[:160]}

    async def _get(self, views, period=None, filters=None):
        params = [("view", view) for view in views]
        if period is not None:
            params.append(("scoringPeriodId", str(period)))
        headers = {"Accept": "application/json"}
        if filters is not None:
            headers["x-fantasy-filter"] = json.dumps(filters, separators=(",", ":"))
        try:
            async with asyncio.timeout(40):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream(
                        "GET", self._url, params=params, headers=headers,
                        cookies={"espn_s2": self._espn_s2, "SWID": self._swid},
                    ) as response:
                        if response.status_code != 200:
                            status = response.status_code
                            detail = {
                                401: "unauthenticated; check both ESPN cookies",
                                403: "access denied; check cookies and league membership",
                                404: "configured league or season not found",
                                429: "rate limit exceeded; retry later",
                            }.get(status, "read request failed")
                            raise EspnError(f"ESPN HTTP {status}: {detail}")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 8_000_000:
                                raise EspnError("ESPN response exceeded the size limit")
            data = json.loads(body)
        except (httpx.TimeoutException, TimeoutError):
            raise EspnError("ESPN read request timed out") from None
        except httpx.HTTPError:
            raise EspnError("ESPN network request failed") from None
        except (TypeError, ValueError):
            raise EspnError("ESPN returned invalid JSON; authentication may have expired") from None
        if not isinstance(data, dict):
            raise EspnError("ESPN returned an unsupported response schema")
        for field, expected in (("id", self.league_id), ("seasonId", self.season)):
            if field in data and data[field] != expected:
                raise EspnError("ESPN response does not match the configured league or season")
        return data

    async def _context(self, extra_views=()):
        views = list(dict.fromkeys(["mTeam", "mRoster", "mSettings", *extra_views]))
        data = await self._get(views)
        teams = data.get("teams")
        if not isinstance(teams, list):
            raise EspnError("ESPN did not return teams; authentication or league schema is unverified")
        matches = [team for team in teams if isinstance(team, dict) and team.get("id") == self.team_id]
        if len(matches) != 1:
            raise EspnError("ESPN did not return the configured team")
        team = matches[0]
        owners = team.get("owners")
        if not isinstance(owners, list) or not any(
                isinstance(owner, str) and owner.strip("{}").upper() == self._swid.strip("{}")
                for owner in owners):
            raise EspnError("ESPN team ownership could not be verified against the configured SWID")
        period = data.get("scoringPeriodId")
        if type(period) is not int or period < 0:
            raise EspnError("ESPN did not return a valid scoring period")
        return data, team, period

    def _base(self, period):
        return {"fetched_at": datetime.now(timezone.utc).isoformat(),
                "league_id": self.league_id, "team_id": self.team_id,
                "season": self.season, "scoring_period_id": period, "ownership_verified": True}

    def _player(self, entry, period):
        if not isinstance(entry, dict):
            raise EspnError("ESPN returned a malformed player entry")
        pool = entry.get("playerPoolEntry", entry)
        if not isinstance(pool, dict) or not isinstance(pool.get("player"), dict):
            raise EspnError("ESPN returned a malformed player record")
        player = pool["player"]
        identifier, name = _integer(player.get("id")), _text(player.get("fullName"))
        if identifier is None or not name:
            raise EspnError("ESPN returned a player without an ID or name")
        slots = player.get("eligibleSlots")
        ownership = player.get("ownership")
        ownership = ownership if isinstance(ownership, dict) else {}
        result = {
            "player_id": identifier, "name": name,
            "position": POSITIONS.get(_integer(player.get("defaultPositionId")), "UNKNOWN"),
            "eligible_slots": [slot for slot in slots[:40] if type(slot) is int]
            if isinstance(slots, list) else [],
            "eligible_slots_known": isinstance(slots, list),
            "slot_id": _integer(entry.get("lineupSlotId")),
            "injury_status": _text(player.get("injuryStatus")) or "UNKNOWN",
            "pro_team_id": _integer(player.get("proTeamId")),
            "availability_status": _text(pool.get("status")) or "UNKNOWN",
            "on_team_id": _integer(pool.get("onTeamId")),
            "locked": pool.get("lineupLocked") if type(pool.get("lineupLocked")) is bool else None,
            "roster_locked": pool.get("rosterLocked") if type(pool.get("rosterLocked")) is bool else None,
            "droppable": player.get("droppable") if type(player.get("droppable")) is bool else None,
            "percent_owned": _number(ownership.get("percentOwned")),
            "percent_started": _number(ownership.get("percentStarted")),
            "average_draft_position": _number(ownership.get("averageDraftPosition")),
            "last_news_at": (
                datetime.fromtimestamp(player["lastNewsDate"] / 1000, timezone.utc).isoformat()
                if type(player.get("lastNewsDate")) in (int, float)
                and 0 < player["lastNewsDate"] < 10_000_000_000_000 else None
            ),
            "season_outlook": _text(player.get("seasonOutlook"), 1200),
            "lock_status": (
                "LOCKED" if pool.get("lineupLocked") is True or pool.get("rosterLocked") is True
                else "UNLOCKED" if pool.get("lineupLocked") is False and pool.get("rosterLocked") is False
                else "UNKNOWN"
            ),
            "projected_points": None, "actual_points": None,
        }
        stats = player.get("stats")
        if isinstance(stats, list):
            for stat in stats:
                if (isinstance(stat, dict) and stat.get("seasonId") == self.season
                        and stat.get("scoringPeriodId") == period and stat.get("statSplitTypeId") == 1):
                    if stat.get("statSourceId") == 0:
                        result["actual_points"] = _number(stat.get("appliedTotal"))
                    elif stat.get("statSourceId") == 1:
                        result["projected_points"] = _number(stat.get("appliedTotal"))
        return result

    @staticmethod
    def _settings(data):
        data = data if isinstance(data, dict) else {}
        sections = {
            "acquisitionSettings": ("acquisitionBudget", "acquisitionLimit", "acquisitionType",
                                    "isUsingAcquisitionBudget", "matchupAcquisitionLimit",
                                    "waiverHours", "waiverOrderReset", "waiverProcessDays",
                                    "waiverProcessHour", "waiverSystem", "seasonAcquisitionLimit"),
            "rosterSettings": ("isBenchUnlimited", "isUsingUndroppableList", "lineupLocktimeType",
                               "moveLimit", "positionLimits", "rosterLocktimeType", "universeIds"),
            "tradeSettings": ("allowOutOfUniverse", "deadlineDate", "max", "revisionHours",
                              "vetoVotesRequired"),
            "scheduleSettings": ("matchupPeriodCount", "matchupPeriodLength", "playoffMatchupPeriodLength",
                                 "playoffSeedingRule", "playoffTeamCount"),
            "scoringSettings": ("scoringType", "playerRankType"),
        }
        result = {name: _fields(data.get(name), fields) if isinstance(data.get(name), dict) else None
                  for name, fields in sections.items()}
        roster = data.get("rosterSettings")
        if isinstance(roster, dict):
            result["rosterSettings"]["lineupSlotCounts"] = _numeric_map(roster.get("lineupSlotCounts"))
            result["rosterSettings"]["positionLimits"] = _numeric_map(roster.get("positionLimits"))
        scoring = data.get("scoringSettings")
        if isinstance(scoring, dict):
            items = scoring.get("scoringItems")
            result["scoringSettings"]["scoringItems"] = [
                {**_fields(item, ("statId", "points", "isReverseItem")),
                 "pointsOverrides": _numeric_map(item.get("pointsOverrides"))}
                for item in items[:200] if isinstance(item, dict)
            ] if isinstance(items, list) else None
        acquisition = data.get("acquisitionSettings")
        if isinstance(acquisition, dict):
            days = acquisition.get("waiverProcessDays")
            result["acquisitionSettings"]["waiverProcessDays"] = [
                day for day in days[:7] if type(day) is int
            ] if isinstance(days, list) else None
        return result

    async def snapshot(self) -> dict:
        data, team, period = await self._context()
        roster = team.get("roster")
        if not isinstance(roster, dict) or not isinstance(roster.get("entries"), list):
            raise EspnError("ESPN roster is missing; an empty roster cannot be assumed")
        entries = roster["entries"]
        settings = self._settings(data.get("settings"))
        acquisition = settings.get("acquisitionSettings") or {}
        counter = team.get("transactionCounter")
        counter = counter if isinstance(counter, dict) else {}
        total, spent = _number(acquisition.get("acquisitionBudget")), _number(counter.get("acquisitionBudgetSpent"))
        record = team.get("record")
        record = record if isinstance(record, dict) else {}
        name = _text(team.get("name"))
        if not name:
            name = " ".join(filter(None, (_text(team.get("location")), _text(team.get("nickname"))))) or "UNKNOWN"
        return {**self._base(period),
                "team": {"id": self.team_id, "name": name, "abbrev": _text(team.get("abbrev")),
                         "waiver_rank": _integer(team.get("waiverRank")),
                         "record": _fields(record.get("overall"), ("wins", "losses", "ties", "pointsFor", "pointsAgainst")),
                         "transaction_counter": _fields(counter, ("acquisitions", "drops", "trades", "acquisitionBudgetSpent"))},
                "roster": [self._player(entry, period) for entry in entries[:100]],
                "roster_truncated": len(entries) > 100, "settings": settings,
                "budget": {"total": total, "spent": spent,
                           "remaining": total - spent if total is not None and spent is not None else None},
                "limitations": ["Roster data comes from ESPN's authenticated read API",
                                "Only direct owner-confirmed starter/bench swaps can write to ESPN",
                                "Null and UNKNOWN values are unavailable, not zero or false",
                                "Pending waiver/trade completeness is unverified"]}

    @staticmethod
    def _team_name(team):
        name = _text(team.get("name"))
        return name or " ".join(filter(None, (
            _text(team.get("location")), _text(team.get("nickname")),
        ))) or "Unknown team"

    async def league_overview(self) -> dict:
        data, _, period = await self._context(("mMatchup", "mStatus"))
        teams = data.get("teams")
        if not isinstance(teams, list):
            raise EspnError("ESPN did not return league standings")
        standings = []
        valid_team_ids = set()
        for team in teams[:100]:
            if not isinstance(team, dict) or _integer(team.get("id")) is None:
                raise EspnError("ESPN returned a malformed standings row")
            team_id = team["id"]
            valid_team_ids.add(team_id)
            record = team.get("record")
            record = record.get("overall") if isinstance(record, dict) else {}
            counter = team.get("transactionCounter")
            counter = counter if isinstance(counter, dict) else {}
            standings.append({
                "team_id": team_id,
                "name": self._team_name(team),
                "abbrev": _text(team.get("abbrev")),
                "is_my_team": team_id == self.team_id,
                "playoff_seed": _integer(team.get("playoffSeed")),
                "waiver_rank": _integer(team.get("waiverRank")),
                "points": _number(team.get("points")),
                "points_adjusted": _number(team.get("pointsAdjusted")),
                "record": _fields(record, (
                    "wins", "losses", "ties", "percentage", "pointsFor", "pointsAgainst",
                    "gamesBack", "streakLength", "streakType",
                )),
                "transactions": _fields(counter, (
                    "acquisitions", "drops", "trades", "moveToActive", "moveToIR",
                    "acquisitionBudgetSpent",
                )),
            })
        standings.sort(key=lambda row: (
            row["playoff_seed"] is None,
            row["playoff_seed"] if row["playoff_seed"] is not None else 10_000,
            row["name"].lower(),
        ))
        for rank, row in enumerate(standings, 1):
            row["rank"] = rank

        matchups = []
        schedule = data.get("schedule")
        if isinstance(schedule, list):
            for matchup in schedule[:500]:
                if not isinstance(matchup, dict) or matchup.get("matchupPeriodId") != period:
                    continue
                sides = {}
                for side_name in ("home", "away"):
                    side = matchup.get(side_name)
                    if not isinstance(side, dict):
                        sides[side_name] = None
                        continue
                    team_id = _integer(side.get("teamId"))
                    sides[side_name] = {
                        "team_id": team_id if team_id in valid_team_ids else None,
                        "total_points": _number(side.get("totalPoints")),
                        "total_projected_points": _number(side.get("totalProjectedPoints")),
                    }
                matchups.append({
                    "matchup_id": _integer(matchup.get("id")),
                    "matchup_period_id": period,
                    "winner": _text(matchup.get("winner")),
                    **sides,
                })
        status = data.get("status")
        status = status if isinstance(status, dict) else {}
        return {
            **self._base(period),
            "league": {
                "team_count": len(standings),
                "current_matchup_period": _integer(status.get("currentMatchupPeriod")),
                "final_scoring_period": _integer(status.get("finalScoringPeriod")),
                "is_active": status.get("isActive") if type(status.get("isActive")) is bool else None,
            },
            "standings": standings,
            "matchups": matchups,
            "limitations": [
                "Standings and matchup totals reflect ESPN's latest published league data",
                "Manager identities and private member details are intentionally omitted",
            ],
        }

    @staticmethod
    def _find_swap_players(snapshot, starter_player_id, bench_player_id):
        roster = snapshot.get("roster")
        if not isinstance(roster, list):
            raise EspnError("ESPN did not return a roster for lineup validation")
        by_id = {player.get("player_id"): player for player in roster if isinstance(player, dict)}
        starter, bench = by_id.get(starter_player_id), by_id.get(bench_player_id)
        if starter is None or bench is None:
            raise EspnError("Both lineup players must still be on your ESPN roster")
        starter_slot, bench_slot = starter.get("slot_id"), bench.get("slot_id")
        if type(starter_slot) is not int or starter_slot in (20, 21):
            raise EspnError("The player being benched is no longer in a starting slot")
        if bench_slot != 20:
            raise EspnError("The replacement player is no longer on the bench")
        if starter_slot not in bench.get("eligible_slots", []):
            raise EspnError(f"{bench['name']} is not eligible for the selected starting slot")
        if 20 not in starter.get("eligible_slots", []):
            raise EspnError(f"{starter['name']} is not eligible for the bench")
        for player in (starter, bench):
            if player.get("lock_status") == "UNKNOWN":
                raise EspnError(f"ESPN did not provide a lock status for {player['name']}")
            if player.get("locked") is not False or player.get("roster_locked") is not False:
                raise EspnError(f"{player['name']} is locked and cannot be moved")
        return starter, bench

    async def preview_lineup_swap(self, starter_player_id: int, bench_player_id: int) -> dict:
        snapshot = await self.snapshot()
        starter, bench = self._find_swap_players(snapshot, starter_player_id, bench_player_id)
        return {
            "scoring_period_id": snapshot["scoring_period_id"],
            "starter": {
                "player_id": starter["player_id"], "name": starter["name"],
                "from_slot_id": starter["slot_id"], "to_slot_id": 20,
            },
            "bench": {
                "player_id": bench["player_id"], "name": bench["name"],
                "from_slot_id": 20, "to_slot_id": starter["slot_id"],
            },
        }

    async def _post_lineup_swap(self, preview: dict) -> None:
        body = {
            "isLeagueManager": False,
            "teamId": self.team_id,
            "type": "ROSTER",
            "scoringPeriodId": preview["scoring_period_id"],
            "executionType": "EXECUTE",
            "memberId": self._swid,
            "items": [
                {
                    "playerId": preview["bench"]["player_id"], "type": "LINEUP",
                    "fromLineupSlotId": 20,
                    "toLineupSlotId": preview["starter"]["from_slot_id"],
                },
                {
                    "playerId": preview["starter"]["player_id"], "type": "LINEUP",
                    "fromLineupSlotId": preview["starter"]["from_slot_id"],
                    "toLineupSlotId": 20,
                },
            ],
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://fantasy.espn.com",
            "Referer": (
                "https://fantasy.espn.com/football/team"
                f"?leagueId={self.league_id}&teamId={self.team_id}&seasonId={self.season}"
            ),
            "x-fantasy-platform": "espn-fantasy-web",
            "x-fantasy-source": "kona",
        }
        try:
            async with asyncio.timeout(35):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False,
                    cookies={"espn_s2": self._espn_s2, "SWID": self._swid},
                ) as client:
                    response = await client.post(
                        self._write_url, headers=headers, json=body,
                    )
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
            raise EspnUnknownOutcome(
                "ESPN did not return a definitive lineup response; the move will be reconciled without retrying"
            ) from None
        if response.status_code >= 500:
            raise EspnUnknownOutcome(
                f"ESPN HTTP {response.status_code} did not provide a definitive lineup outcome"
            )
        if response.status_code not in (200, 201, 202):
            detail = {
                400: "ESPN rejected the lineup request",
                401: "ESPN authentication expired",
                403: "ESPN denied the lineup request",
                409: "ESPN reported a lineup conflict",
            }.get(response.status_code, "ESPN rejected the lineup request")
            raise EspnError(f"{detail} (HTTP {response.status_code})")

    async def execute_lineup_swap(self, expected: dict) -> dict:
        fresh = await self.preview_lineup_swap(
            expected["starter"]["player_id"], expected["bench"]["player_id"],
        )
        if fresh != expected:
            raise EspnError("The lineup changed after preview; refresh and review the swap again")
        uncertain = None
        try:
            await self._post_lineup_swap(expected)
        except EspnUnknownOutcome as exc:
            uncertain = exc
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(1)
            try:
                snapshot = await self.snapshot()
            except EspnError:
                raise EspnUnknownOutcome(
                    "The lineup request may have succeeded, but ESPN readback failed; do not retry"
                ) from None
            roster = {player["player_id"]: player for player in snapshot["roster"]}
            starter = roster.get(expected["starter"]["player_id"])
            bench = roster.get(expected["bench"]["player_id"])
            if (starter and bench and starter.get("slot_id") == 20
                    and bench.get("slot_id") == expected["starter"]["from_slot_id"]):
                return {
                    "status": "completed",
                    "verified_at": snapshot["fetched_at"],
                    "scoring_period_id": snapshot["scoring_period_id"],
                }
        if uncertain is not None:
            raise uncertain
        raise EspnUnknownOutcome(
            "ESPN accepted the request but readback did not verify both lineup slots; do not retry"
        )

    async def preview_lineup_moves(self, moves: list[dict]) -> dict:
        if len(moves) != 2:
            raise EspnError("Live lineup execution requires exactly one mirrored two-player swap")
        targets = {move.get("slot_id") for move in moves}
        if 20 not in targets or len(targets) != 2:
            raise EspnError("Live lineup execution requires one player moving to the bench")
        starter_move = next(move for move in moves if move.get("slot_id") == 20)
        bench_move = next(move for move in moves if move.get("slot_id") != 20)
        preview = await self.preview_lineup_swap(
            starter_move.get("player_id"), bench_move.get("player_id"),
        )
        if bench_move.get("slot_id") != preview["starter"]["from_slot_id"]:
            raise EspnError("The proposed lineup moves are not an exact mirrored swap")
        return preview

    async def execute_lineup_moves(self, moves: list[dict]) -> dict:
        preview = await self.preview_lineup_moves(moves)
        return await self.execute_lineup_swap(preview)

    async def preview_add_drop(
        self, add_player_id: int | None = None, drop_player_id: int | None = None,
    ) -> dict:
        if type(add_player_id) is bool or type(drop_player_id) is bool:
            raise EspnError("ESPN player IDs must be integers")
        if sum(value is not None for value in (add_player_id, drop_player_id)) != 1:
            raise EspnError("Verified live acquisition execution requires exactly one add or one drop")
        player_id = add_player_id if add_player_id is not None else drop_player_id
        if type(player_id) is not int or player_id == 0:
            raise EspnError("ESPN player IDs must be nonzero integers")

        snapshot = await self.snapshot()
        roster = {player["player_id"]: player for player in snapshot["roster"]}
        if drop_player_id is not None:
            player = roster.get(drop_player_id)
            if player is None:
                raise EspnError("The player to drop is no longer on your ESPN roster")
            if player.get("droppable") is not True:
                raise EspnError(f"ESPN does not confirm that {player['name']} is droppable")
            if player.get("lock_status") != "UNLOCKED":
                raise EspnError(f"{player['name']} is locked and cannot be dropped")
            return {
                "scoring_period_id": snapshot["scoring_period_id"],
                "operation": "drop",
                "drop": {"player_id": player["player_id"], "name": player["name"]},
            }

        if add_player_id in roster:
            raise EspnError("The player to add is already on your ESPN roster")
        available = await self.available_players(limit=50)
        player = next(
            (candidate for candidate in available["players"]
             if candidate["player_id"] == add_player_id),
            None,
        )
        if player is None:
            raise EspnError("The player to add is not in the current verified ESPN player results")
        if player.get("availability_status") != "FREEAGENT":
            raise EspnError(f"{player['name']} requires a waiver claim rather than a free-agent add")
        if player.get("lock_status") != "UNLOCKED":
            raise EspnError(f"{player['name']} is locked and cannot be added")
        return {
            "scoring_period_id": snapshot["scoring_period_id"],
            "operation": "add",
            "add": {"player_id": player["player_id"], "name": player["name"]},
        }

    async def _post_add_drop(self, preview: dict) -> None:
        if preview["operation"] == "add":
            body = {
                "isLeagueManager": False,
                "teamId": self.team_id,
                "type": "FREEAGENT",
                "scoringPeriodId": preview["scoring_period_id"],
                "executionType": "EXECUTE",
                "items": [{
                    "playerId": preview["add"]["player_id"],
                    "type": "ADD",
                    "toTeamId": self.team_id,
                }],
            }
        else:
            body = {
                "isLeagueManager": False,
                "teamId": self.team_id,
                "type": "ROSTER",
                "memberId": self._swid,
                "scoringPeriodId": preview["scoring_period_id"],
                "executionType": "EXECUTE",
                "items": [{
                    "playerId": preview["drop"]["player_id"],
                    "type": "DROP",
                    "fromTeamId": self.team_id,
                }],
            }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://fantasy.espn.com",
            "Referer": (
                "https://fantasy.espn.com/football/team"
                f"?leagueId={self.league_id}&teamId={self.team_id}&seasonId={self.season}"
            ),
            "x-fantasy-platform": "espn-fantasy-web",
            "x-fantasy-source": "kona",
        }
        try:
            async with asyncio.timeout(35):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False,
                    cookies={"espn_s2": self._espn_s2, "SWID": self._swid},
                ) as client:
                    response = await client.post(self._write_url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
            raise EspnUnknownOutcome(
                "ESPN did not return a definitive transaction response; reconcile before retrying"
            ) from None
        if response.status_code >= 500:
            raise EspnUnknownOutcome(
                f"ESPN HTTP {response.status_code} did not provide a definitive transaction outcome"
            )
        if response.status_code not in (200, 201, 202):
            detail = {
                400: "ESPN rejected the transaction request",
                401: "ESPN authentication expired",
                403: "ESPN denied the transaction request",
                409: "ESPN reported a transaction conflict",
            }.get(response.status_code, "ESPN transaction request failed")
            raise EspnError(f"{detail} (HTTP {response.status_code})")

    async def execute_add_drop(self, expected: dict) -> dict:
        fresh = await self.preview_add_drop(
            add_player_id=expected.get("add", {}).get("player_id"),
            drop_player_id=expected.get("drop", {}).get("player_id"),
        )
        if fresh != expected:
            raise EspnError("Player availability changed after preview; refresh and review the action again")
        uncertain = None
        try:
            await self._post_add_drop(expected)
        except EspnUnknownOutcome as exc:
            uncertain = exc
        target_id = (expected.get("add") or expected.get("drop"))["player_id"]
        should_exist = expected["operation"] == "add"
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(1)
            try:
                snapshot = await self.snapshot()
            except EspnError:
                raise EspnUnknownOutcome(
                    "The transaction may have succeeded, but ESPN roster readback failed; do not retry"
                ) from None
            exists = any(player["player_id"] == target_id for player in snapshot["roster"])
            if exists is should_exist:
                return {
                    "status": "completed",
                    "operation": expected["operation"],
                    "player_id": target_id,
                    "verified_at": snapshot["fetched_at"],
                    "scoring_period_id": snapshot["scoring_period_id"],
                }
        if uncertain is not None:
            raise uncertain
        raise EspnUnknownOutcome(
            "ESPN accepted the request but roster readback did not verify the transaction; do not retry"
        )

    async def available_players(self, position: str | None = None, limit: int = 25) -> dict:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise EspnError("Player search limit must be between 1 and 50")
        if position is not None:
            if not isinstance(position, str) or position.upper() not in SLOTS:
                raise EspnError("Unsupported player position")
            position = position.upper()
        _, _, period = await self._context()
        filters = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                               "filterSlotIds": {"value": [SLOTS[position]] if position else []},
                               "limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
        data = await self._get(["kona_player_info"], period, filters)
        players = data.get("players")
        if not isinstance(players, list):
            raise EspnError("ESPN did not return available-player data")
        return {**self._base(period), "position": position,
                "players": [self._player(player, period) for player in players[:limit]],
                "limit": limit, "complete_pool": False,
                "limitations": ["Availability is a read snapshot, not acquisition eligibility",
                                "Player locks are unknown; projections are null when not supplied"]}

    async def transactions(self) -> dict:
        _, _, period = await self._context()
        filters = {"transactions": {"filterType": {"value": ["FREEAGENT", "WAIVER", "WAIVER_ERROR"]}}}
        data = await self._get(["mTransactions2"], period, filters)
        raw = data.get("transactions")
        if not isinstance(raw, list):
            return {**self._base(period), "transactions": [], "available": False,
                    "pending_waivers": None, "pending_trades": None, "complete": False,
                    "limitations": ["ESPN did not expose transaction data; no empty-history claim is made"]}
        normalized = []
        for transaction in raw:
            if not isinstance(transaction, dict):
                raise EspnError("ESPN returned a malformed transaction")
            if transaction.get("teamId") != self.team_id:
                continue
            items = transaction.get("items")
            normalized.append({
                **_fields(transaction, ("type", "status", "scoringPeriodId", "processDate", "proposedDate", "bidAmount")),
                "team_id": self.team_id,
                "items": [_fields(item, ("type", "playerId")) for item in items[:30]
                          if isinstance(item, dict)] if isinstance(items, list) else None,
            })
            if len(normalized) >= 50:
                break
        return {**self._base(period), "transactions": normalized, "available": True,
                "history_truncated": len(raw) > 50,
                "pending_waivers": None, "pending_trades": None, "complete": False,
                "limitations": ["At most 50 current-period FREEAGENT/WAIVER/WAIVER_ERROR records for this team",
                                "Pending waivers, trades, and complete accounting are unverified"]}

    async def test_connection(self) -> str:
        await self._context()
        return (
            "ESPN access and configured team ownership verified; direct owner-confirmed lineup "
            "swaps are available, while agent writes and all other transaction types remain disabled"
        )
