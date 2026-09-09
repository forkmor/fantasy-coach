import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def policy_data():
    return {
        "allowed_actions": ["set_lineup", "add_drop", "waiver_submit", "waiver_cancel",
                            "trade_propose", "trade_accept", "trade_reject", "trade_cancel"],
        "protected_player_ids": [1],
        "max_adds_per_week": 3, "max_drops_per_week": 3, "max_trades_per_week": 1,
        "max_bid": 10, "waiver_budget": 100, "waiver_reserve": 20,
        "max_trade_players": 4, "max_trade_value_loss_pct": 5.0,
        "trade_values": {"1": 30.0, "2": 20.0, "3": 25.0},
        "max_tool_calls": 5, "max_output_tokens": 1024, "max_run_seconds": 30,
        "max_runs_per_day": 5,
    }
