"""Parse PokerNow hand-history exports (JSON, handVersion 2) into Hand/Action objects.

The export lists, per hand, the players with their starting stacks and a flat list of
events. Bets and calls carry the *total* committed on the current street, so the parser
replays the events to recover how many chips each action added, the pot before each
decision and every player's stack (also in big blinds) at that moment.

Quirks handled here (all seen in real exports):
- hand numbers restart when you move tables, so hands are ordered by ``startedAt``;
- dead buttons (``dealerSeat`` is an empty seat) and dead small blinds (no SB post);
- several pot awards in a row are side pots, and split pots award part of a pot each;
- the game can switch between Hold'em and PLO inside one tournament;
- moving tables shows up as a change of hero seat or a jump in hand numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Event payload types.
EV_CHECK = 0
EV_BIG_BLIND = 2
EV_SMALL_BLIND = 3
EV_CALL = 7
EV_BET = 8
EV_BOARD = 9
EV_AWARD = 10
EV_FOLD = 11
EV_SHOW = 12
EV_HAND_END = 15
EV_UNCALLED = 16

PLAYER_ACTIONS = {EV_CHECK, EV_CALL, EV_BET, EV_FOLD}
STREETS = ["preflop", "flop", "turn", "river"]
GAME_NAMES = {"th": "holdem", "omaha": "plo"}

POSITION_GROUPS = {
    "UTG": "EP", "UTG+1": "EP", "UTG+2": "EP", "UTG+3": "EP", "UTG+4": "EP",
    "LJ": "MP", "HJ": "MP",
    "CO": "LP", "BTN": "LP",
    "SB": "Blinds", "BB": "Blinds",
}
# Display order for position tables.
POSITION_ORDER = ["UTG", "UTG+1", "UTG+2", "UTG+3", "UTG+4", "LJ", "HJ", "CO", "BTN", "SB", "BB"]


class ParseError(ValueError):
    pass


@dataclass
class Action:
    seq: int                 # index among this hand's actions
    at: int                  # event timestamp (ms)
    street: str              # preflop / flop / turn / river
    seat: int
    player_id: str
    kind: str                # post_sb, post_bb, check, call, bet, raise, fold, return
    amount: int              # chips this action added to the pot (returned chips for "return")
    to_amount: int           # player's total on this street after the action
    all_in: bool
    # Decision context, measured just before the action.
    pot_before: int          # everything in the pot so far, all streets
    to_call: int             # chips needed to call (capped at the stack behind)
    stack_before: int        # chips behind
    bb: int
    raises_before: int       # preflop: raises over the big blind; postflop: bets + raises this street
    callers_before: int      # players who called the last bet/raise (limpers when unraised preflop)
    active_before: int       # players not folded
    to_act_after: int        # players yet to act after this one in the betting round (not folded, not all-in)
    eff_stack_bb: float      # min(own stack at hand start, biggest other live stack at hand start) / bb

    @property
    def is_decision(self) -> bool:
        return self.kind in ("check", "call", "bet", "raise", "fold")

    @property
    def is_aggressive(self) -> bool:
        return self.kind in ("bet", "raise")

    @property
    def stack_bb(self) -> float:
        return self.stack_before / self.bb

    @property
    def pot_bb(self) -> float:
        return self.pot_before / self.bb


@dataclass
class Award:
    seat: int
    player_id: str
    amount: int
    pot: int
    pot_index: int           # 1 = main pot, 2+ = side pots (PokerNow "position")
    run: int
    cards: list[str] | None
    hand_description: str | None


@dataclass
class Player:
    id: str
    seat: int
    name: str
    stack: int                       # at the start of the hand
    hole_cards: list[str | None] | None  # always for the hero, for opponents only when shown (None = card not shown)
    position: str = ""
    invested: int = 0                # chips put in, net of uncalled bets returned
    won: int = 0
    folded_street: str | None = None
    all_in: bool = False
    showed: bool = False             # revealed cards (at showdown or voluntarily)

    @property
    def cards_known(self) -> bool:
        return bool(self.hole_cards) and all(self.hole_cards)

    @property
    def cards_text(self) -> str:
        return " ".join(c or "??" for c in self.hole_cards) if self.hole_cards else ""

    @property
    def net(self) -> int:
        return self.won - self.invested

    @property
    def end_stack(self) -> int:
        return self.stack + self.net

    @property
    def position_group(self) -> str:
        return POSITION_GROUPS.get(self.position, "")


@dataclass
class Hand:
    id: str
    number: str
    game: str                        # holdem / plo
    small_blind: int
    big_blind: int
    ante: int
    dealer_seat: int
    started_at: int                  # ms since epoch
    hero_id: str
    tournament: str
    players: list[Player]
    table: int = 1                   # 1-based table index within the session (see assign_tables)
    actions: list[Action] = field(default_factory=list)
    boards: dict[int, list[str]] = field(default_factory=dict)  # run -> 5 (or fewer) cards
    awards: list[Award] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    # ---- lookups -------------------------------------------------------------------
    def player(self, player_id: str) -> Player | None:
        return next((p for p in self.players if p.id == player_id), None)

    def at_seat(self, seat: int) -> Player | None:
        return next((p for p in self.players if p.seat == seat), None)

    @property
    def hero(self) -> Player | None:
        return self.player(self.hero_id)

    @property
    def board(self) -> list[str]:
        return self.boards.get(1, [])

    @property
    def started(self) -> datetime:
        return datetime.fromtimestamp(self.started_at / 1000, tz=timezone.utc)

    @property
    def num_players(self) -> int:
        return len(self.players)

    @property
    def pot(self) -> int:
        return sum(p.invested for p in self.players)

    def actions_on(self, street: str) -> list[Action]:
        return [a for a in self.actions if a.street == street]

    def decisions(self, player_id: str | None = None) -> list[Action]:
        return [a for a in self.actions if a.is_decision and (player_id is None or a.player_id == player_id)]

    # ---- derived facts --------------------------------------------------------------
    @property
    def last_street(self) -> str:
        """Furthest street dealt (run 1)."""
        return STREETS[{0: 0, 3: 1, 4: 2, 5: 3}.get(len(self.board), 0)]

    def saw_street(self, player_id: str, street: str) -> bool:
        p = self.player(player_id)
        if p is None or STREETS.index(self.last_street) < STREETS.index(street):
            return False
        return p.folded_street is None or STREETS.index(p.folded_street) > STREETS.index(street)

    @property
    def showdown(self) -> bool:
        return sum(1 for p in self.players if p.folded_street is None) >= 2

    def went_to_showdown(self, player_id: str) -> bool:
        p = self.player(player_id)
        return bool(p and self.showdown and p.folded_street is None)

    @property
    def preflop_aggressor(self) -> str | None:
        raises = [a for a in self.actions_on("preflop") if a.kind == "raise"]
        return raises[-1].player_id if raises else None

    def allin_point(self) -> int | None:
        """Seq of the last decision if, after it, the pot was contested all-in with cards to come.

        Returns None if the hand never got all-in, or if the board was complete when the
        last chips went in (so there was no luck left to measure).
        """
        if not self.showdown:
            return None
        live = [p for p in self.players if p.folded_street is None]
        if not any(p.all_in for p in live):
            return None
        decisions = self.decisions()
        if not decisions:
            # Everyone was all-in from the blinds.
            return -1 if len(self.board) < 5 else None
        last = decisions[-1]
        if last.street == "river":
            return None
        return last.seq

    def board_at(self, seq: int, run: int = 1) -> list[str]:
        """Board cards that were out when action ``seq`` happened."""
        if seq < 0:
            return []
        street = self.actions[seq].street
        return self.boards.get(run, self.board)[: {"preflop": 0, "flop": 3, "turn": 4, "river": 5}[street]]


# ---------------------------------------------------------------------------------------
# Positions


def _non_blind_names(k: int) -> list[str]:
    """Names for the k players who are not in the blinds, in preflop action order."""
    if k <= 0:
        return []
    if k <= 3:
        return ["HJ", "CO", "BTN"][-k:]
    middle = k - 4
    names = ["UTG"]
    names += [f"UTG+{i}" for i in range(1, middle)]
    if middle >= 1:
        names.append("LJ")
    return names + ["HJ", "CO", "BTN"]


def assign_positions(seats: list[int], dealer_seat: int, sb_seat: int | None, bb_seat: int) -> dict[int, str]:
    """Map seat -> position name.

    Positions come from who actually posted the blinds, which keeps dead buttons and dead
    small blinds right. The last player to act preflop before the blinds is the BTN even
    when the button itself sits on an empty seat (that player acts last after the flop).
    Heads-up, the dealer posts the small blind and is labelled BTN.
    """
    order = sorted(seats)
    if bb_seat not in order:
        raise ParseError(f"big blind seat {bb_seat} has no player")
    if len(order) == 2:
        other = next(s for s in order if s != bb_seat)
        return {other: "BTN", bb_seat: "BB"}
    i = order.index(bb_seat)
    preflop_order = order[i + 1:] + order[: i + 1]  # starts left of the BB, ends with the BB
    non_blinds = [s for s in preflop_order if s not in (sb_seat, bb_seat)]
    positions = dict(zip(non_blinds, _non_blind_names(len(non_blinds))))
    if sb_seat is not None:
        positions[sb_seat] = "SB"
    positions[bb_seat] = "BB"
    return positions


# ---------------------------------------------------------------------------------------
# Parsing


def _street_order(seats: list[int], street: str, bb_seat: int, dealer_seat: int) -> list[int]:
    """Seats in the order they act on a street."""
    order = sorted(seats)
    if street == "preflop":
        i = order.index(bb_seat)
        return order[i + 1:] + order[: i + 1]
    # Postflop: first seat clockwise from the button (the button itself acts last).
    after = [s for s in order if s > dealer_seat]
    before = [s for s in order if s <= dealer_seat]
    return after + before


def parse_hand(raw: dict, hero_id: str = "", tournament: str = "") -> Hand:
    if raw.get("handVersion", 2) != 2:
        raise ParseError(f"hand {raw.get('id')}: unsupported handVersion {raw.get('handVersion')}")
    game = GAME_NAMES.get(raw["gameType"], raw["gameType"])
    bb = int(raw["bigBlind"])
    players = [
        Player(id=p["id"], seat=int(p["seat"]), name=p["name"], stack=int(p["stack"]),
               hole_cards=list(p["hand"]) if p.get("hand") else None)
        for p in raw["players"]
    ]
    hand = Hand(
        id=raw["id"], number=str(raw["number"]), game=game,
        small_blind=int(raw["smallBlind"]), big_blind=bb, ante=int(raw.get("ante") or 0),
        dealer_seat=int(raw["dealerSeat"]), started_at=int(raw["startedAt"]),
        hero_id=hero_id, tournament=tournament, players=players, raw=raw,
    )
    for flag in ("bombPot", "doubleBoard", "straddleSeat"):
        if raw.get(flag):
            hand.warnings.append(f"{flag} is set; positions and stats may be off for this hand")
    if hand.ante:
        hand.warnings.append("antes are not modelled; chip replay may not balance")

    by_seat = {p.seat: p for p in players}
    payloads = [e["payload"] for e in raw["events"]]
    times = [e.get("at", 0) for e in raw["events"]]
    sb_seat = next((p["seat"] for p in payloads if p["type"] == EV_SMALL_BLIND), None)
    bb_seat = next((p["seat"] for p in payloads if p["type"] == EV_BIG_BLIND), None)
    if bb_seat is None:
        raise ParseError(f"hand {hand.id}: no big blind posted")
    for seat, pos in assign_positions(list(by_seat), hand.dealer_seat, sb_seat, bb_seat).items():
        by_seat[seat].position = pos

    starts = {p.seat: p.stack for p in players}
    behind = dict(starts)
    committed = {s: 0 for s in by_seat}       # this street
    street = "preflop"
    current_bet = 0
    raises = 0
    callers = 0
    folded: set[int] = set()
    all_in: set[int] = set()
    acted: set[int] = set()                   # acted since the last bet/raise this street
    run_cards: dict[int, dict[int, list[str]]] = {}

    def live_seats() -> list[int]:
        return [s for s in by_seat if s not in folded]

    def to_act_after(seat: int) -> int:
        order = _street_order(list(by_seat), street, bb_seat, hand.dealer_seat)
        i = order.index(seat)
        rest = order[i + 1:] + order[:i]
        return sum(1 for s in rest if s not in folded and s not in all_in
                   and (s not in acted or committed[s] < current_bet))

    def eff_stack(seat: int) -> float:
        others = [starts[s] for s in live_seats() if s != seat]
        return min(starts[seat], max(others)) / bb if others else starts[seat] / bb

    for payload, at in zip(payloads, times):
        t = payload["type"]
        seat = payload.get("seat")
        if seat is not None and seat not in by_seat:
            hand.warnings.append(f"event type {t} for empty seat {seat}")
            continue

        if t in (EV_SMALL_BLIND, EV_BIG_BLIND):
            value = int(payload["value"])
            kind = "post_sb" if t == EV_SMALL_BLIND else "post_bb"
            hand.actions.append(Action(
                seq=len(hand.actions), at=at, street=street, seat=seat, player_id=by_seat[seat].id,
                kind=kind, amount=value, to_amount=committed[seat] + value,
                all_in=bool(payload.get("allIn")), pot_before=sum(starts[s] - behind[s] for s in by_seat),
                to_call=0, stack_before=behind[seat], bb=bb, raises_before=0, callers_before=0,
                active_before=len(live_seats()), to_act_after=0, eff_stack_bb=eff_stack(seat),
            ))
            committed[seat] += value
            behind[seat] -= value
            current_bet = max(current_bet, committed[seat])
            if payload.get("allIn") or behind[seat] == 0:
                all_in.add(seat)

        elif t in PLAYER_ACTIONS:
            pot_before = sum(starts[s] - behind[s] for s in by_seat)
            to_call = min(max(current_bet - committed[seat], 0), behind[seat])
            context = dict(
                seq=len(hand.actions), at=at, street=street, seat=seat, player_id=by_seat[seat].id,
                pot_before=pot_before, to_call=to_call, stack_before=behind[seat], bb=bb,
                raises_before=raises, callers_before=callers, active_before=len(live_seats()),
                to_act_after=to_act_after(seat), eff_stack_bb=eff_stack(seat),
                all_in=bool(payload.get("allIn")),
            )
            if t == EV_FOLD:
                hand.actions.append(Action(kind="fold", amount=0, to_amount=committed[seat], **context))
                folded.add(seat)
                by_seat[seat].folded_street = street
            elif t == EV_CHECK:
                hand.actions.append(Action(kind="check", amount=0, to_amount=committed[seat], **context))
                acted.add(seat)
            else:
                value = int(payload["value"])
                added = value - committed[seat]
                if added < 0 or added > behind[seat]:
                    hand.warnings.append(f"seat {seat} puts in {added} with {behind[seat]} behind")
                    added = max(0, min(added, behind[seat]))
                    value = committed[seat] + added
                if t == EV_BET and value > current_bet:
                    kind = "bet" if street != "preflop" and current_bet == 0 else "raise"
                else:
                    kind = "call"
                hand.actions.append(Action(kind=kind, amount=added, to_amount=value, **context))
                committed[seat] = value
                behind[seat] -= added
                if kind == "call":
                    callers += 1
                    acted.add(seat)
                else:
                    current_bet = value
                    raises += 1
                    callers = 0
                    acted = {seat}
            if behind[seat] == 0 and seat not in folded:
                all_in.add(seat)

        elif t == EV_UNCALLED:
            value = int(payload["value"])
            hand.actions.append(Action(
                seq=len(hand.actions), at=at, street=street, seat=seat, player_id=by_seat[seat].id,
                kind="return", amount=value, to_amount=committed[seat] - value, all_in=False,
                pot_before=sum(starts[s] - behind[s] for s in by_seat), to_call=0,
                stack_before=behind[seat], bb=bb, raises_before=raises, callers_before=callers,
                active_before=len(live_seats()), to_act_after=0, eff_stack_bb=eff_stack(seat),
            ))
            committed[seat] -= value
            behind[seat] += value
            if behind[seat] > 0:
                all_in.discard(seat)

        elif t == EV_BOARD:
            run = int(payload.get("run") or 1)
            turn = int(payload["turn"])
            run_cards.setdefault(run, {})[turn] = list(payload["cards"])
            if run == 1:
                street = STREETS[turn]
                committed = {s: 0 for s in by_seat}
                current_bet = 0
                raises = 0
                callers = 0
                acted = set()

        elif t == EV_AWARD:
            hand.awards.append(Award(
                seat=seat, player_id=by_seat[seat].id, amount=int(payload["value"]),
                pot=int(payload.get("pot", payload["value"])), pot_index=int(payload.get("position") or 1),
                run=int(payload.get("runNumber") or 1), cards=payload.get("cards"),
                hand_description=payload.get("handDescription"),
            ))
            hilo_warning = "hi-lo pot: equity is not computed for this hand"
            if payload.get("hiLo") == "l" and hilo_warning not in hand.warnings:
                hand.warnings.append(hilo_warning)

        elif t == EV_SHOW:
            # A player can show one card at a time; unshown cards come through as null.
            p = by_seat[seat]
            p.showed = True
            cards = list(payload["cards"])
            if p.hole_cards and len(p.hole_cards) == len(cards):
                cards = [c or old for c, old in zip(cards, p.hole_cards)]
            p.hole_cards = cards

        elif t == EV_HAND_END:
            pass

        else:
            hand.warnings.append(f"unknown event type {t}: {payload}")

    # Boards: a later run reuses the cards run 1 had already dealt.
    base = run_cards.get(1, {})
    for run, cards in run_cards.items():
        board: list[str] = []
        for turn in (1, 2, 3):
            if turn in cards:
                board += cards[turn]
            elif turn in base:
                board += base[turn]
        hand.boards[run] = board

    for p in players:
        p.invested = starts[p.seat] - behind[p.seat]
        p.won = sum(a.amount for a in hand.awards if a.seat == p.seat)
        p.all_in = p.seat in all_in or (behind[p.seat] == 0 and p.folded_street is None)
        if p.hole_cards and p.id != hero_id and p.folded_street is None and hand.showdown:
            p.showed = True
    return hand


@dataclass
class Session:
    """One exported file: a tournament (or cash session) seen by one player."""
    hero_id: str
    tournament: str
    generated_at: str
    hands: list[Hand]

    @property
    def hero_name(self) -> str:
        for h in self.hands:
            if h.hero:
                return h.hero.name
        return ""


def assign_tables(hands: list[Hand]) -> None:
    """Number the tables the hero sat at, in order.

    Exports have no table id. Hand numbers count per table, so a new table shows up as the
    hero changing seat or the hand number not following on from the previous hand.
    """
    table = 0
    prev: Hand | None = None
    for h in hands:
        hero = h.hero
        if prev is None:
            table = 1
        else:
            prev_hero = prev.hero
            try:
                follows = int(h.number) == int(prev.number) + 1
            except ValueError:
                follows = True
            if not follows or (hero and prev_hero and hero.seat != prev_hero.seat):
                table += 1
        h.table = table
        prev = h


def parse_export(data: dict) -> Session:
    hero_id = data.get("playerId", "")
    slug = data.get("tournamentSlug") or data.get("gameId") or data.get("slug") or ""
    hands = [parse_hand(h, hero_id, slug) for h in data.get("hands", [])]
    hands.sort(key=lambda h: (h.started_at, h.id))
    assign_tables(hands)
    return Session(hero_id=hero_id, tournament=slug, generated_at=data.get("generatedAt", ""), hands=hands)


def parse_file(path: str | Path) -> Session:
    with open(path, encoding="utf-8") as f:
        return parse_export(json.load(f))


# ---------------------------------------------------------------------------------------
# Consistency checks


def chip_replay(hands: Iterable[Hand]) -> list[str]:
    """Check chip accounting for one session; returns problems (empty means it all balances).

    - every hand: chips awarded == chips put in;
    - every player: start + won - invested equals their stack the next time they are dealt
      in at the same table (opponents play hands we never see once we change tables, so
      only the hero is followed across tables).
    """
    problems: list[str] = []
    last_end: dict[str, tuple[str, int, int]] = {}
    for h in sorted(hands, key=lambda h: (h.started_at, h.id)):
        awarded = sum(a.amount for a in h.awards)
        if awarded != h.pot:
            problems.append(f"hand {h.number} ({h.id}): awarded {awarded} but pot was {h.pot}")
        for p in h.players:
            if p.id in last_end:
                prev_hand, table, expected = last_end[p.id]
                if (p.id == h.hero_id or table == h.table) and expected != p.stack:
                    problems.append(
                        f"{p.name}: ended hand {prev_hand} with {expected}, started hand {h.number} ({h.id}) with {p.stack}")
            if p.end_stack < 0:
                problems.append(f"hand {h.number} ({h.id}): {p.name} ends with a negative stack")
            last_end[p.id] = (f"{h.number} ({h.id})", h.table, p.end_stack)
    return problems
