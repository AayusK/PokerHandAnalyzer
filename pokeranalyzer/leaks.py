"""Rule-based leak flags for the hero, and short-stack push/fold checks.

The short-stack charts are approximate chip-EV ranges with no antes: "push the top X% of
hands", where X depends on how many players are left to act and the effective stack.
They ignore ICM, so near the bubble or a pay jump a flagged fold can still be right.
Hands are ranked by equity against a random hand (see ranges.py). Each check allows some
slack (``CHART_TOLERANCE``) so only clear deviations get flagged. All the numbers are
module constants so they are easy to tune.
"""

from __future__ import annotations

from dataclasses import dataclass

from .parser import Hand
from .ranges import hand_class, parse_range, percentile, range_percent
from .stats import MIN_RAISE_MAX_BB

# Hands worth playing from early position (UTG, UTG+1, ...). Anything else is flagged.
EP_RANGE_TEXT = "66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo"
EP_RANGE = parse_range(EP_RANGE_TEXT)

# Big blind vs a min-raise: folding a hand in this top share of hands is flagged.
BB_DEFEND_TOP_PCT = 50.0

# Push/fold applies at or below this effective stack.
PUSH_FOLD_MAX_BB = 15.0
# Below this, opening for less than all-in is flagged as well.
SHOVE_ONLY_BB = 8.0
# Percentile points of slack before a chart deviation is flagged.
CHART_TOLERANCE = 7.0

# Share of hands to shove at 10 BB, by number of players left to act behind you.
PUSH_PCT_AT_10BB = {1: 58, 2: 40, 3: 30, 4: 24, 5: 20, 6: 17, 7: 15, 8: 13}
# Share of hands to call a lone shove with at 10 BB, by how many players the shover had behind.
CALL_PCT_AT_10BB = {1: 37, 2: 30, 3: 25, 4: 20}
# How ranges widen or tighten with stack depth, relative to 10 BB.
PUSH_STACK_FACTOR = [(2, 3.0), (3, 1.65), (4, 1.5), (5, 1.38), (6, 1.27), (7, 1.18), (8, 1.11), (9, 1.05),
                     (10, 1.0), (11, 0.95), (12, 0.91), (13, 0.87), (14, 0.83), (15, 0.8)]
CALL_STACK_FACTOR = [(2, 3.0), (3, 2.0), (5, 1.45), (8, 1.15), (10, 1.0), (12, 0.9), (15, 0.78)]


def _interp(table: list[tuple[float, float]], x: float) -> float:
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def push_percent(players_behind: int, eff_bb: float) -> float:
    base = PUSH_PCT_AT_10BB[min(max(players_behind, 1), max(PUSH_PCT_AT_10BB))]
    return min(100.0, base * _interp(PUSH_STACK_FACTOR, eff_bb))


def call_percent(shover_players_behind: int, eff_bb: float) -> float:
    base = CALL_PCT_AT_10BB[min(max(shover_players_behind, 1), max(CALL_PCT_AT_10BB))]
    return min(100.0, base * _interp(CALL_STACK_FACTOR, eff_bb))


@dataclass
class Flag:
    hand_id: str
    kind: str
    severity: str        # "high" | "medium" | "low"
    message: str


KINDS = {
    "limp": "Limped in",
    "loose_ep": "Loose from early position",
    "bb_fold_minraise": "Folded BB to a min-raise",
    "missed_shove": "Folded a chart shove",
    "loose_shove": "Shoved outside the chart",
    "small_open_short": "Opened small when short",
    "loose_call_shove": "Called a shove too loose",
    "tight_fold_shove": "Folded to a shove too tight",
}


def hand_flags(hand: Hand) -> list[Flag]:
    """Leak flags for the hero in one hand."""
    hero = hand.hero
    if hero is None:
        return []
    pre = [a for a in hand.decisions(hero.id) if a.street == "preflop"]
    if not pre:
        return []
    first = pre[0]
    flags: list[Flag] = []

    def add(kind: str, severity: str, message: str) -> None:
        flags.append(Flag(hand.id, kind, severity, message))

    holdem = hand.game == "holdem" and hero.cards_known and len(hero.hole_cards) == 2
    cls = hand_class(hero.hole_cards) if holdem else None
    pct = percentile(cls) if cls else None
    unopened = first.raises_before == 0
    short = holdem and first.eff_stack_bb <= PUSH_FOLD_MAX_BB

    # Limping (completing the small blind counts). Very short, it is flagged as a push/fold miss instead.
    if unopened and first.kind == "call" and hero.position != "BB" \
            and not (short and first.eff_stack_bb <= SHOVE_ONLY_BB):
        if first.callers_before == 0:
            what = "Completed the small blind" if hero.position == "SB" else f"Open-limped from {hero.position}"
        else:
            what = f"Over-limped behind {first.callers_before} limper(s) from {hero.position}"
        add("limp", "medium" if first.callers_before == 0 and hero.position != "SB" else "low",
            what + (f" with {cls}" if cls else ""))

    # Early position, outside the EP range.
    if holdem and hero.position_group == "EP" and not short and first.raises_before <= 1 \
            and first.kind in ("call", "raise") and cls not in EP_RANGE:
        add("loose_ep", "medium",
            f"Played {cls} from {hero.position} ({'raise' if first.kind == 'raise' else 'call'}); "
            f"EP range is {EP_RANGE_TEXT} (~{range_percent(EP_RANGE):.0f}% of hands)")

    # Big blind folding to a min-raise.
    if hero.position == "BB" and first.raises_before == 1 and first.kind == "fold":
        raise_to = max((a.to_amount for a in hand.actions if a.seq < first.seq and a.kind == "raise"), default=0)
        if raise_to <= MIN_RAISE_MAX_BB * hand.big_blind and pct is not None and pct <= BB_DEFEND_TOP_PCT:
            add("bb_fold_minraise", "medium",
                f"Folded {cls} (top {pct:.0f}%) in the BB to a raise to {raise_to / hand.big_blind:g} BB")

    if short:
        flags += _push_fold_flags(hand, first, cls, pct)
    return flags


def _push_fold_flags(hand: Hand, first, cls: str, pct: float) -> list[Flag]:
    flags: list[Flag] = []
    eff = first.eff_stack_bb
    hero_allin = first.all_in or (first.kind in ("raise", "call") and first.amount >= first.stack_before)

    if first.raises_before == 0 and first.callers_before == 0:
        # Folded to the hero: shove or fold.
        target = push_percent(first.to_act_after, eff)
        where = f"{eff:.1f} BB effective, {first.to_act_after} left to act"
        if first.kind == "fold" and pct <= target - CHART_TOLERANCE:
            flags.append(Flag(hand.id, "missed_shove", "high",
                              f"Folded {cls} (top {pct:.0f}%) at {where}; chart shoves top {target:.0f}%"))
        elif hero_allin and pct > target + CHART_TOLERANCE:
            flags.append(Flag(hand.id, "loose_shove", "medium",
                              f"Shoved {cls} (top {pct:.0f}%) at {where}; chart shoves top {target:.0f}%"))
        elif not hero_allin and first.kind in ("raise", "call") and eff <= SHOVE_ONLY_BB:
            what = "Limped" if first.kind == "call" else f"Raised to {first.to_amount / hand.big_blind:g} BB"
            flags.append(Flag(hand.id, "small_open_short", "low",
                              f"{what} with {cls} at {where}; at this depth it is shove or fold"))
        return flags

    # Facing a single all-in with nobody else in yet.
    raises = [a for a in hand.actions if a.seq < first.seq and a.kind == "raise"]
    if first.raises_before == 1 and first.callers_before == 0 and raises and raises[-1].all_in:
        shove = raises[-1]
        target = call_percent(shove.to_act_after, eff)
        where = f"{eff:.1f} BB effective vs a shove from {hand.player(shove.player_id).position}"
        if first.kind == "fold" and pct <= target - CHART_TOLERANCE:
            flags.append(Flag(hand.id, "tight_fold_shove", "medium",
                              f"Folded {cls} (top {pct:.0f}%) at {where}; chart calls top {target:.0f}%"))
        elif first.kind in ("call", "raise") and pct > target + CHART_TOLERANCE:
            flags.append(Flag(hand.id, "loose_call_shove", "medium",
                              f"Called {cls} (top {pct:.0f}%) at {where}; chart calls top {target:.0f}%"))
    return flags


def all_flags(hands: list[Hand]) -> list[Flag]:
    return [f for h in hands for f in hand_flags(h)]
