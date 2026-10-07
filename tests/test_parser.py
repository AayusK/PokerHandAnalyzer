from collections import Counter

import pytest

from conftest import END, bb, bet, board, call, check, fold, make_hand, ret, sb, show, win
from pokeranalyzer.parser import assign_positions, chip_replay


def positions(h):
    return {p.seat: p.position for p in h.players}


# ---- The sample export ------------------------------------------------------------------

def test_sample_parses(session):
    assert len(session.hands) == 40
    assert session.hero_name == "Kesayu"
    assert session.tournament == "osu-freeroll-5-1zGM0DuisE"
    assert Counter(h.game for h in session.hands) == {"holdem": 24, "plo": 16}
    assert [h.started_at for h in session.hands] == sorted(h.started_at for h in session.hands)
    assert all(h.hero is not None for h in session.hands)
    assert not any(h.warnings for h in session.hands)


def test_chip_replay_balances(session):
    assert chip_replay(session.hands) == []


def test_every_hand_conserves_chips(session):
    for h in session.hands:
        assert sum(a.amount for a in h.awards) == h.pot
        assert sum(p.net for p in h.players) == 0


def test_table_changes(session):
    # Hand numbers count per table: 1-22, then 22-27 at a second table, then 31-42 at a third.
    assert [h.table for h in session.hands] == [1] * 22 + [2] * 6 + [3] * 12
    assert [h.number for h in session.hands].count("22") == 2


def test_hero_wins_the_tournament(session):
    last = session.hands[-1]
    assert last.hero.end_stack == sum(p.stack for p in last.players)
    assert [p.end_stack for p in last.players if p.id != last.hero_id] == [0]


# ---- Positions -----------------------------------------------------------------------------

def test_positions_6max(hand):
    assert positions(hand(1)) == {1: "CO", 2: "BTN", 3: "SB", 4: "BB", 5: "UTG", 6: "HJ"}


def test_positions_8_handed(hand):
    assert positions(hand(3)) == {7: "UTG", 9: "UTG+1", 1: "LJ", 2: "HJ", 3: "CO", 4: "BTN", 5: "SB", 6: "BB"}


def test_positions_dead_button(hand):
    # dealerSeat 5 is empty; seat 3 acts last before the blinds, so it plays as the button.
    h = hand(24)
    assert h.dealer_seat not in {p.seat for p in h.players}
    assert positions(h) == {8: "UTG", 9: "HJ", 10: "CO", 3: "BTN", 6: "SB", 7: "BB"}


def test_positions_dead_small_blind(hand):
    h = hand(23)
    assert not any(a.kind == "post_sb" for a in h.actions)
    assert positions(h) == {7: "UTG", 8: "HJ", 9: "CO", 10: "BTN", 6: "BB"}


def test_positions_three_handed(hand):
    assert positions(hand(35)) == {1: "BTN", 7: "SB", 8: "BB"}
    # Dead button and dead small blind with three players left.
    assert positions(hand(34)) == {8: "CO", 1: "BTN", 7: "BB"}


def test_positions_heads_up(hand):
    h = hand(40)
    assert positions(h) == {8: "BTN", 7: "BB"}
    # The button posts the small blind and acts first preflop.
    assert h.actions[0].kind == "post_sb" and h.actions[0].seat == 8
    assert h.decisions()[0].seat == 8


@pytest.mark.parametrize("n, expected", [
    (9, ["UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB"]),
    (10, ["UTG", "UTG+1", "UTG+2", "UTG+3", "LJ", "HJ", "CO", "BTN", "SB", "BB"]),
    (7, ["UTG", "LJ", "HJ", "CO", "BTN", "SB", "BB"]),
    (6, ["UTG", "HJ", "CO", "BTN", "SB", "BB"]),
    (5, ["HJ", "CO", "BTN", "SB", "BB"]),
    (4, ["CO", "BTN", "SB", "BB"]),
])
def test_position_names_by_table_size(n, expected):
    seats = list(range(1, n + 1))
    dealer = n - 2  # so seats run UTG... BTN, SB, BB in seat order
    got = assign_positions(seats, dealer, sb_seat=n - 1, bb_seat=n)
    assert [got[s] for s in seats] == expected


def test_heads_up_postflop_order():
    h = make_hand([(1, 1000, ["As", "Kd"]), (2, 1000, None)], dealer=1, events=[
        sb(1, 10), bb(2, 20), call(1, 20), check(2),
        board(1, ["2c", "7d", "9h"]), check(2), bet(1, 40), fold(2), ret(1, 40), END, win(1, 40),
    ])
    assert positions(h) == {1: "BTN", 2: "BB"}
    flop = h.actions_on("flop")
    assert flop[0].seat == 2 and flop[0].kind == "check" and flop[0].to_act_after == 1
    assert flop[1].kind == "bet" and flop[1].to_act_after == 0   # the BB already acted this round
    assert chip_replay([h]) == []
    assert h.hero.net == 20


# ---- Amounts and decision context ---------------------------------------------------------

def test_amounts_and_context(hand):
    h = hand(3)
    hero = h.hero
    raise_ = next(a for a in h.decisions(hero.id) if a.kind == "raise")
    assert (raise_.amount, raise_.to_amount, raise_.pot_before, raise_.stack_before) == (100, 100, 70, 1950)
    assert raise_.pot_bb == 3.5 and raise_.stack_bb == 97.5
    assert raise_.raises_before == 0 and raise_.callers_before == 2   # two limpers
    hj_call = next(a for a in h.actions if a.seat == 2 and a.kind == "call" and a.to_amount == 100)
    assert hj_call.amount == 80 and hj_call.to_call == 80 and hj_call.raises_before == 1
    flop_bet = next(a for a in h.actions_on("flop") if a.kind == "bet")
    assert flop_bet.pot_before == 250 and flop_bet.amount == 80
    assert h.actions[-1].kind == "return" and h.actions[-1].amount == 80
    assert hero.invested == 100 and hero.won == 250 and hero.net == 150


def test_preflop_raise_by_big_blind_counts_as_raise(hand):
    h = hand(4)
    three_bet = next(a for a in h.actions if a.seat == 7 and a.kind == "raise")
    assert three_bet.raises_before == 1 and three_bet.to_amount == 480


def test_split_and_side_pots(hand):
    h = hand(10)
    assert sorted({(a.pot_index, a.pot) for a in h.awards}) == [(1, 7268), (2, 1089), (3, 514)]
    assert h.hero.won == 1817 + 363 + 257
    assert len([a for a in h.awards if a.pot_index == 1]) == 4

    h = hand(5)
    assert [(a.pot_index, a.amount) for a in h.awards] == [(1, 120), (2, 910)]


def test_one_card_at_a_time_shows_are_merged(hand):
    h = hand(24)
    p = h.at_seat(7)
    assert p.hole_cards == ["2d", "9d"] and p.showed and p.cards_known


def test_voluntary_show_is_not_a_showdown(hand):
    h = hand(2)
    shower = h.at_seat(5)
    assert shower.showed and not h.showdown and not h.went_to_showdown(shower.id)


def test_all_in_flags(hand):
    h = hand(39)  # opponent shoves, hero (bigger stack) calls
    assert h.showdown
    assert [p.all_in for p in h.players if p.folded_street is None and p.id != h.hero_id] == [True]
    assert not h.hero.all_in
    assert h.allin_point() is not None
    assert h.board_at(h.allin_point()) == []


def test_unknown_event_is_reported_not_fatal():
    h = make_hand([(1, 100, None), (2, 100, None)], dealer=1, events=[
        sb(1, 10), bb(2, 20), {"type": 99, "foo": 1}, fold(1), ret(2, 10), END, win(2, 20),
    ])
    assert any("unknown event type 99" in w for w in h.warnings)
    assert chip_replay([h]) == []


def test_later_run_reuses_earlier_cards():
    # All-in on the turn, river run twice.
    h = make_hand([(1, 100, ["As", "Ad"]), (2, 100, ["Ks", "Kd"])], dealer=1, events=[
        sb(1, 10), bb(2, 20), call(1, 20), check(2),
        board(1, ["2c", "7d", "9h"]), check(2), check(1),
        board(2, ["Jc"]), bet(2, 80, allin=True), call(1, 80, allin=True),
        show(1, ["As", "Ad"]), show(2, ["Ks", "Kd"]),
        board(3, ["3s"], run=1), board(3, ["Kh"], run=2), END,
        win(1, 100, pot=200, run="1"), win(2, 100, pot=200, run="2"),
    ])
    assert h.boards[1] == ["2c", "7d", "9h", "Jc", "3s"]
    assert h.boards[2] == ["2c", "7d", "9h", "Jc", "Kh"]
    assert chip_replay([h]) == []
    assert h.hero.net == 0
