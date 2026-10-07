"""Standard tracker stats, for the hero or any opponent.

Each hand is boiled down to one row of flags per player (``player_hand_flags``): did they
put money in voluntarily, raise preflop, have a chance to 3-bet, and so on. ``summarize``
adds those rows up, optionally by position/game/stack depth, and every stat carries the
number of opportunities it was measured on so small samples are obvious.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .parser import Hand, POSITION_ORDER

# A big-blind "min-raise" spot: a single raise to at most this many big blinds.
MIN_RAISE_MAX_BB = 2.0

STACK_BUCKETS = [(0, 10, "<10 BB"), (10, 20, "10–20 BB"), (20, 40, "20–40 BB"), (40, 100, "40–100 BB"),
                 (100, float("inf"), "100+ BB")]
STACK_BUCKET_ORDER = [b[2] for b in STACK_BUCKETS]


def stack_bucket(stack_bb: float) -> str:
    for lo, hi, label in STACK_BUCKETS:
        if lo <= stack_bb < hi:
            return label
    return STACK_BUCKETS[-1][2]


def player_hand_flags(hand: Hand, player_id: str) -> dict:
    """One row of per-hand facts and stat opportunities for a player."""
    p = hand.player(player_id)
    if p is None:
        raise KeyError(player_id)
    bb = hand.big_blind
    pre = [a for a in hand.decisions(player_id) if a.street == "preflop"]
    flop = [a for a in hand.decisions(player_id) if a.street == "flop"]
    post = [a for a in hand.decisions(player_id) if a.street != "preflop"]
    first = pre[0] if pre else None

    vpip = any(a.kind in ("call", "raise") for a in pre)
    pfr = any(a.kind == "raise" for a in pre)
    unopened = first is not None and first.raises_before == 0
    open_opp = unopened and first.callers_before == 0 and p.position != "BB"
    limp = unopened and first.kind == "call" and p.position != "BB"
    facing_open = [a for a in pre if a.raises_before == 1]
    three_bet_opp = bool(facing_open)
    three_bet = any(a.kind == "raise" for a in facing_open)

    # Big blind facing a single small raise.
    bb_minraise_opp = bb_minraise_fold = False
    if p.position == "BB" and first is not None and first.raises_before == 1:
        raise_to = max((a.to_amount for a in hand.actions if a.seq < first.seq and a.kind == "raise"), default=0)
        if raise_to <= MIN_RAISE_MAX_BB * bb:
            bb_minraise_opp = True
            bb_minraise_fold = first.kind == "fold"

    # Flop continuation bets.
    aggressor = hand.preflop_aggressor
    cbet_opp = cbet = False
    fold_to_cbet_opp = fold_to_cbet = False
    if aggressor == player_id and flop and flop[0].raises_before == 0:
        cbet_opp = True
        cbet = flop[0].kind == "bet"
    if aggressor and aggressor != player_id:
        agg_flop = [a for a in hand.decisions(aggressor) if a.street == "flop"]
        if agg_flop and agg_flop[0].raises_before == 0 and agg_flop[0].kind == "bet":
            cbet_seq = agg_flop[0].seq
            after = [a for a in flop if a.seq > cbet_seq]
            if after and after[0].raises_before == 1:
                fold_to_cbet_opp = True
                fold_to_cbet = after[0].kind == "fold"

    saw_flop = hand.saw_street(player_id, "flop")
    wtsd = saw_flop and hand.went_to_showdown(player_id)
    preflop_allin = any(a.all_in and a.kind in ("call", "raise") for a in pre)

    return {
        "hand_id": hand.id,
        "player_id": player_id,
        "name": p.name,
        "seat": p.seat,
        "position": p.position,
        "position_group": p.position_group,
        "start_stack": p.stack,
        "end_stack": p.end_stack,
        "invested": p.invested,
        "won": p.won,
        "net": p.net,
        "net_bb": p.net / bb,
        "stack_bb": p.stack / bb,
        "eff_stack_bb": first.eff_stack_bb if first else p.stack / bb,
        "hole_cards": p.cards_text,
        "showed": p.showed,
        "pf_decision": first is not None,
        "vpip": vpip,
        "pfr": pfr,
        "limp": limp,
        "open_opp": open_opp,
        "open_raise": open_opp and first.kind == "raise",
        "three_bet_opp": three_bet_opp,
        "three_bet": three_bet,
        "bb_minraise_opp": bb_minraise_opp,
        "bb_minraise_fold": bb_minraise_fold,
        "preflop_allin": preflop_allin,
        "saw_flop": saw_flop,
        "cbet_opp": cbet_opp,
        "cbet": cbet,
        "fold_to_cbet_opp": fold_to_cbet_opp,
        "fold_to_cbet": fold_to_cbet,
        "at_showdown": hand.went_to_showdown(player_id),
        "wtsd": wtsd,
        "wsd": wtsd and p.won > 0,
        "post_bets": sum(1 for a in post if a.kind == "bet"),
        "post_raises": sum(1 for a in post if a.kind == "raise"),
        "post_calls": sum(1 for a in post if a.kind == "call"),
        "post_checks": sum(1 for a in post if a.kind == "check"),
        "post_folds": sum(1 for a in post if a.kind == "fold"),
    }


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    numerator: str
    denominator: str
    help: str


STATS = [
    StatDef("vpip", "VPIP", "vpip", "pf_decision", "Put money in preflop voluntarily (blinds don't count)."),
    StatDef("pfr", "PFR", "pfr", "pf_decision", "Raised preflop."),
    StatDef("rfi", "Open raise", "open_raise", "open_opp", "Raised when folded to (raise first in)."),
    StatDef("limp", "Limp", "limp", "pf_decision", "Called the big blind with no raise in front."),
    StatDef("three_bet", "3-bet", "three_bet", "three_bet_opp", "Re-raised when facing a single raise."),
    StatDef("cbet", "Flop c-bet", "cbet", "cbet_opp", "Bet the flop as the preflop raiser when checked to."),
    StatDef("fold_to_cbet", "Fold to c-bet", "fold_to_cbet", "fold_to_cbet_opp", "Folded to a flop c-bet."),
    StatDef("wtsd", "Went to showdown", "wtsd", "saw_flop", "Reached showdown after seeing the flop."),
    StatDef("wsd", "Won at showdown", "wsd", "wtsd", "Won chips when reaching showdown."),
    StatDef("bb_fold_minraise", "BB fold vs min-raise", "bb_minraise_fold", "bb_minraise_opp",
            f"Folded the big blind to a single raise of at most {MIN_RAISE_MAX_BB:g} BB."),
]


def flags_frame(hands: list[Hand], player_id: str | None = None) -> pd.DataFrame:
    """Flags for one player (or every player when ``player_id`` is None) across hands."""
    rows = []
    for h in hands:
        ids = [player_id] if player_id else [p.id for p in h.players]
        for pid in ids:
            if h.player(pid) is None:
                continue
            row = player_hand_flags(h, pid)
            row.update(game=h.game, big_blind=h.big_blind, started_at=h.started_at,
                       num_players=h.num_players, session=h.tournament, table_no=h.table)
            rows.append(row)
    return add_derived(pd.DataFrame(rows))


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["stack_depth"] = df["stack_bb"].map(stack_bucket)
    return df


def _pct(num: float, den: float) -> float | None:
    return 100.0 * num / den if den else None


def summarize(df: pd.DataFrame, by: list[str] | str | None = None) -> pd.DataFrame:
    """Aggregate per-hand flags into stats. Every stat X comes with ``X_n`` (its sample size)."""
    if isinstance(by, str):
        by = [by]
    if df.empty:
        return pd.DataFrame()
    groups = df.groupby(by, observed=True, sort=False) if by else [((), df)]
    rows = []
    for key, g in groups:
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(by or [], key))
        row["hands"] = len(g)
        row["net_bb"] = g["net_bb"].sum()
        row["bb_per_100"] = 100 * g["net_bb"].mean()
        for s in STATS:
            den = g[s.denominator].sum()
            row[s.key] = _pct(g[s.numerator].sum(), den)
            row[f"{s.key}_n"] = int(den)
        aggressive = g["post_bets"].sum() + g["post_raises"].sum()
        calls = g["post_calls"].sum()
        row["af"] = aggressive / calls if calls else None
        row["af_n"] = int(aggressive + calls)
        afq_den = aggressive + calls + g["post_folds"].sum()
        row["afq"] = _pct(aggressive, afq_den)
        row["afq_n"] = int(afq_den)
        rows.append(row)
    out = pd.DataFrame(rows)
    if by and len(by) == 1 and by[0] in SORT_ORDERS:
        order = {v: i for i, v in enumerate(SORT_ORDERS[by[0]])}
        out = out.sort_values(by[0], key=lambda s: s.map(lambda v: order.get(v, len(order))))
    return out.reset_index(drop=True)


SORT_ORDERS = {
    "position": POSITION_ORDER,
    "position_group": ["EP", "MP", "LP", "Blinds"],
    "stack_depth": STACK_BUCKET_ORDER,
}

STAT_LABELS = {s.key: s.label for s in STATS} | {"af": "Aggression factor", "afq": "Aggression freq"}
STAT_HELP = {s.key: s.help for s in STATS} | {
    "af": "Postflop (bets + raises) / calls.",
    "afq": "Postflop (bets + raises) / (bets + raises + calls + folds).",
}


def format_stat(value: float | None, n: int, ratio: bool = False) -> str:
    """'23% (n=40)' style text; '—' when there is no sample."""
    if value is None or pd.isna(value):
        return f"— (n={n})"
    return f"{value:.2f} (n={n})" if ratio else f"{value:.0f}% (n={n})"


def display_table(summary: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """Summary with each stat rendered as 'value (n=…)' for display."""
    by = by or []
    out = summary[by + ["hands"]].copy()
    out["net BB"] = summary["net_bb"].round(1)
    out["BB/100"] = summary["bb_per_100"].round(1)
    for key, label in STAT_LABELS.items():
        out[label] = [format_stat(v, n, ratio=key == "af") for v, n in zip(summary[key], summary[f"{key}_n"])]
    return out
