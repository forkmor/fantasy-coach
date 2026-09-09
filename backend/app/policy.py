from datetime import datetime, timezone

from .models import ActionInput, Policy


def check_policy(action: ActionInput, policy: Policy, snapshot: dict,
                 expected_team_id: int = 4) -> list[str]:
    """Return deterministic denials, not a claim that ESPN will accept a move."""
    denials = []
    if action.action not in policy.allowed_actions:
        denials.append("This action is not enabled in the owner policy")
    try:
        stamp = datetime.fromisoformat(snapshot["fetched_at"])
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
    except (KeyError, TypeError, ValueError):
        denials.append("A valid timestamped ESPN snapshot is required")
    else:
        if age < -5 or age > 120:
            denials.append("ESPN state is stale; refresh before evaluating a move")
    team_id = snapshot.get("team_id")
    if (team_id != expected_team_id
            or snapshot.get("league_id") != 656212638
            or snapshot.get("season") != 2026):
        denials.append("Snapshot does not match the configured ESPN team")
    roster = {player["player_id"]: player for player in snapshot.get("roster", [])}
    outgoing = set(action.outgoing_player_ids)
    if action.drop_player_id is not None:
        outgoing.add(action.drop_player_id)
    if outgoing & set(policy.protected_player_ids):
        denials.append("A protected player cannot be dropped or traded away")
    if outgoing - roster.keys():
        denials.append("An outgoing player is not on this team's roster")
    if action.add_player_id in roster:
        denials.append("The player to add is already on this team's roster")
    if action.add_player_id and policy.max_adds_per_week == 0:
        denials.append("Adding players is disabled by the weekly cap")
    if action.drop_player_id and policy.max_drops_per_week == 0:
        denials.append("Dropping players is disabled by the weekly cap")
    if action.bid is not None:
        if action.bid > policy.max_bid:
            denials.append("Bid exceeds the per-claim limit")
        if action.bid > policy.waiver_budget - policy.waiver_reserve:
            denials.append("Bid would violate the configured waiver reserve")
    if action.action == "set_lineup":
        for move in action.moves:
            player = roster.get(move.player_id)
            if player is None:
                denials.append(f"Player {move.player_id} is not on the roster")
            elif move.slot_id not in player.get("eligible_slots", []):
                denials.append(f"Player {move.player_id} is not eligible for slot {move.slot_id}")
    if action.action in {"trade_propose", "trade_accept"}:
        if policy.max_trades_per_week == 0:
            denials.append("Trades are disabled by the weekly cap")
        if action.other_team_id == team_id:
            denials.append("Cannot trade with the managed team itself")
        if action.action == "trade_propose":
            players = action.outgoing_player_ids + action.incoming_player_ids
            if len(players) > policy.max_trade_players:
                denials.append("Trade exceeds the player-count limit")
            if any(str(player) not in policy.trade_values for player in players):
                denials.append("Owner-supplied trade values are required for every player")
            else:
                outgoing_value = sum(policy.trade_values[str(player)] for player in action.outgoing_player_ids)
                incoming_value = sum(policy.trade_values[str(player)] for player in action.incoming_player_ids)
                minimum = outgoing_value * (1 - policy.max_trade_value_loss_pct / 100)
                if incoming_value < minimum:
                    denials.append("Trade exceeds the owner-configured value-loss tolerance")
    return denials


def unresolved_checks(action: ActionInput) -> list[str]:
    common = [
        "Current ESPN lock times, transaction deadlines, and full league legality",
        "Complete transaction history and atomic pending-commitment/cap accounting",
    ]
    if action.action not in {"set_lineup", "add_drop"}:
        common.insert(0, "Authenticated write contract and authoritative result reconciliation")
    if action.action in {"add_drop", "waiver_submit"}:
        common.append("Current player availability, roster capacity, and waiver rules/budget")
    if action.action.startswith("trade_"):
        common.append("Authoritative trade status, assets, ownership, and pending commitments")
    return common


def check_acquisition_limits(action: ActionInput, policy: Policy, transactions: dict) -> list[str]:
    if action.action != "add_drop":
        return []
    if not transactions.get("available") or transactions.get("history_truncated"):
        return ["Complete current-period ESPN transaction accounting is required"]
    adds = drops = 0
    for transaction in transactions.get("transactions", []):
        if transaction.get("status") != "EXECUTED":
            continue
        for item in transaction.get("items") or []:
            adds += item.get("type") == "ADD"
            drops += item.get("type") == "DROP"
    denials = []
    if action.add_player_id is not None and adds >= policy.max_adds_per_week:
        denials.append("The configured weekly add limit has been reached")
    if action.drop_player_id is not None and drops >= policy.max_drops_per_week:
        denials.append("The configured weekly drop limit has been reached")
    return denials
