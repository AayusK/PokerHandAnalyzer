"""Text replays of hands and compact betting lines."""

from __future__ import annotations

from .parser import Hand, STREETS

GAME_LABELS = {"holdem": "Hold'em", "plo": "PLO"}
LINE_CODES = {"check": "X", "call": "C", "bet": "B", "raise": "R", "fold": "F"}
SUITS = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}


def pretty(cards: list[str | None] | None, symbols: bool = True) -> str:
    if not cards:
        return ""
    out = []
    for c in cards:
        if not c:
            out.append("??")
        else:
            out.append(c[0] + (SUITS.get(c[1], c[1]) if symbols else c[1]))
    return " ".join(out)


def _bb(chips: float, bb: int) -> str:
    return f"{chips / bb:.1f} BB"


def betting_line(hand: Hand, player_id: str) -> str:
    """e.g. 'pre C R | flop B | turn X C' (X check, C call, B bet, R raise, F fold)."""
    parts = []
    for street in STREETS:
        codes = [LINE_CODES[a.kind] + ("*" if a.all_in else "")
                 for a in hand.decisions(player_id) if a.street == street]
        if codes:
            parts.append(f"{'pre' if street == 'preflop' else street} {' '.join(codes)}")
    return " | ".join(parts)


def replay_lines(hand: Hand, symbols: bool = True) -> list[str]:
    """Step-by-step text replay, with pot and stack sizes in big blinds at each decision."""
    bb = hand.big_blind
    lines = [
        f"Hand #{hand.number} · {GAME_LABELS.get(hand.game, hand.game)} {hand.small_blind}/{bb}"
        f"{f' ante {hand.ante}' if hand.ante else ''} · {hand.num_players} players · table {hand.table}"
        f" · {hand.started:%Y-%m-%d %H:%M} UTC",
    ]
    for p in sorted(hand.players, key=lambda p: p.seat):
        me = "  ← you" if p.id == hand.hero_id else ""
        cards = f"  [{pretty(p.hole_cards, symbols)}]" if p.hole_cards else ""
        lines.append(f"  Seat {p.seat:>2} {p.position:<5} {p.name}: {p.stack:,} ({_bb(p.stack, bb)}){cards}{me}")

    name = {p.id: p.name for p in hand.players}
    pos = {p.id: p.position for p in hand.players}
    street = None
    for a in hand.actions:
        if a.street != street:
            street = a.street
            if street == "preflop":
                lines.append("*** PREFLOP ***")
            else:
                n = {"flop": 3, "turn": 4, "river": 5}[street]
                lines.append(f"*** {street.upper()} *** [{pretty(hand.board[:n], symbols)}]  pot {a.pot_before:,}"
                             f" ({_bb(a.pot_before, bb)})")
        who = f"{name[a.player_id]} ({pos[a.player_id]})"
        allin = " and is all-in" if a.all_in else ""
        if a.kind == "post_sb":
            lines.append(f"  {who} posts small blind {a.amount:,}{allin}")
        elif a.kind == "post_bb":
            lines.append(f"  {who} posts big blind {a.amount:,}{allin}")
        elif a.kind == "return":
            lines.append(f"  Uncalled {a.amount:,} returned to {who}")
        else:
            ctx = f"pot {_bb(a.pot_before, bb)}, stack {_bb(a.stack_before, bb)}"
            if a.kind == "fold":
                text = "folds"
            elif a.kind == "check":
                text = "checks"
            elif a.kind == "call":
                text = f"calls {a.amount:,}"
            elif a.kind == "bet":
                text = f"bets {a.to_amount:,} ({_bb(a.to_amount, bb)})"
            else:
                text = f"raises to {a.to_amount:,} ({_bb(a.to_amount, bb)})"
            lines.append(f"  {who} {text}{allin}    [{ctx}]")

    # Cards dealt after the last action (all-in run-outs).
    for run, board in sorted(hand.boards.items()):
        shown = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}[street or "preflop"] if run == 1 else 0
        if len(board) > shown:
            label = "Board" if len(hand.boards) == 1 else f"Board (run {run})"
            lines.append(f"*** {label} *** [{pretty(board, symbols)}]")

    if hand.showdown:
        lines.append("*** SHOWDOWN ***")
        for p in hand.players:
            if p.folded_street is None:
                desc = next((aw.hand_description for aw in hand.awards if aw.seat == p.seat and aw.hand_description), "")
                lines.append(f"  {p.name} ({p.position}) shows [{pretty(p.hole_cards, symbols) or 'mucked'}]"
                             f"{f' — {desc}' if desc else ''}")
    for aw in hand.awards:
        pot_name = "the pot" if len({a.pot_index for a in hand.awards}) == 1 else (
            "the main pot" if aw.pot_index == 1 else f"side pot {aw.pot_index - 1}")
        share = f" (split {aw.pot:,})" if aw.amount != aw.pot else ""
        run = f" on run {aw.run}" if len(hand.boards) > 1 else ""
        lines.append(f"  {name[aw.player_id]} wins {aw.amount:,} from {pot_name}{share}{run}")
    for p in hand.players:
        if p.showed and not hand.went_to_showdown(p.id):
            lines.append(f"  {p.name} shows [{pretty(p.hole_cards, symbols)}]")
    hero = hand.hero
    if hero:
        sign = "+" if hero.net >= 0 else ""
        lines.append(f"Result for {hero.name}: {sign}{hero.net:,} ({sign}{hero.net / bb:.1f} BB)")
    for w in hand.warnings:
        lines.append(f"! {w}")
    return lines


def replay_text(hand: Hand, symbols: bool = True) -> str:
    return "\n".join(replay_lines(hand, symbols))
