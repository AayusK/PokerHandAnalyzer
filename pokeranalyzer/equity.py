"""Equity for Hold'em (eval7) and PLO (phevaluator), plus luck-adjusted all-in results.

Equity is exact when two or fewer board cards are still to come and Monte Carlo
otherwise. Monte Carlo runs are seeded, so the same hand always gives the same number.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from math import comb

import eval7
from phevaluator import evaluate_omaha_cards

from .parser import Hand

DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]
EXACT_MAX_RUNOUTS = 50_000
DEFAULT_ITERATIONS = 20_000


def _norm(card: str) -> str:
    return card[0].upper() + card[1].lower()


def _scorer(game: str):
    """Return score(hole, board) where higher is better."""
    if game == "holdem":
        cache = {c: eval7.Card(c) for c in DECK}

        def score(hole: list[str], board: list[str]) -> int:
            return eval7.evaluate([cache[c] for c in hole] + [cache[c] for c in board])
        return score
    if game == "plo":
        def score(hole: list[str], board: list[str]) -> int:
            return -evaluate_omaha_cards(*board, *hole)  # phevaluator: lower rank is better
        return score
    raise ValueError(f"no evaluator for game {game!r}")


def equity(holes: list[list[str]], board: list[str] | None = None, game: str = "holdem",
           dead: list[str] | None = None, iterations: int = DEFAULT_ITERATIONS, seed: int = 0) -> list[float]:
    """Share of the pot each hand wins on average (ties split), given the board so far."""
    holes = [[_norm(c) for c in h] for h in holes]
    board = [_norm(c) for c in board or []]
    known = [c for h in holes for c in h] + board + [_norm(c) for c in dead or []]
    used = set(known)
    if len(used) != len(known):
        raise ValueError(f"duplicate cards in {known}")
    stub = [c for c in DECK if c not in used]
    need = 5 - len(board)
    score = _scorer(game)
    shares = [0.0] * len(holes)

    def settle(full_board: list[str]) -> None:
        scores = [score(h, full_board) for h in holes]
        best = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        for i in winners:
            shares[i] += 1 / len(winners)

    n_runouts = comb(len(stub), need)
    if n_runouts <= EXACT_MAX_RUNOUTS:
        count = 0
        for extra in itertools.combinations(stub, need):
            settle(board + list(extra))
            count += 1
    else:
        rng = random.Random(seed)
        count = iterations
        for _ in range(iterations):
            settle(board + rng.sample(stub, need))
    return [s / count for s in shares]


# ---------------------------------------------------------------------------------------
# Pots and all-in results


@dataclass
class Pot:
    amount: int
    eligible: list[str]      # player ids who can win it


def build_pots(hand: Hand) -> list[Pot]:
    """Split the final pot into main and side pots from what each player put in."""
    contrib = {p.id: p.invested for p in hand.players if p.invested > 0}
    live = {p.id for p in hand.players if p.folded_street is None}
    levels = sorted({contrib[pid] for pid in live if pid in contrib})
    pots: list[Pot] = []
    prev = 0
    for level in levels:
        amount = sum(min(c, level) - min(c, prev) for c in contrib.values())
        eligible = [pid for pid in live if contrib.get(pid, 0) >= level]
        if amount > 0:
            if pots and set(pots[-1].eligible) == set(eligible):
                pots[-1].amount += amount
            else:
                pots.append(Pot(amount, eligible))
        prev = level
    # Chips from folded players above the biggest live contribution go to the last pot.
    leftover = sum(max(0, c - prev) for c in contrib.values())
    if leftover and pots:
        pots[-1].amount += leftover
    return pots


@dataclass
class AllInResult:
    hand_id: str
    player_id: str
    street: str              # street the money went in on
    board: list[str]         # board cards out at that moment
    equity: float            # share of everything this player could win, weighted by pot size
    expected: float          # chips expected back from the pots
    won: int                 # chips actually won
    invested: int

    @property
    def expected_net(self) -> float:
        return self.expected - self.invested

    @property
    def actual_net(self) -> int:
        return self.won - self.invested

    @property
    def luck(self) -> float:
        """Chips won above (positive) or below (negative) what the equity was worth."""
        return self.won - self.expected


def allin_results(hand: Hand, iterations: int = DEFAULT_ITERATIONS) -> list[AllInResult]:
    """Expected vs actual result for every live player in a hand that was all-in with cards to come.

    Needs the cards of every player still in the hand. Returns [] when they are not all
    known, when there were no cards left to come, or for hi-lo pots.
    """
    point = hand.allin_point()
    if point is None or any("hi-lo" in w for w in hand.warnings):
        return []
    live = [p for p in hand.players if p.folded_street is None]
    if any(not p.cards_known for p in live):
        return []
    board = hand.board_at(point)
    street = hand.actions[point].street if point >= 0 else "preflop"
    folded_cards = [c for p in hand.players if p.folded_street is not None and p.hole_cards
                    for c in p.hole_cards if c]
    cards = {p.id: p.hole_cards for p in live}
    seed = int.from_bytes(hand.id.encode()[:8].ljust(8, b"\0"), "little")

    pots = build_pots(hand)
    expected = {p.id: 0.0 for p in live}
    cache: dict[tuple[str, ...], list[float]] = {}
    for pot in pots:
        ids = tuple(sorted(pot.eligible))
        if len(ids) == 1:
            expected[ids[0]] += pot.amount
            continue
        if ids not in cache:
            # Folded hands we saw are dead cards; leave them out only if they clash.
            dead = [c for c in folded_cards if c not in board and all(c not in cards[i] for i in ids)]
            cache[ids] = equity([cards[i] for i in ids], board, hand.game, dead=dead,
                                iterations=iterations, seed=seed)
        for pid, eq in zip(ids, cache[ids]):
            expected[pid] += eq * pot.amount

    out = []
    for p in live:
        winnable = sum(pot.amount for pot in pots if p.id in pot.eligible)
        out.append(AllInResult(
            hand_id=hand.id, player_id=p.id, street=street, board=board,
            equity=expected[p.id] / winnable if winnable else 0.0,
            expected=expected[p.id], won=p.won, invested=p.invested,
        ))
    return out
