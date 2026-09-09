import math
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ActionName = Literal[
    "set_lineup", "add_drop", "waiver_submit", "waiver_cancel",
    "trade_propose", "trade_accept", "trade_reject", "trade_cancel",
]
ProviderName = Literal["grok", "gemini"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Settings(StrictModel):
    provider: ProviderName = "grok"
    model: str = Field(default="", max_length=120, pattern=r"^[a-zA-Z0-9._/-]*$")
    mode: Literal["dry_run", "live"] = "dry_run"
    deep_research: bool = False
    paused: bool = True


class Policy(StrictModel):
    allowed_actions: list[ActionName] = Field(max_length=8)
    protected_player_ids: list[int] = Field(max_length=200)
    max_adds_per_week: int = Field(ge=0, le=100)
    max_drops_per_week: int = Field(ge=0, le=100)
    max_trades_per_week: int = Field(ge=0, le=50)
    max_bid: int = Field(ge=0, le=100000)
    waiver_budget: int = Field(ge=0, le=100000)
    waiver_reserve: int = Field(ge=0, le=100000)
    max_trade_players: int = Field(ge=0, le=20)
    max_trade_value_loss_pct: float = Field(ge=0, le=100)
    trade_values: dict[str, float] = Field(max_length=2000)
    max_tool_calls: int = Field(ge=1, le=100)
    max_output_tokens: int = Field(ge=128, le=32768)
    max_run_seconds: int = Field(ge=10, le=1800)
    max_runs_per_day: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def consistent_limits(self):
        if self.waiver_reserve > self.waiver_budget:
            raise ValueError("Waiver reserve cannot exceed the configured waiver budget")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("Action permissions must be unique")
        if any(player == 0 for player in self.protected_player_ids):
            raise ValueError("Protected player IDs must be nonzero (D/ST IDs may be negative)")
        if any(not re.fullmatch(r"-?[1-9][0-9]*", key) or value < 0
               for key, value in self.trade_values.items()):
            raise ValueError("Trade values require nonzero ESPN player IDs and nonnegative values")
        if any(not math.isfinite(value) for value in self.trade_values.values()):
            raise ValueError("Trade values must be finite")
        return self


class Schedule(StrictModel):
    enabled: bool = False
    timezone: str = "America/Chicago"
    interval_minutes: int = Field(default=60, ge=5, le=10080)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class CredentialsInput(StrictModel):
    name: Literal["grok", "gemini", "espn_s2", "swid"]
    value: str = Field(min_length=1, max_length=16000)

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Credential must not contain whitespace padding or control characters")
        return value


class LoginInput(StrictModel):
    token: str = Field(min_length=1, max_length=200)


class EspnConnectInput(StrictModel):
    espn_s2: str = Field(min_length=1, max_length=16000)
    swid: str = Field(min_length=1, max_length=80)

    @field_validator("espn_s2", "swid")
    @classmethod
    def clean_cookie(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("ESPN cookies must not contain whitespace padding or control characters")
        return value


class TestConnectionInput(StrictModel):
    target: Literal["grok", "gemini", "espn"]


class RunInput(StrictModel):
    objective: str = Field(
        default="Review my team and improve the lineup and roster within my configured limits.",
        min_length=1, max_length=4000,
    )


class LineupMove(StrictModel):
    player_id: int = Field(description="Nonzero ESPN player ID; D/ST IDs may be negative")
    slot_id: int = Field(ge=0, le=100)

    @field_validator("player_id")
    @classmethod
    def nonzero_player(cls, value: int) -> int:
        if value == 0:
            raise ValueError("ESPN player IDs must be nonzero")
        return value


class LineupSwapPreviewInput(StrictModel):
    starter_player_id: int
    bench_player_id: int

    @model_validator(mode="after")
    def distinct_nonzero_players(self):
        if self.starter_player_id == 0 or self.bench_player_id == 0:
            raise ValueError("ESPN player IDs must be nonzero")
        if self.starter_player_id == self.bench_player_id:
            raise ValueError("Choose two different players")
        return self


class LineupSwapConfirmInput(StrictModel):
    operation_id: str = Field(
        min_length=36, max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )


class AddDropPreviewInput(StrictModel):
    add_player_id: int | None = None
    drop_player_id: int | None = None

    @model_validator(mode="after")
    def exactly_one_player(self):
        values = [self.add_player_id, self.drop_player_id]
        if sum(value is not None for value in values) != 1:
            raise ValueError("Choose exactly one player to add or drop")
        if any(value == 0 for value in values if value is not None):
            raise ValueError("ESPN player IDs must be nonzero")
        return self


class AddDropConfirmInput(StrictModel):
    operation_id: str = Field(
        min_length=36, max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )


class ActionInput(StrictModel):
    action: ActionName
    reason: str = Field(min_length=1, max_length=2000)
    moves: list[LineupMove] = Field(default_factory=list, max_length=100)
    add_player_id: int | None = Field(default=None, description="Nonzero ESPN ID; D/ST IDs may be negative")
    drop_player_id: int | None = Field(default=None, description="Nonzero ESPN ID; D/ST IDs may be negative")
    bid: int | None = Field(default=None, ge=0)
    transaction_id: str | None = Field(default=None, min_length=1, max_length=100)
    other_team_id: int | None = Field(default=None, gt=0)
    outgoing_player_ids: list[int] = Field(default_factory=list, max_length=20)
    incoming_player_ids: list[int] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def action_fields(self):
        required = {
            "set_lineup": {"moves"},
            "add_drop": set(),
            "waiver_submit": {"add_player_id", "bid"},
            "waiver_cancel": {"transaction_id"},
            "trade_propose": {"other_team_id", "outgoing_player_ids", "incoming_player_ids"},
            "trade_accept": {"transaction_id"},
            "trade_reject": {"transaction_id"},
            "trade_cancel": {"transaction_id"},
        }
        allowed = {
            "set_lineup": {"moves"},
            "add_drop": {"add_player_id", "drop_player_id"},
            "waiver_submit": {"add_player_id", "drop_player_id", "bid"},
            "waiver_cancel": {"transaction_id"},
            "trade_propose": {"other_team_id", "outgoing_player_ids", "incoming_player_ids"},
            "trade_accept": {"transaction_id"},
            "trade_reject": {"transaction_id"},
            "trade_cancel": {"transaction_id"},
        }
        present = {name for name in type(self).model_fields
                   if name not in {"action", "reason"} and getattr(self, name) not in (None, [])}
        if not required[self.action] <= present or not present <= allowed[self.action]:
            raise ValueError("Arguments do not match the action type")
        if self.action == "add_drop" and not present:
            raise ValueError("An add/drop needs a player to add or drop")
        if self.add_player_id == 0 or self.drop_player_id == 0:
            raise ValueError("ESPN player IDs must be nonzero")
        if self.add_player_id is not None and self.add_player_id == self.drop_player_id:
            raise ValueError("Cannot add and drop the same player")
        for ids in (self.outgoing_player_ids, self.incoming_player_ids):
            if any(player == 0 for player in ids) or len(set(ids)) != len(ids):
                raise ValueError("Trade player IDs must be nonzero and unique")
        if set(self.outgoing_player_ids) & set(self.incoming_player_ids):
            raise ValueError("The same player cannot be on both sides of a trade")
        if len({move.player_id for move in self.moves}) != len(self.moves):
            raise ValueError("Lineup player IDs must be unique")
        return self
