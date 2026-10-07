"""Leak flags for the hero: rule-based checks, and preflop decisions compared with GTO charts.

Rule-based: limping, playing outside an early-position range, folding the big blind to a
min-raise, and opening for less than all-in when very short.

Push/fold (Hold'em, at most ``NASH_MAX_BB`` effective): first-in shove-or-fold decisions and
calls of a lone first-in shove are compared with the Nash equilibrium of the push-or-fold game
for that exact spot (``gto.nash_decisions``): the hand's real stacks, blinds, antes and players
left to act, solved from a preflop equity matrix. A decision is flagged when the equilibrium
takes that action less than 10% of the time and it gave up at least ``MIN_FLAG_EV_BB`` of chip
EV. The model simplifies: chip EV with no ICM (near the bubble or a pay jump a flagged fold can
still be right), push or fold only, at most one caller (everyone behind a caller folds), and
callers' hands independent of each other.

User charts (charts.py): the first preflop decision in a raise-first-in or facing-an-open spot is
compared with the user's own charts, and an action the chart takes less than 10% of the time is
flagged as ``chart_deviation``. Those charts carry no EVs, so there is no EV threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .charts import Chart, chart_decisions, load_charts
from .gto import MIXED_FREQ, NASH_MAX_BB, GtoDecision, nash_decisions
from .parser import Hand
from .ranges import hand_class, parse_range, percentile, range_percent
from .stats import MIN_RAISE_MAX_BB

# Hands worth playing from early position (UTG, UTG+1, ...). Anything else is flagged.
EP_RANGE_TEXT = "66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo"
EP_RANGE = parse_range(EP_RANGE_TEXT)

# Big blind vs a min-raise: folding a hand in this top share of hands is flagged.
BB_DEFEND_TOP_PCT = 50.0

# Push/fold applies at or below this effective stack (the early-position check doesn't).
PUSH_FOLD_MAX_BB = NASH_MAX_BB
# Below this, opening for less than all-in is flagged as well.
SHOVE_ONLY_BB = 8.0
# A push/fold deviation is flagged only when it gives up at least this much chip EV (BB).
MIN_FLAG_EV_BB = 0.05
# Severity of a push/fold flag by chip EV lost (BB); below the last threshold it is "low".
EV_SEVERITY = ((0.5, "high"), (0.15, "medium"))


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
    "missed_shove": "Folded a Nash shove",
    "loose_shove": "Shoved outside the Nash range",
    "small_open_short": "Opened small when short",
    "loose_call_shove": "Called a shove outside the Nash range",
    "tight_fold_shove": "Folded a Nash call vs a shove",
    "chart_deviation": "Off your GTO chart",
}

# (situation, action) -> flag kind, for push/fold decisions.
_NASH_KINDS = {
    ("push", "fold"): "missed_shove",
    ("push", "push"): "loose_shove",
    ("call", "fold"): "tight_fold_shove",
    ("call", "call"): "loose_call_shove",
}

# Chart action -> (what the player did, what the chart does).
_ACTION_WORDS = {
    "fold": ("Folded", "folds"), "push": ("Shoved", "shoves"), "allin": ("Shoved", "shoves"),
    "call": ("Called", "calls"), "limp": ("Limped", "limps"), "raise": ("Raised", "raises"),
    "3bet": ("3-bet", "3-bets"),
}


def hand_flags(hand: Hand, charts: list[Chart] | None = None) -> list[Flag]:
    """Leak flags for the hero in one hand.

    ``charts`` are the user's GTO charts; None loads them from the charts directory (pass them
    in when going through many hands).
    """
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
    hero_allin = first.all_in or (first.kind in ("raise", "call") and first.amount >= first.stack_before)
    # First in, calling when everyone behind is already all-in (blinds posted all-in) is a shove.
    hero_allin = hero_allin or (unopened and first.callers_before == 0 and first.kind == "call"
                                and first.to_act_after == 0)
    # Very short and first in: anything but shove or fold is "opened small", not a limp.
    small_open = (short and unopened and first.callers_before == 0 and first.eff_stack_bb <= SHOVE_ONLY_BB
                  and first.kind in ("raise", "call") and not hero_allin)

    # Limping (completing the small blind counts).
    if unopened and first.kind == "call" and hero.position != "BB" and not hero_allin and not small_open:
        if first.callers_before == 0:
            what = "Completed the small blind" if hero.position == "SB" else f"Open-limped from {hero.position}"
        else:
            what = f"Over-limped behind {first.callers_before} limper(s) from {hero.position}"
        add("limp", "medium" if first.callers_before == 0 and hero.position != "SB" else "low",
            what + (f" with {cls}" if cls else ""))

    # Early position, outside the EP range (short stacks are judged by push/fold instead).
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

    if small_open:
        what = "Limped" if first.kind == "call" else f"Raised to {first.to_amount / hand.big_blind:g} BB"
        add("small_open_short", "low",
            f"{what} with {cls} at {first.eff_stack_bb:.1f} BB effective, {first.to_act_after} left to act; "
            "at this depth it is shove or fold")

    if holdem:
        flags += [f for d in nash_decisions(hand) if (f := _nash_flag(d))]
        if charts is None:
            charts = load_charts()[0]
        flags += [f for d in chart_decisions(hand, charts) if (f := _chart_flag(d))]
    return flags


def _pct(f: float) -> str:
    """A chart frequency as a whole percent, but with one decimal rounded away from the 10% / 90%
    flag thresholds when it is just past them, so a flagged action never reads as 10%."""
    if MIXED_FREQ - 0.005 <= f < MIXED_FREQ:
        return f"{math.floor(round(f * 1000, 6)) / 10:.1f}%"
    if 1 - MIXED_FREQ < f <= 1 - MIXED_FREQ + 0.005:
        return f"{math.ceil(round(f * 1000, 6)) / 10:.1f}%"
    return f"{f:.0%}"


def _nash_flag(d: GtoDecision) -> Flag | None:
    """A push/fold flag when the equilibrium (almost) never takes the action and it cost EV."""
    kind = _NASH_KINDS.get((d.situation, d.action))
    if kind is None or d.verdict != "deviates" or d.ev_loss_bb is None or d.ev_loss_bb < MIN_FLAG_EV_BB:
        return None
    act = "push" if d.situation == "push" else "call"
    severity = next((s for ev, s in EV_SEVERITY if d.ev_loss_bb >= ev), "low")
    return Flag(d.hand_id, kind, severity,
                f"{_ACTION_WORDS[d.action][0]} {d.hand_class} ({d.title}); the Nash push/fold chart "
                f"{_ACTION_WORDS[act][1]} it {_pct(d.freqs[act])} of the time. EV lost: {d.ev_loss_bb:.2f} BB")


def _chart_flag(d: GtoDecision) -> Flag | None:
    """A user-chart flag when the chart (almost) never takes the action that was taken."""
    if d.verdict != "deviates":
        return None
    mix = ", ".join(f"{a} {_pct(f)}" for a, f in sorted(d.freqs.items(), key=lambda kv: -kv[1]) if f >= 0.005)
    return Flag(d.hand_id, "chart_deviation", "medium",
                f"{_ACTION_WORDS[d.action][0]} {d.hand_class} ({d.title}); your chart \"{d.source}\" "
                f"{_ACTION_WORDS[d.action][1]} it {_pct(d.action_freq)} of the time ({mix})")


def all_flags(hands: list[Hand], charts: list[Chart] | None = None) -> list[Flag]:
    """Leak flags for the hero over many hands; charts are loaded once when not given."""
    if charts is None:
        charts = load_charts()[0]
    return [f for h in hands for f in hand_flags(h, charts)]
