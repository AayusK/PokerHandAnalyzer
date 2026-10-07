"""Game-theory-optimal (Nash equilibrium) push/fold charts for short stacks, and comparing
real decisions with them.

The shared types (GtoDecision, verdict_for, range_percent, COMPAT, EQUITY) are the contract
charts.py, db.py, leaks.py and the dashboard build on. ``solve`` computes the chip-EV
equilibrium of a push-or-fold spot from the preflop equity matrix; ``nash_decisions`` finds
the spot a real decision was made in and compares the decision with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from .parser import Action, Hand, Player
from .ranges import CLASSES, CLASS_INDEX, combos, hand_class

DATA_FILE = Path(__file__).with_name("data") / "preflop_equity.json"

# Push/fold comparisons apply at or below this effective stack (big blinds).
NASH_MAX_BB = 15.0

# Verdict thresholds on the chart frequency of the action that was taken.
AGREE_FREQ = 0.5      # the chart takes this action at least half the time
MIXED_FREQ = 0.1      # the chart takes it sometimes

# Hands with these warnings (from the parser) have forced action or boards the comparisons
# don't model; neither Nash nor user charts are compared on them.
UNSUPPORTED_WARNINGS = ("bombPot", "straddleSeat", "doubleBoard")

# Weight of each class in a range: number of combos out of 1326.
COMBO_WEIGHTS = np.array([combos(c) for c in CLASSES], dtype=float)


def verdict_for(action_freq: float) -> str:
    """'agrees' | 'mixed' | 'deviates' from how often the chart takes the action that was taken."""
    if action_freq >= AGREE_FREQ:
        return "agrees"
    if action_freq >= MIXED_FREQ:
        return "mixed"
    return "deviates"


def range_percent(freqs: np.ndarray | list[float]) -> float:
    """Combo-weighted share of all hands (0-100) for per-class frequencies in CLASSES order."""
    return float(100.0 * np.dot(np.asarray(freqs, dtype=float), COMBO_WEIGHTS) / 1326.0)


@dataclass
class GtoDecision:
    """One preflop decision compared with a chart (computed Nash push/fold, or a user chart)."""
    hand_id: str
    seq: int                      # Action.seq of the decision
    player_id: str
    source: str                   # "nash" for the computed push/fold equilibrium, else the user chart's name
    situation: str                # "push" (first in, shove or fold) | "call" (facing a lone shove) | "rfi" | "vs_open"
    position: str
    eff_stack_bb: float
    players_behind: int           # players still to act behind the decision maker (push/rfi), or behind the shover (call)
    hand_class: str               # e.g. "A5s"
    action: str                   # what was done: push/fold/call/limp/raise/3bet/allin
    chart_action: str             # the chart's most frequent action for this class
    freqs: dict[str, float]       # chart frequency of every action for this class (fold included); sums to 1
    action_freq: float            # chart frequency of the action that was taken
    verdict: str                  # "agrees" | "mixed" | "deviates" | "off-chart" (action the chart doesn't model)
    ev_loss_bb: float | None      # Nash only: EV of the best action minus EV of the action taken (>= 0), in BB
    title: str                    # human description of the spot, e.g. "CO first in · 8.2 BB · 3 left to act"
    grid: dict[str, list[float]] = field(default_factory=dict)  # action -> 169 frequencies in CLASSES order
    evs: list[float] | None = None  # Nash only: EV(push or call) - EV(fold) per class in CLASSES order, in BB

    def to_row(self) -> dict:
        """Flat dict for storage; dict/list fields as JSON text."""
        return {
            "hand_id": self.hand_id, "seq": self.seq, "player_id": self.player_id, "source": self.source,
            "situation": self.situation, "position": self.position, "eff_stack_bb": self.eff_stack_bb,
            "players_behind": self.players_behind, "hand_class": self.hand_class, "action": self.action,
            "chart_action": self.chart_action, "freqs": json.dumps(self.freqs), "action_freq": self.action_freq,
            "verdict": self.verdict, "ev_loss_bb": self.ev_loss_bb, "title": self.title,
            "grid": json.dumps({k: [round(v, 4) for v in vals] for k, vals in self.grid.items()}),
            "evs": json.dumps([round(v, 4) for v in self.evs]) if self.evs is not None else None,
        }


# ---------------------------------------------------------------------------------------
# Preflop equity matrix (built by scripts/build_equity_matrix.py)


def _load_equity() -> np.ndarray:
    data = json.loads(DATA_FILE.read_text())
    if data["classes"] != CLASSES:
        raise ValueError(f"{DATA_FILE} was built for a different class order; rebuild it")
    return np.array(data["equity"], dtype=float)


def _compat_counts() -> np.ndarray:
    """COMPAT[i][j]: combos of class j that share no card with one given combo of class i."""
    suits = "shdc"

    def class_combos(cls: str) -> list[frozenset[str]]:
        r1, r2 = cls[0], cls[1]
        if len(cls) == 2:
            return [frozenset({r1 + a, r1 + b}) for k, a in enumerate(suits) for b in suits[k + 1:]]
        if cls[2] == "s":
            return [frozenset({r1 + s, r2 + s}) for s in suits]
        return [frozenset({r1 + a, r2 + b}) for a in suits for b in suits if a != b]

    all_combos = [class_combos(c) for c in CLASSES]
    out = np.zeros((len(CLASSES), len(CLASSES)))
    for i, ci in enumerate(all_combos):
        rep = ci[0]
        for j, cj in enumerate(all_combos):
            out[i, j] = sum(1 for c in cj if not (c & rep))
    return out


EQUITY = _load_equity() if DATA_FILE.exists() else None
COMPAT = _compat_counts()


# ---------------------------------------------------------------------------------------
# Nash push/fold solver
#
# Model (amounts in big blinds, chip EV, no ICM): the first player in (the pusher) folds or
# shoves all-in; the k players behind, in preflop order, each call or fold. Simplifications:
# - push or fold only: no limps or smaller raises;
# - single caller: once someone calls, everyone after folds (equilibrium overcall ranges are
#   very tight);
# - callers' hands are independent given the pusher's hand: card removal is applied between
#   the pusher and each caller, not between callers. This overstates the chance that everyone
#   folds (by about a point with 8 behind), so early push ranges are slightly too wide and
#   marginal pushes' EVs are up to ~0.07 BB too high.
#
# Gains are measured against folding. With e_j = min(S_p, S_j):
#   pusher, all fold:      wins W0 = (N-1)a + dead + sum(q)
#   pusher, j calls first: pot_j = N a + dead + 2 e_j + sum(q) - q_j, nets eq_j(h) pot_j - a - e_j
#   Gain_push(h)   = P0(h) W0 + sum_j Pj(h) (eq_j(h) pot_j - a - e_j) + a + p_p
#   Gain_call_j(c) = eq_c pot_j - e_j + q_j
# Caller j's view of the push range is weighted by the chance that the callers before j fold
# with each pushing hand (card removal), so every gain is the exact derivative of that
# player's EV in the model and the game is consistent.

# The solver stops when the averaged strategies are this close to an equilibrium: no player
# gains more than SOLVE_TOLERANCE BB by a best response (callers measured per time they face
# the shove), and every class whose action is worth more than CLEAR_EV_BB is played purely to
# within PURE_TOLERANCE.
SOLVE_TOLERANCE = 2e-5
CLEAR_EV_BB = 0.05
PURE_TOLERANCE = 0.01
SOLVE_MAX_ITERATIONS = 3000
SOLVE_CHECK_EVERY = 10
# A caller holding a class is treated as never facing the shove when its chance of doing so
# is below this share of its chance against the whole push range, as when an earlier caller
# calls almost everything. The reach left then is leftover weight from the early averaged
# iterates, and the push range it implies is noise.
UNREACHED_SHARE = 1e-3

_WEIGHTS = COMBO_WEIGHTS / 1326.0
# (169, 338): [COMPAT.T | (COMPAT * EQUITY).T] / 1225. For a range vector x over the
# opponent's classes, x @ _MIX gives per own class the chance the opponent holds the range
# (first half) and that chance times own equity against it (second half).
_MIX = np.hstack([COMPAT.T, (COMPAT * EQUITY).T]) / 1225.0 if EQUITY is not None else None
_EQ_RANDOM = (COMPAT * EQUITY).sum(axis=1) / 1225.0 if EQUITY is not None else None


@dataclass(frozen=True)
class PushFoldSpot:
    """A first-in push-or-fold spot, in big blinds. Frozen so solutions can be cached."""
    pusher_stack: float                 # S_p: chips the pusher can put in, at hand start, after the ante (incl. any blind posted)
    pusher_posted: float                # p_p: blind the pusher already posted (0 or the SB)
    caller_stacks: tuple[float, ...]    # S_j for callers in action order
    caller_posted: tuple[float, ...]    # q_j: blind each caller posted (SB / BB / 0)
    ante: float = 0.0                   # a: ante per player
    players: int = 0                    # N: players dealt in (antes); 0 means k + 1
    dead: float = 0.0                   # any other dead money

    @property
    def k(self) -> int:
        return len(self.caller_stacks)


@dataclass
class PushFoldSolution:
    spot: PushFoldSpot
    push: np.ndarray        # (169,) push frequency
    call: np.ndarray        # (k, 169) call frequency per caller
    push_ev: np.ndarray     # (169,) Gain_push vs the equilibrium callers
    call_ev: np.ndarray     # (k, 169) Gain_call vs the equilibrium push range
    exploitability: float
    iterations: int

    def push_percent(self) -> float:
        return range_percent(self.push)

    def call_percent(self, j: int) -> float:
        return range_percent(self.call[j])


class _Game:
    """Payoff constants of a spot and the two gain functions."""

    def __init__(self, spot: PushFoldSpot):
        k = spot.k
        if k == 0 or len(spot.caller_posted) != k:
            raise ValueError(f"spot needs one posted amount per caller and at least one caller: {spot}")
        if spot.pusher_stack <= 0:
            raise ValueError(f"pusher has no chips: {spot}")
        n = spot.players or k + 1
        a = spot.ante
        q = np.array(spot.caller_posted, dtype=float)
        e = np.minimum(spot.pusher_stack, np.array(spot.caller_stacks, dtype=float))
        self.fold_win = (n - 1) * a + spot.dead + q.sum()       # W0
        self.pot = n * a + spot.dead + 2 * e + q.sum() - q      # pot_j
        self.risk = a + e                                       # pusher's chips in when j calls
        self.fold_loss = a + spot.pusher_posted                 # pusher's chips in when folding
        self.call_cost = e - q                                  # chips j adds to call

    def push_gain(self, call: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Gain_push per class vs the callers' strategies, and fold_through[j, h]: the chance
        everyone before caller j folds when the pusher holds h."""
        mixed = call @ _MIX
        p_call, p_call_won = mixed[:, :169], mixed[:, 169:]     # pi_j(h), pi_j(h) * eq_j(h)
        fold_through = np.ones_like(p_call)
        fold_through[1:] = np.cumprod(1.0 - p_call[:-1], axis=0)
        all_fold = fold_through[-1] * (1.0 - p_call[-1])
        called = fold_through * (p_call_won * self.pot[:, None] - p_call * self.risk[:, None])
        return all_fold * self.fold_win + called.sum(axis=0) + self.fold_loss, fold_through

    def call_gain(self, push: np.ndarray, fold_through: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Gain_call per caller and class vs the push range, and reach[j, c]: the chance caller
        j holding c faces the push."""
        mixed = (push[None, :] * fold_through) @ _MIX
        reach, won = mixed[:, :169], mixed[:, 169:]
        plain = push @ _MIX
        reached = reach > UNREACHED_SHARE * plain[None, :169] + 1e-12
        eq = won / np.where(reached, reach, 1.0)
        if not reached.all():
            # (Practically) never reached: use the whole push range, or any two cards if there
            # is none.
            fallback = np.where(plain[:169] > 1e-12, plain[169:] / np.maximum(plain[:169], 1e-12), _EQ_RANDOM)
            eq = np.where(reached, eq, fallback[None, :])
        return eq * self.pot[:, None] - self.call_cost[:, None], reach

    def measure(self, push: np.ndarray, call: np.ndarray) -> tuple[float, bool, np.ndarray, np.ndarray]:
        """Exploitability (BB): the best-response gain of the most exploiting player, callers
        weighted by the chance they face the shove; whether the strategies meet the stopping
        rule; and both gain arrays."""
        push_ev, fold_through = self.push_gain(call)
        call_ev, reach = self.call_gain(push, fold_through)
        pusher = float(np.dot(_WEIGHTS, np.maximum(push_ev, 0.0) - push * push_ev))
        callers = ((np.maximum(call_ev, 0.0) - call * call_ev) * reach) @ _WEIGHTS
        per_shove = callers / np.maximum(reach @ _WEIGHTS, 1e-12)
        impure = max(_impurity(push, push_ev), _impurity(call, call_ev))
        done = max(pusher, float(per_shove.max())) < SOLVE_TOLERANCE and impure <= PURE_TOLERANCE
        return max(pusher, float(callers.max())), done, push_ev, call_ev


def _impurity(freq: np.ndarray, ev: np.ndarray) -> float:
    """How far classes with a clearly positive or negative EV are from always / never acting."""
    return float(max(np.max(1.0 - freq[ev > CLEAR_EV_BB], initial=0.0), np.max(freq[ev < -CLEAR_EV_BB], initial=0.0)))


def _regret_match(regrets: np.ndarray, gain: np.ndarray, current: np.ndarray) -> np.ndarray:
    """CFR+ update for a two-action (act / fold) strategy; returns the new act frequency."""
    regrets[0] = np.maximum(regrets[0] + gain * (1.0 - current), 0.0)
    regrets[1] = np.maximum(regrets[1] - gain * current, 0.0)
    total = regrets[0] + regrets[1]
    return np.where(total > 0, regrets[0] / np.where(total > 0, total, 1.0), 0.5)


@lru_cache(maxsize=1024)
def solve(spot: PushFoldSpot) -> PushFoldSolution:
    """Nash equilibrium of the push/fold game for a spot.

    CFR+ (regret matching+ with alternating updates: pusher, then all callers) on the
    per-class frequencies. The result is the average of the iterates weighted by t^2 (as in
    discounted CFR; it forgets the poor early iterates much faster than fictitious play or a
    plain average). Callers' regrets use their gain given that they face the shove, not
    weighted by how often they do, so callers behind players who call almost everything
    still converge to a best response. Runs until the stopping rule above holds, typically a
    few hundred iterations. Cached; the arrays are read-only.
    """
    if EQUITY is None:
        raise RuntimeError(f"{DATA_FILE} is missing; build it with scripts/build_equity_matrix.py")
    game = _Game(spot)
    k = spot.k
    push, call = np.full(169, 0.5), np.full((k, 169), 0.5)
    push_regrets, call_regrets = np.zeros((2, 169)), np.zeros((2, k, 169))
    push_sum, call_sum, weight = np.zeros(169), np.zeros((k, 169)), 0.0
    iterations = 0
    for t in range(1, SOLVE_MAX_ITERATIONS + 1):
        gain, fold_through = game.push_gain(call)
        push = _regret_match(push_regrets, gain, push)
        gain, _ = game.call_gain(push, fold_through)
        call = _regret_match(call_regrets, gain, call)
        push_sum += t * t * push
        call_sum += t * t * call
        weight += t * t
        iterations = t
        if t % SOLVE_CHECK_EVERY == 0 and game.measure(push_sum / weight, call_sum / weight)[1]:
            break
    push, call = push_sum / weight, call_sum / weight
    exploitability, _, push_ev, call_ev = game.measure(push, call)
    for arr in (push, call, push_ev, call_ev):
        arr.flags.writeable = False
    return PushFoldSolution(spot, push, call, push_ev, call_ev, exploitability, iterations)


def symmetric_spot(players_behind: int, stack_bb: float, ante: float = 0.0, players: int | None = None) -> PushFoldSpot:
    """Everyone has ``stack_bb`` (after the ante). With two or more players behind, the last two
    are the SB (0.5) and BB (1); with one, the pusher is the SB against the BB."""
    if players_behind < 1:
        raise ValueError("need at least one player behind")
    if players_behind == 1:
        pusher_posted, posted = 0.5, (1.0,)
    else:
        pusher_posted, posted = 0.0, (0.0,) * (players_behind - 2) + (0.5, 1.0)
    return PushFoldSpot(stack_bb, pusher_posted, (stack_bb,) * players_behind, posted, ante=ante,
                        players=players or 0)


# ---------------------------------------------------------------------------------------
# Comparing real decisions with the push/fold equilibrium


def _round_stack(chips: float, bb: int) -> float:
    """Stack in BB rounded to 0.05 (so similar spots share a cached solution)."""
    return max(0.05, round(chips / bb * 20) / 20)


def _all_in(a: Action) -> bool:
    return a.all_in or a.amount >= a.stack_before


def _spot_for(hand: Hand, pusher: Player, callers: list[Player]) -> PushFoldSpot:
    bb = hand.big_blind
    posted: dict[str, float] = {}
    for a in hand.actions:
        if a.kind in ("post_sb", "post_bb"):
            posted[a.player_id] = posted.get(a.player_id, 0.0) + a.amount / bb
    in_spot = {pusher.id} | {c.id for c in callers}
    dead = sum(v for pid, v in posted.items() if pid not in in_spot)
    return PushFoldSpot(
        pusher_stack=_round_stack(pusher.stack - hand.ante, bb),
        pusher_posted=round(posted.get(pusher.id, 0.0), 4),
        caller_stacks=tuple(_round_stack(c.stack - hand.ante, bb) for c in callers),
        caller_posted=tuple(round(posted.get(c.id, 0.0), 4) for c in callers),
        ante=round(hand.ante / bb, 4), players=len(hand.players), dead=round(dead, 4),
    )


def _preflop_order(hand: Hand) -> list[Player]:
    """Players in preflop action order: left of the big blind round to the big blind."""
    bb_seat = next((a.seat for a in hand.actions if a.kind == "post_bb"), None)
    seats = sorted(p.seat for p in hand.players)
    if bb_seat not in seats:
        return []
    i = seats.index(bb_seat)
    return [hand.at_seat(s) for s in seats[i + 1:] + seats[: i + 1]]


def nash_decisions(hand: Hand, player_id: str | None = None) -> list[GtoDecision]:
    """Compare a player's first preflop decision (default: the hero) with the Nash push/fold
    equilibrium: first in ("push") or facing a lone first-in shove ("call"), at most
    NASH_MAX_BB effective. Empty for other spots, non-Hold'em hands and unknown cards."""
    pid = player_id or hand.hero_id
    player = hand.player(pid)
    if hand.game != "holdem" or player is None or not player.cards_known or len(player.hole_cards) != 2:
        return []
    if any(w.startswith(UNSUPPORTED_WARNINGS) for w in hand.warnings):
        return []
    try:
        cls = hand_class(player.hole_cards)
    except (ValueError, IndexError):
        return []
    if cls not in CLASS_INDEX:
        return []
    d = next((a for a in hand.decisions(pid) if a.street == "preflop"), None)
    order = [p.id for p in _preflop_order(hand)]
    if d is None or pid not in order:
        return []

    if d.raises_before == 0 and d.callers_before == 0 and player.position != "BB" and d.kind != "check":
        situation, pusher = "push", player
    elif d.raises_before == 1 and d.callers_before == 0:
        r = next((a for a in reversed(hand.actions[: d.seq]) if a.street == "preflop" and a.kind == "raise"), None)
        if r is None or r.raises_before or r.callers_before or not _all_in(r):
            return []
        situation, pusher = "call", hand.player(r.player_id)
    else:
        return []
    callers = [hand.player(c) for c in order[order.index(pusher.id) + 1:]]
    if not callers or (situation == "call" and player not in callers):
        return []
    # Effective stacks for the title and storage come from the chip counts; the spot's are rounded.
    chips = {p.id: (p.stack - hand.ante) / hand.big_blind for p in [pusher, *callers]}
    if min(chips[pusher.id], max(chips[c.id] for c in callers)) > NASH_MAX_BB:
        return []
    spot = _spot_for(hand, pusher, callers)

    sol = solve(spot)
    i = CLASS_INDEX[cls]
    if situation == "push":
        freq, gain, grid, evs = float(sol.push[i]), float(sol.push_ev[i]), sol.push, sol.push_ev
        eff = min(chips[pusher.id], max(chips[c.id] for c in callers))
        if d.kind == "fold":
            action = "fold"
        elif _all_in(d) or (d.kind == "call" and d.to_act_after == 0):
            action = "push"     # a call when everyone behind is already all-in is the same as a shove
        else:
            action = "limp" if d.kind == "call" else "raise"
        title = f"{player.position} first in · {eff:.1f} BB · {spot.k} left to act"
    else:
        j = callers.index(player)
        freq, gain, grid, evs = float(sol.call[j, i]), float(sol.call_ev[j, i]), sol.call[j], sol.call_ev[j]
        eff = min(chips[pusher.id], chips[player.id])
        action = "fold" if d.kind == "fold" else "call"
        title = f"{player.position} vs {pusher.position} shove · {eff:.1f} BB"

    act = "push" if situation == "push" else "call"
    freqs = {act: round(freq, 4), "fold": round(1.0 - freq, 4)}
    if action in freqs:
        action_freq = freqs[action]
        verdict = verdict_for(action_freq)
        ev_loss = round(max(gain, 0.0) - (gain if action == act else 0.0), 4)
    else:
        action_freq, verdict, ev_loss = 0.0, "off-chart", None
    return [GtoDecision(
        hand_id=hand.id, seq=d.seq, player_id=pid, source="nash", situation=situation,
        position=player.position, eff_stack_bb=round(eff, 2), players_behind=spot.k, hand_class=cls,
        action=action, chart_action=act if freq >= 0.5 else "fold", freqs=freqs, action_freq=action_freq,
        verdict=verdict, ev_loss_bb=ev_loss, title=title, grid={act: [float(v) for v in grid]},
        evs=[float(v) for v in evs],
    )]


__all__ = [
    "CLASSES", "CLASS_INDEX", "COMBO_WEIGHTS", "COMPAT", "EQUITY", "GtoDecision", "NASH_MAX_BB",
    "PushFoldSolution", "PushFoldSpot", "nash_decisions", "range_percent", "solve", "symmetric_spot",
    "verdict_for",
]
