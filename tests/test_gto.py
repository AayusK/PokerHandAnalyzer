import time

import numpy as np
import pytest

from conftest import END, bb, bet, call, fold, make_hand, raw_hand, ret, sb, win
from pokeranalyzer import gto
from pokeranalyzer.gto import (CLASS_INDEX, COMPAT, EQUITY, PushFoldSpot, nash_decisions, range_percent, solve,
                               symmetric_spot)
from pokeranalyzer.parser import parse_hand

I = CLASS_INDEX


# ---- Equity matrix and card removal ---------------------------------------------------------

def test_equity_matrix():
    assert EQUITY.shape == (169, 169)
    assert np.allclose(EQUITY + EQUITY.T, 1.0, atol=1e-9)
    assert np.allclose(np.diag(EQUITY), 0.5)
    assert EQUITY[I["AA"], I["KK"]] == pytest.approx(0.82, abs=0.005)
    assert EQUITY[I["AKs"], I["QQ"]] == pytest.approx(0.46, abs=0.005)
    assert EQUITY[I["72o"], I["AA"]] == pytest.approx(0.12, abs=0.005)


@pytest.mark.parametrize("a, b, n", [("AA", "KK", 6), ("AA", "AKs", 2), ("AKs", "AKo", 6), ("AA", "AA", 1),
                                     ("AKs", "AKs", 3), ("72o", "72o", 7)])
def test_compat_counts(a, b, n):
    assert COMPAT[I[a], I[b]] == n


def test_compat_rows_sum_to_remaining_combos():
    assert np.all(COMPAT.sum(axis=1) == 1225)


# ---- Solver ------------------------------------------------------------------------------------

def test_gain_formulas():
    game = gto._Game(PushFoldSpot(10.0, 0.5, (10.0,), (1.0,)))
    # Nobody calls: the pusher wins the BB and keeps the SB it would have folded.
    assert np.allclose(game.push_gain(np.zeros((1, 169)))[0], 1.5)
    # Always called: equity against a random hand times the 20 BB pot, minus 10 BB, plus the SB saved.
    eq_random = (COMPAT * EQUITY).sum(axis=1) / 1225
    assert np.allclose(game.push_gain(np.ones((1, 169)))[0], eq_random * 20 - 10 + 0.5)
    # The BB facing any two cards: pot 20, puts in 9 more.
    gain, reach = game.call_gain(np.ones(169), np.ones((1, 169)))
    assert np.allclose(gain[0], eq_random * 20 - 9)
    assert np.allclose(reach, 1.0)


def test_symmetric_spot():
    hu = symmetric_spot(1, 10)
    assert (hu.pusher_posted, hu.caller_posted, hu.caller_stacks) == (0.5, (1.0,), (10,))
    three = symmetric_spot(3, 8, ante=0.125, players=9)
    assert (three.pusher_posted, three.caller_posted, three.players) == (0.0, (0.0, 0.5, 1.0), 9)


def test_heads_up_matches_published_nash():
    # Published heads-up Nash push/fold at 10 BB, no ante: SB pushes ~58%, BB calls ~37%. The published
    # figures are whole percents, one offsuit class is 0.9% of hands and the equity matrix is Monte Carlo
    # (SE ~0.001 per pair, enough to move a boundary class), so allow 1.5 points.
    sol = solve(symmetric_spot(1, 10))
    assert sol.push_percent() == pytest.approx(58, abs=1.5)
    assert sol.call_percent(0) == pytest.approx(37, abs=1.5)
    assert sol.push[I["AA"]] > 0.99 and sol.push[I["32o"]] < 0.01
    assert sol.call[0, I["A2o"]] > 0.99 and sol.call[0, I["T8o"]] < 0.01


def test_ranges_tighten_with_depth_and_players():
    for k in (1, 3):
        pushes = [solve(symmetric_spot(k, s)).push_percent() for s in (5, 10, 15, 20)]
        assert pushes == sorted(pushes, reverse=True) and len(set(pushes)) == 4
    calls = [solve(symmetric_spot(1, s)).call_percent(0) for s in (5, 10, 15, 20)]
    assert calls == sorted(calls, reverse=True)
    pushes = [solve(symmetric_spot(k, 10)).push_percent() for k in (1, 2, 3, 5, 8)]
    assert pushes == sorted(pushes, reverse=True) and len(set(pushes)) == 5


def test_ante_widens_the_push_range():
    plain = solve(symmetric_spot(3, 10, players=9)).push_percent()
    antes = solve(symmetric_spot(3, 10, ante=0.125, players=9)).push_percent()
    assert antes > plain + 3


SPOTS = [
    symmetric_spot(1, 10),
    symmetric_spot(3, 10),
    symmetric_spot(8, 10),
    symmetric_spot(8, 20),
    symmetric_spot(5, 4, ante=0.125, players=9),
    PushFoldSpot(37.6, 0.0, (3.5, 2.45, 2.95), (0.0, 0.5, 1.0), players=6),   # big stack vs three short ones
    PushFoldSpot(12.0, 0.0, (1.5, 9.0, 14.0), (0.0, 0.5, 1.0)),   # the first caller calls almost anything
    PushFoldSpot(12.0, 0.0, (0.6, 9.0, 14.0), (0.0, 0.5, 1.0)),   # ... or everything
    # A 1.35 BB stack calls everything; the averaged early iterates leave it folding ~1e-6 of the time.
    PushFoldSpot(19.0, 0.0, (1.35, 3.3, 11.8, 13.65, 2.6), (0.0, 0.0, 0.0, 0.5, 1.0), players=6),
]


@pytest.mark.parametrize("spot", SPOTS)
def test_equilibrium(spot):
    sol = solve(spot)
    assert sol.exploitability < 0.01
    assert sol.exploitability < gto.SOLVE_TOLERANCE
    # Away from indifference every class plays a pure strategy matching the sign of its EV.
    for freq, ev in [(sol.push, sol.push_ev)] + list(zip(sol.call, sol.call_ev)):
        assert np.all(freq[ev > 0.05] >= 0.99)
        assert np.all(freq[ev < -0.05] <= 0.01)
    assert 0 < sol.push_percent() <= 100


def test_rarely_reached_caller_still_best_responds():
    # The blinds only face the shove when the 1.5 BB stack folds, but their charts are still best responses.
    sol = solve(SPOTS[-3])
    assert sol.call_percent(0) > 90 and sol.push_percent() > 80
    assert sol.call[2, I["AA"]] == 1 and sol.call[2, I["72o"]] == 0
    assert 40 < sol.call_percent(2) < 80
    # Never reached at all: they respond to the whole push range.
    sol = solve(SPOTS[-2])
    assert sol.call_percent(0) == 100
    assert np.isfinite(sol.call_ev).all() and 40 < sol.call_percent(2) < 80
    # Reached only through the leftovers of early iterates: still the whole push range, not that noise.
    sol = solve(SPOTS[-1])
    assert sol.call_percent(0) > 99.9 and sol.iterations < gto.SOLVE_MAX_ITERATIONS
    assert sol.call_ev[2, I["J2s"]] > 0 and sol.call[2, I["J2s"]] > 0.99


def test_solve_is_fast_and_cached():
    spot = symmetric_spot(8, 12.5)
    start = time.perf_counter()
    sol = solve.__wrapped__(spot)
    assert time.perf_counter() - start < 1.0
    assert sol.exploitability < 0.01 and sol.call.shape == (8, 169)
    assert solve(spot) is solve(spot)
    with pytest.raises(ValueError):
        solve(spot).push[0] = 0.5        # cached arrays are read-only


# ---- Decisions on the sample tournament -----------------------------------------------------

@pytest.mark.parametrize("number, cls, behind", [(32, "A2o", 3), (33, "A5s", 2)])
def test_sample_missed_shoves(hand, number, cls, behind):
    # The hero folds first in as a big stack with only tiny stacks left to act (hand 33 has a dead SB).
    h = hand(number)
    [d] = nash_decisions(h)
    assert (d.situation, d.source, d.position, d.hand_class, d.action) == ("push", "nash", "CO", cls, "fold")
    assert d.players_behind == behind and d.eff_stack_bb <= 6
    assert d.chart_action == "push" and d.freqs["push"] > 0.99
    assert d.verdict == "deviates" and d.action_freq < 0.01
    assert d.ev_loss_bb > 0.2
    assert d.ev_loss_bb == pytest.approx(d.evs[I[cls]], abs=1e-3)
    assert d.title.startswith("CO first in · ") and d.title.endswith(f"{behind} left to act")
    assert len(d.grid["push"]) == 169 and d.to_row()["source"] == "nash"


def test_sample_fold_that_agrees(hand):
    [d] = nash_decisions(hand(24))      # SB folds T2o against a 7.1 BB big blind
    assert (d.situation, d.action, d.players_behind, d.verdict, d.ev_loss_bb) == ("push", "fold", 1, "agrees", 0)
    assert d.title == "SB first in · 7.1 BB · 1 left to act"


def test_sample_facing_a_shove(hand):
    h = hand(31)                         # HJ shoves 2.3 BB, hero folds T9o on the button
    [d] = nash_decisions(h)
    assert (d.situation, d.action, d.players_behind, d.title) == ("call", "fold", 4, "BTN vs HJ shove · 2.3 BB")
    assert d.verdict == "deviates" and d.ev_loss_bb > 0
    bb_player = next(p for p in h.players if p.position == "BB")
    assert nash_decisions(h, bb_player.id) == []       # cards not shown

    h = hand(33)                         # the big blind calls the button's shove with A7o
    bb_player = next(p for p in h.players if p.position == "BB")
    [d] = nash_decisions(h, bb_player.id)
    assert (d.situation, d.action, d.hand_class, d.verdict) == ("call", "call", "A7o", "agrees")


def test_sample_titles_use_the_chip_stacks(hand):
    # 8,892 chips at a 1,500 big blind is 5.93 BB: solved as 5.95 but titled 5.9, as in the replay.
    [d] = nash_decisions(hand(33))
    assert (d.title, d.eff_stack_bb) == ("CO first in · 5.9 BB · 2 left to act", 5.93)
    h = hand(32)
    small_blind = next(p for p in h.players if p.position == "SB")
    [d] = nash_decisions(h, small_blind.id)     # 2,430 chips at 1,000
    assert d.title == "SB vs BTN shove · 2.4 BB"


def test_sample_off_chart_and_skipped(session, hand):
    [d] = nash_decisions(hand(39))      # min-raise from the SB at 10.5 BB
    assert (d.action, d.verdict, d.action_freq, d.ev_loss_bb) == ("raise", "off-chart", 0.0, None)
    assert nash_decisions(hand(7)) == []                 # first in, but 30 BB deep
    for h in session.hands:
        if h.game != "holdem":
            assert all(nash_decisions(h, p.id) == [] for p in h.players)


# ---- Synthetic spots -------------------------------------------------------------------------

def nine_handed(hero_cards, events, stack=160):
    """Nine players at 8 BB, dealer seat 7: hero seat 1 is UTG, 8 is SB, 9 is BB."""
    players = [(s, stack, hero_cards if s == 1 else None) for s in range(1, 10)]
    return make_hand(players, dealer=7, events=events)


def test_first_in_push_and_limp():
    shove = lambda cards: nine_handed(cards, [sb(8, 10), bb(9, 20), bet(1, 160, allin=True)]
                                      + [fold(s) for s in range(2, 10)] + [ret(1, 140), END, win(1, 50)])
    [d] = nash_decisions(shove(["7c", "2d"]))
    assert (d.action, d.position, d.players_behind, d.verdict) == ("push", "UTG", 8, "deviates")
    assert d.ev_loss_bb == pytest.approx(-d.evs[I["72o"]], abs=1e-3) and d.ev_loss_bb > 0
    expected = solve(PushFoldSpot(8.0, 0.0, (8.0,) * 8, (0.0,) * 6 + (0.5, 1.0), players=9))
    assert d.grid["push"] == pytest.approx(list(expected.push))

    [d] = nash_decisions(shove(["As", "Ah"]))
    assert (d.action, d.verdict, d.ev_loss_bb, d.freqs) == ("push", "agrees", 0, {"push": 1.0, "fold": 0.0})

    limp = nine_handed(["As", "Ks"], [sb(8, 10), bb(9, 20), call(1, 20)] + [fold(s) for s in range(2, 10)]
                       + [END, win(1, 50)])
    [d] = nash_decisions(limp)
    assert (d.action, d.verdict, d.action_freq, d.ev_loss_bb) == ("limp", "off-chart", 0.0, None)
    assert d.chart_action == "push"


def test_completing_against_an_all_in_big_blind_is_a_push():
    # The BB posted all-in for exactly 1 BB, so nobody behind can put in more: the SB's call is a shove.
    h = make_hand([(1, 2000, None), (2, 200, ["Kd", "7c"]), (3, 20, None)], dealer=1, hero="p2",
                  events=[sb(2, 10), bb(3, 20, allin=True), fold(1), call(2, 20), END, win(2, 40)])
    [d] = nash_decisions(h)
    assert (d.situation, d.action, d.verdict, d.ev_loss_bb) == ("push", "push", "agrees", 0)


def bb_vs_sb_shove(bb_cards, bb_calls):
    """Three players at 10 BB, dealer seat 2: the button folds, the SB (seat 3) shoves, the hero is the BB."""
    players = [(1, 200, bb_cards), (2, 200, None), (3, 200, None)]
    events = [sb(3, 10), bb(1, 20), fold(2), bet(3, 200, allin=True)]
    events += [call(1, 200, allin=True), END, win(1, 400)] if bb_calls else [fold(1), ret(3, 180), END, win(3, 40)]
    return make_hand(players, dealer=2, events=events)


def test_big_blind_facing_small_blind_shove():
    h = bb_vs_sb_shove(["Kd", "9c"], bb_calls=False)
    [d] = nash_decisions(h)
    assert (d.situation, d.position, d.action, d.players_behind) == ("call", "BB", "fold", 1)
    assert d.title == "BB vs SB shove · 10.0 BB" and d.eff_stack_bb == 10
    assert d.chart_action == "call" and d.verdict == "deviates" and d.ev_loss_bb > 0
    expected = solve(PushFoldSpot(10.0, 0.5, (10.0,), (1.0,), players=3))
    assert d.grid["call"] == pytest.approx(list(expected.call[0]))
    assert d.evs == pytest.approx(list(expected.call_ev[0]))

    [d] = nash_decisions(bb_vs_sb_shove(["Kd", "9c"], bb_calls=True))
    assert (d.action, d.verdict, d.ev_loss_bb) == ("call", "agrees", 0)
    [d] = nash_decisions(bb_vs_sb_shove(["7d", "2c"], bb_calls=True))
    assert (d.action, d.verdict) == ("call", "deviates") and d.ev_loss_bb > 0


def test_dead_small_blind():
    # Seat 3 (the small blind) is empty: the button acts with only the BB behind and 1 BB in the pot.
    players = [(1, 200, None), (2, 160, ["As", "5s"]), (4, 200, None)]
    h = make_hand(players, dealer=2, events=[bb(4, 20), fold(1), fold(2), END, win(4, 20)], hero="p2")
    [d] = nash_decisions(h)
    assert (d.position, d.players_behind, d.action, d.verdict) == ("BTN", 1, "fold", "deviates")
    expected = solve(PushFoldSpot(8.0, 0.0, (10.0,), (1.0,), players=3))
    assert d.grid["push"] == pytest.approx(list(expected.push))
    # Without the SB's half blind in the pot the push range is tighter than the SB's against the BB.
    assert range_percent(d.grid["push"]) < solve(PushFoldSpot(8.0, 0.5, (10.0,), (1.0,))).push_percent()


def test_antes_are_taken_off_the_stacks():
    raw = raw_hand([(1, 205, ["Qs", "Js"]), (2, 405, None), (3, 305, None)], dealer=2,
                   events=[sb(3, 10), bb(1, 20), bet(2, 400, allin=True), fold(3), fold(1), ret(2, 380), END,
                           win(2, 55)])
    raw["ante"] = 5
    [d] = nash_decisions(parse_hand(raw, hero_id="p1"))
    expected = solve(PushFoldSpot(20.0, 0.0, (15.0, 10.0), (0.5, 1.0), ante=0.25, players=3))
    assert (d.situation, d.title) == ("call", "BB vs BTN shove · 10.0 BB")
    assert d.grid["call"] == pytest.approx(list(expected.call[1]))


def test_not_compared():
    plo = make_hand([(1, 200, ["As", "Ah", "Kd", "Kc"]), (2, 200, None)], dealer=1, game="omaha",
                    events=[sb(1, 10), bb(2, 20), fold(1), ret(2, 10), END, win(2, 20)])
    assert nash_decisions(plo) == []
    unknown = make_hand([(1, 200, None), (2, 200, ["Qs", "Qh"])], dealer=1,
                        events=[sb(1, 10), bb(2, 20), fold(1), ret(2, 10), END, win(2, 20)])
    assert nash_decisions(unknown) == []                  # the hero's cards are not known
    assert nash_decisions(unknown, "p3") == []            # no such player
    raw = raw_hand([(1, 200, ["As", "Ah"]), (2, 200, None)], dealer=1,
                   events=[sb(1, 10), bb(2, 20), fold(1), ret(2, 10), END, win(2, 20)])
    raw["straddleSeat"] = 2
    assert nash_decisions(parse_hand(raw, hero_id="p1")) == []
    del raw["straddleSeat"]
    raw["doubleBoard"] = True
    assert nash_decisions(parse_hand(raw, hero_id="p1")) == []
