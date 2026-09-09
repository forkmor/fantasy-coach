import hashlib
import asyncio
from typing import Callable

from pydantic import Field, ValidationError

from .espn import CAPABILITIES, EspnError, EspnUnknownOutcome
from .models import ActionInput, Policy, StrictModel
from .policy import check_acquisition_limits, check_policy, unresolved_checks
from .storage import Store, encode


class ToolError(Exception):
    pass


class EmptyInput(StrictModel):
    pass


class PlayerSearch(StrictModel):
    position: str | None = Field(default=None, max_length=10)
    limit: int = Field(default=25, ge=1, le=50)


class IntentInput(StrictModel):
    intent_id: str = Field(min_length=1, max_length=100)


TOOL_MODELS = {
    "get_team": (EmptyInput, "Read current roster, league scoring, slots and ESPN context."),
    "search_available_players": (PlayerSearch, "Read available players, bounded to at most 50 results."),
    "get_transactions": (EmptyInput, "Read ESPN transaction and pending waiver/trade context."),
    "get_policy": (EmptyInput, "Read owner limits and currently verified capabilities. These are not editable."),
    "plan_action": (ActionInput, "Record a proposed move with deterministic checks. Dry-run is not ESPN acceptance."),
    "execute_action": (IntentInput, "Request execution of an intent. The backend enforces policy and capability gates."),
    "get_action_status": (IntentInput, "Read the recorded result of an action intent from this run."),
}


def definitions() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": name, "description": description, "parameters": model.model_json_schema(),
        }}
        for name, (model, description) in TOOL_MODELS.items()
    ]


class ToolRegistry:
    def __init__(self, store: Store, espn_factory: Callable, run_id: str,
                 league_id: int = 656212638, team_id: int = 4, season: int = 2026):
        self.store = store
        self.espn_factory = espn_factory
        self.run_id = run_id
        self.league_id = league_id
        self.team_id = team_id
        self.season = season

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in TOOL_MODELS:
            raise ToolError("Unknown tool")
        try:
            parsed = TOOL_MODELS[name][0].model_validate(arguments)
        except ValidationError as exc:
            raise ToolError("Tool arguments do not match the declared schema") from exc
        settings = self.store.get("settings", {})
        run = self.store.run(self.run_id)
        if settings.get("paused", True) or not run or run["status"] != "running":
            raise ToolError("Agent execution is paused or this run is no longer active")
        if name == "get_policy":
            return {"policy": self.store.get("policy"), "capabilities": CAPABILITIES,
                    "mode": run["mode"], "scope": {
                        "league_id": self.league_id,
                        "team_id": self.team_id,
                        "season": self.season,
                    },
                    "accounting": "Live execution unavailable until complete league accounting is verified"}
        if name == "get_team":
            snapshot = await self.espn_factory().snapshot()
            self.store.set("last_snapshot", snapshot)
            return snapshot
        if name == "search_available_players":
            return await self.espn_factory().available_players(parsed.position, parsed.limit)
        if name == "get_transactions":
            return await self.espn_factory().transactions()
        if name == "plan_action":
            return await self._plan(parsed, run)
        if name in {"execute_action", "get_action_status"}:
            intent = self.store.action(parsed.intent_id)
            if intent is None or intent["run_id"] != self.run_id:
                raise ToolError("Action intent does not belong to this run")
            if name == "get_action_status":
                return intent
            return await self._execute(intent, run)
        raise ToolError("Tool has no registered handler")

    async def _plan(self, action: ActionInput, run: dict) -> dict:
        policy_data = self.store.get("policy")
        if policy_data is None:
            raise ToolError("Complete the owner policy before planning actions")
        policy_version = self.store.get("policy_version", 0)
        snapshot = await self.espn_factory().snapshot()
        denials = check_policy(
            action, Policy.model_validate(policy_data), snapshot, self.team_id,
        )
        payload = action.model_dump()
        fingerprint = hashlib.sha256(encode({
            "payload": {key: value for key, value in payload.items() if key != "reason"},
            "period": snapshot.get("scoring_period_id"), "policy_version": policy_version,
        }).encode()).hexdigest()
        for existing in self.store.actions():
            if existing["run_id"] == self.run_id and existing["fingerprint"] == fingerprint:
                return existing
        status = "blocked" if denials else "dry_run"
        result = {
            "submitted": False, "denials": denials,
            "unresolved_checks": unresolved_checks(action),
            "detail": "Proposal only. No ESPN mutation was submitted. League legality is not fully verified.",
        }
        intent = self.store.add_action(run["id"], payload, status, result, policy_version, fingerprint)
        self.store.event("action_planned", {"intent_id": intent["id"], **result}, run["id"])
        return intent

    async def _execute(self, intent: dict, run: dict) -> dict:
        if run["mode"] != "live":
            raise ToolError("This run is proposal-only; live execution was not enabled by the owner")
        capability = next(
            (item for item in CAPABILITIES if item["action"] == intent["action"]), None,
        )
        if capability is None or not capability["enabled"]:
            raise ToolError(f"Live {intent['action']} execution is not verified")
        if intent["status"] != "dry_run":
            raise ToolError(f"This action intent is already {intent['status']}")
        policy_data = self.store.get("policy")
        if policy_data is None:
            raise ToolError("Owner policy is unavailable")
        policy_version = self.store.get("policy_version", 0)
        if intent["policy_version"] != policy_version:
            raise ToolError("Owner policy changed after this action was planned")
        policy = Policy.model_validate(policy_data)
        snapshot = await self.espn_factory().snapshot()
        action = ActionInput.model_validate(intent["payload"])
        denials = check_policy(action, policy, snapshot, self.team_id)
        if action.action == "add_drop":
            denials.extend(check_acquisition_limits(
                action, policy, await self.espn_factory().transactions(),
            ))
        if denials:
            self.store.update_action(
                intent["id"], expected_status="dry_run", status="blocked",
                result={"submitted": False, "denials": denials,
                        "detail": "Fresh policy or ESPN state blocked this action"},
            )
            raise ToolError("Fresh policy or ESPN state blocked this action")
        expected = None
        if action.action == "set_lineup":
            try:
                expected = await self.espn_factory().preview_lineup_moves(
                    [move.model_dump() for move in action.moves],
                )
            except EspnError as exc:
                self.store.update_action(
                    intent["id"], expected_status="dry_run", status="blocked",
                    result={"submitted": False, "denials": [str(exc)],
                            "detail": "The proposed lineup shape is not a verified live operation"},
                )
                raise ToolError(str(exc)) from exc
        elif action.action == "add_drop":
            try:
                expected = await self.espn_factory().preview_add_drop(
                    action.add_player_id, action.drop_player_id,
                )
            except EspnError as exc:
                self.store.update_action(
                    intent["id"], expected_status="dry_run", status="blocked",
                    result={"submitted": False, "denials": [str(exc)],
                            "detail": "The proposed acquisition is not a verified live operation"},
                )
                raise ToolError(str(exc)) from exc
        self.store.update_action(
            intent["id"], expected_status="dry_run", status="submitting",
            result={"submitted": True, "detail": "Submitting once; automatic mutation retries are disabled"},
        )
        try:
            if action.action == "set_lineup":
                result = await self.espn_factory().execute_lineup_swap(expected)
            elif action.action == "add_drop":
                result = await self.espn_factory().execute_add_drop(expected)
            else:
                raise ToolError(f"Live {action.action} execution is not verified")
        except asyncio.CancelledError:
            self.store.update_action(
                intent["id"], expected_status="submitting", status="unknown",
                result={"submitted": True,
                        "detail": "Execution was interrupted after submission began; check ESPN before retrying"},
            )
            self.store.event("action_unknown", {"intent_id": intent["id"]}, self.run_id)
            raise
        except EspnUnknownOutcome as exc:
            updated = self.store.update_action(
                intent["id"], expected_status="submitting", status="unknown",
                result={"submitted": True, "detail": str(exc)},
            )
            self.store.event("action_unknown", {"intent_id": intent["id"]}, self.run_id)
            return updated
        except EspnError as exc:
            self.store.update_action(
                intent["id"], expected_status="submitting", status="failed",
                result={"submitted": True, "detail": str(exc)},
            )
            self.store.event("action_failed", {"intent_id": intent["id"]}, self.run_id)
            raise ToolError(str(exc)) from exc
        updated = self.store.update_action(
            intent["id"], expected_status="submitting", status="completed",
            result={"submitted": True, **result},
        )
        self.store.event("action_completed", {"intent_id": intent["id"], **result}, self.run_id)
        return updated
