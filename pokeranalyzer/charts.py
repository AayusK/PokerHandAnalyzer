"""User-supplied GTO charts for deeper-stack preflop spots, and comparing real decisions with them.

We can't solve deep-stack preflop spots ourselves, so these charts come from the user: JSON files
in the charts directory (``POKER_CHARTS``, default ``data/charts``) with weighted ranges per spot,
e.g. exported from a solver or training tool. Two situations are covered: raise first in
("rfi") and facing a single open raise ("vs_open"). The file format is in docs/gto-charts.md.

A bad chart file never breaks anything: ``load_charts`` skips it and reports why.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import gto
from .gto import UNSUPPORTED_WARNINGS, GtoDecision, verdict_for
from .parser import POSITION_ORDER, Action, Hand
from .ranges import CLASS_INDEX, CLASSES, hand_class, parse_weighted_range

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "charts"

# Actions a chart can give per situation; fold is whatever weight is left over.
SITUATION_ACTIONS = {
    "rfi": ("raise", "limp", "allin"),
    "vs_open": ("3bet", "call", "allin"),
}

CHART_KEYS = {"name", "source", "spots"}
SPOT_KEYS = {"situation", "position", "positions", "opener", "stack_bb", "players", "actions"}

# Reserved for the computed push/fold equilibrium (GtoDecision.source).
RESERVED_NAMES = {"nash"}

# Slack on "the action weights for a class add up to at most 1".
SUM_TOLERANCE = 1e-6


class ChartError(ValueError):
    pass


@dataclass
class ChartSpot:
    """One spot of a chart: when it applies, and the weight of each action per hand class."""
    situation: str                          # "rfi" | "vs_open"
    positions: tuple[str, ...]              # empty: any position
    openers: tuple[str, ...]                # vs_open only; empty: any opener
    stack_bb: tuple[float, float] | None    # inclusive range on the decision's effective stack
    players: tuple[int, int] | None         # inclusive range on players dealt in
    actions: dict[str, list[float]]         # action -> 169 weights in CLASSES order (fold is the rest)
    ranges: dict[str, str] = field(default_factory=dict)  # action -> range text as written in the file
    index: int = 0                          # position in the file's "spots" list

    def matches(self, position: str, opener: str | None, eff_bb: float, players: int) -> bool:
        if self.positions and position not in self.positions:
            return False
        if self.openers and opener not in self.openers:
            return False
        if self.stack_bb and not self.stack_bb[0] <= eff_bb <= self.stack_bb[1]:
            return False
        return not self.players or self.players[0] <= players <= self.players[1]

    def freqs(self, cls: str) -> dict[str, float]:
        """Frequency of every action for one class, fold included (sums to 1)."""
        i = CLASS_INDEX[cls]
        out = {a: round(w[i], 4) for a, w in self.actions.items()}
        out["fold"] = round(max(0.0, 1.0 - sum(w[i] for w in self.actions.values())), 4)
        return out

    def label(self) -> str:
        """Short description, e.g. "vs_open · BB vs BTN/CO · 20-60 BB · 5-6 players"."""
        parts = [self.situation, "/".join(self.positions) or "any position"]
        if self.openers:
            parts[-1] += " vs " + "/".join(self.openers)
        if self.stack_bb:
            parts.append(f"{self.stack_bb[0]:g}-{self.stack_bb[1]:g} BB")
        if self.players:
            parts.append(f"{self.players[0]}-{self.players[1]} players")
        return " · ".join(parts)


@dataclass
class Chart:
    name: str
    source: str
    spots: list[ChartSpot]
    path: Path | None = None


# ---------------------------------------------------------------------------------------
# Loading and validation


def charts_dir() -> Path:
    return Path(os.environ.get("POKER_CHARTS") or DEFAULT_DIR)


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ChartError(f"duplicate key {dupes[0]!r}")
    return dict(pairs)


def _names(value: object, what: str) -> tuple[str, ...]:
    """A position name or a list of them, upper-cased and checked."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(v, str) for v in items):
        raise ChartError(f"{what} must be a position name or a non-empty list of them")
    out = tuple(v.strip().upper() for v in items)
    unknown = [v for v in out if v not in POSITION_ORDER]
    if unknown:
        raise ChartError(f"unknown {what} {unknown[0]!r} (use {', '.join(POSITION_ORDER)})")
    return out


def _bounds(value: object, what: str, integer: bool) -> tuple:
    """An inclusive [low, high] range of numbers."""
    kinds = (int,) if integer else (int, float)
    if not (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, kinds) and not isinstance(v, bool) for v in value)):
        raise ChartError(f"{what} must be [low, high] with two {'whole numbers' if integer else 'numbers'}")
    low, high = value
    try:
        finite = all(math.isfinite(v) for v in value)
    except OverflowError:       # an int too big for a float
        finite = False
    if not finite or low < 0 or low > high:
        raise ChartError(f"bad {what} {value}: need 0 <= low <= high")
    return (int(low), int(high)) if integer else (float(low), float(high))


def _parse_spot(raw: object, index: int) -> ChartSpot:
    if not isinstance(raw, dict):
        raise ChartError("must be an object")
    unknown = sorted(set(raw) - SPOT_KEYS)
    if unknown:
        raise ChartError(f"unknown key {unknown[0]!r} (allowed: {', '.join(sorted(SPOT_KEYS))})")
    situation = raw.get("situation")
    if "situation" not in raw:
        raise ChartError(f'missing "situation" ({" or ".join(SITUATION_ACTIONS)})')
    if not isinstance(situation, str) or situation not in SITUATION_ACTIONS:
        raise ChartError(f"unknown situation {situation!r} (use {' or '.join(SITUATION_ACTIONS)})")
    if "position" in raw and "positions" in raw:
        raise ChartError('give "position" or "positions", not both')
    positions: tuple[str, ...] = ()
    if "position" in raw or "positions" in raw:
        key = "position" if "position" in raw else "positions"
        positions = _names(raw[key], key)
    openers: tuple[str, ...] = ()
    if "opener" in raw:
        if situation != "vs_open":
            raise ChartError('"opener" only applies to vs_open spots')
        openers = _names(raw["opener"], "opener")
        if "BB" in openers:
            raise ChartError("the BB never opens first in; remove it from opener")
    if situation == "rfi" and "BB" in positions:
        raise ChartError("the BB can't raise first in; remove it from the rfi positions")
    stack_bb = _bounds(raw["stack_bb"], "stack_bb", integer=False) if "stack_bb" in raw else None
    players = _bounds(raw["players"], "players", integer=True) if "players" in raw else None

    actions = raw.get("actions")
    if not isinstance(actions, dict) or not actions:
        raise ChartError('"actions" must be an object mapping actions to ranges')
    allowed = SITUATION_ACTIONS[situation]
    weights: dict[str, list[float]] = {}
    for action, text in actions.items():
        if action == "fold":
            raise ChartError('leave out "fold": it is whatever weight the other actions leave')
        if action not in allowed:
            raise ChartError(f"unknown {situation} action {action!r} (allowed: {', '.join(allowed)})")
        if not isinstance(text, str):
            raise ChartError(f"actions.{action} must be a range string")
        try:
            parsed = parse_weighted_range(text)
        except ValueError as e:
            raise ChartError(f"actions.{action}: {e}") from None
        weights[action] = [parsed.get(cls, 0.0) for cls in CLASSES]

    over = [(cls, sum(w[i] for w in weights.values())) for i, cls in enumerate(CLASSES)]
    over = [(cls, total) for cls, total in over if total > 1 + SUM_TOLERANCE]
    if over:
        shown = ", ".join(f"{cls} {total:g}" for cls, total in over[:5])
        more = f" and {len(over) - 5} more" if len(over) > 5 else ""
        raise ChartError(f"action weights add up to more than 1 for {shown}{more}")
    return ChartSpot(situation, positions, openers, stack_bb, players, weights,
                     ranges=dict(actions), index=index)


def parse_chart(data: object, default_name: str = "") -> Chart:
    """Validate a chart's JSON data; raises ChartError naming the problem."""
    if not isinstance(data, dict):
        raise ChartError("the file must hold a JSON object")
    unknown = sorted(set(data) - CHART_KEYS)
    if unknown:
        raise ChartError(f"unknown key {unknown[0]!r} (allowed: {', '.join(sorted(CHART_KEYS))})")
    name = data.get("name", default_name)
    if not isinstance(name, str) or not name.strip():
        raise ChartError('"name" must be a non-empty string')
    name = name.strip()
    if name.lower() in RESERVED_NAMES:
        raise ChartError(f"the name {name!r} is reserved for the computed push/fold charts")
    source = data.get("source", "")
    if not isinstance(source, str):
        raise ChartError('"source" must be a string')
    spots = data.get("spots")
    if not isinstance(spots, list) or not spots:
        raise ChartError('"spots" must be a non-empty list')
    parsed = []
    for i, raw in enumerate(spots):
        try:
            parsed.append(_parse_spot(raw, i))
        except ChartError as e:
            situation = raw.get("situation") if isinstance(raw, dict) else None
            where = f" ({situation})" if isinstance(situation, str) and situation in SITUATION_ACTIONS else ""
            raise ChartError(f"spots[{i}]{where}: {e}") from None
    return Chart(name=name, source=source, spots=parsed)


def load_chart(path: str | Path) -> Chart:
    """Read and validate one chart file; raises ChartError with the file name in the message."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text, object_pairs_hook=_no_duplicates)
        chart = parse_chart(data, default_name=path.stem)
    except json.JSONDecodeError as e:
        raise ChartError(f"{path.name}: bad JSON at line {e.lineno}, column {e.colno}: {e.msg}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ChartError(f"{path.name}: can't read the file ({e})") from None
    except ChartError as e:
        raise ChartError(f"{path.name}: {e}") from None
    except (ValueError, OverflowError, RecursionError) as e:   # e.g. a 5000-digit number, nesting too deep
        raise ChartError(f"{path.name}: can't read the chart ({e})") from None
    chart.path = path
    return chart


def load_charts(directory: str | Path | None = None) -> tuple[list[Chart], list[str]]:
    """Every *.json chart in the directory, sorted by file name, plus one error message per bad file.

    A missing directory means no charts. Never raises for a bad file.
    """
    directory = Path(directory) if directory else charts_dir()
    if not directory.exists():
        return [], []
    if not directory.is_dir():
        return [], [f"{directory}: not a directory"]
    charts: list[Chart] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in sorted(p for p in directory.glob("*.json") if not p.name.startswith(".")):
        try:
            chart = load_chart(path)
        except ChartError as e:
            errors.append(str(e))
            continue
        if chart.name in seen:
            errors.append(f"{path.name}: chart name {chart.name!r} is already used by {seen[chart.name]}")
            continue
        seen[chart.name] = path.name
        charts.append(chart)
    return charts, errors


# ---------------------------------------------------------------------------------------
# Comparing decisions


def _all_in(a: Action) -> bool:
    return a.all_in or a.amount >= a.stack_before


def _spot_context(hand: Hand, d: Action) -> tuple[str, str | None] | None:
    """(situation, opener position) for the player's first preflop decision, or None if no chart applies."""
    position = hand.player(d.player_id).position
    if d.raises_before == 0 and d.callers_before == 0:
        return ("rfi", None) if position != "BB" else None
    if d.raises_before == 1 and d.callers_before == 0:
        r = next((a for a in reversed(hand.actions[:d.seq]) if a.street == "preflop" and a.kind == "raise"), None)
        # The open must be first in; an all-in open is the push/fold "call" spot instead.
        if r is not None and r.raises_before == 0 and r.callers_before == 0 and not _all_in(r):
            return "vs_open", hand.player(r.player_id).position
    return None


def _action_taken(situation: str, d: Action, spot: ChartSpot) -> str | None:
    if d.kind == "fold":
        return "fold"
    if d.kind == "call":
        return "limp" if situation == "rfi" else "call"
    if d.kind == "raise":
        if _all_in(d) and "allin" in spot.actions:
            return "allin"
        return "raise" if situation == "rfi" else "3bet"
    return None


def chart_decisions(hand: Hand, charts: list[Chart], player_id: str | None = None) -> list[GtoDecision]:
    """The player's first preflop decision compared with the first chart spot that matches it.

    Hold'em only, and only when the player's cards are known. Default player: the hero.
    """
    player = hand.player(player_id or hand.hero_id)
    if (not charts or player is None or hand.game != "holdem" or not player.cards_known
            or len(player.hole_cards) != 2 or any(w.startswith(UNSUPPORTED_WARNINGS) for w in hand.warnings)):
        return []
    pre = [a for a in hand.decisions(player.id) if a.street == "preflop"]
    if not pre:
        return []
    d = pre[0]
    context = _spot_context(hand, d)
    if context is None:
        return []
    situation, opener = context
    eff = round(d.eff_stack_bb, 2)
    for chart in charts:
        for spot in chart.spots:
            if spot.situation != situation or not spot.matches(player.position, opener, eff, hand.num_players):
                continue
            action = _action_taken(situation, d, spot)
            if action is None:
                return []
            cls = hand_class(player.hole_cards)
            freqs = spot.freqs(cls)
            action_freq = freqs.get(action, 0.0)
            where = f"{player.position} raise first in" if situation == "rfi" else f"{player.position} vs {opener} open"
            return [GtoDecision(
                hand_id=hand.id, seq=d.seq, player_id=player.id, source=chart.name, situation=situation,
                position=player.position, eff_stack_bb=eff, players_behind=d.to_act_after, hand_class=cls,
                action=action, chart_action=max(freqs, key=freqs.get), freqs=freqs, action_freq=action_freq,
                verdict=verdict_for(action_freq), ev_loss_bb=None, title=f"{where} · {d.eff_stack_bb:.1f} BB",
                grid={a: list(w) for a, w in spot.actions.items()},
            )]
    return []


def all_decisions(hand: Hand, charts: list[Chart] | None = None, player_id: str | None = None) -> list[GtoDecision]:
    """Computed push/fold comparisons plus user-chart comparisons for one player (default: the hero).

    With charts=None the charts are loaded from the charts directory; pass them in when going
    through many hands.
    """
    if charts is None:
        charts = load_charts()[0]
    return gto.nash_decisions(hand, player_id) + chart_decisions(hand, charts, player_id)
