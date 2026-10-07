import pytest

from conftest import END, bb, bet, call, fold, make_hand, ret, sb, win
from pokeranalyzer.leaks import all_flags, call_percent, hand_flags, push_percent
from pokeranalyzer.ranges import hand_class, parse_range, percentile, top_percent


def kinds(h):
    return [f.kind for f in hand_flags(h)]


# ---- Ranges ---------------------------------------------------------------------------------

def test_hand_class():
    assert hand_class(["Kh", "Ah"]) == "AKs"
    assert hand_class(["7d", "7c"]) == "77"
    assert hand_class(["2c", "Td"]) == "T2o"


@pytest.mark.parametrize("text, n", [("77+", 8), ("ATs+", 4), ("A2s-A5s", 4), ("KQ", 2), ("22-44", 3),
                                     ("66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo", 25)])
def test_parse_range(text, n):
    assert len(parse_range(text)) == n


def test_percentiles():
    assert percentile("AA") == pytest.approx(100 * 6 / 1326)
    assert percentile("32o") == 100
    assert len(top_percent(100)) == 169
    assert percentile(["As", "Kd"]) < percentile(["9s", "8s"])


def test_chart_widens_with_fewer_players_and_shorter_stacks():
    assert push_percent(1, 10) > push_percent(2, 10) > push_percent(8, 10)
    assert push_percent(3, 5) > push_percent(3, 10) > push_percent(3, 15)
    assert push_percent(1, 2) == 100
    assert call_percent(1, 10) > call_percent(4, 10)


# ---- Flags on the sample -------------------------------------------------------------------

def test_sample_flags(session):
    flags = all_flags(session.hands)
    numbers = {h.id: h.number for h in session.hands}
    by_kind = {}
    for f in flags:
        by_kind.setdefault(f.kind, []).append(numbers[f.hand_id])
    # A2o and A5s folded as the big stack with only tiny stacks left to act.
    assert by_kind["missed_shove"] == ["32", "33"]
    assert len(by_kind["limp"]) == 8
    assert set(by_kind) == {"limp", "missed_shove"}


# ---- Synthetic spots -------------------------------------------------------------------------

def nine_handed(hero_cards, events, hero_seat=1, stack=2000):
    """Nine players, dealer seat 7: seat 1 is UTG, 6 is CO, 8 is SB, 9 is BB."""
    players = [(s, stack, hero_cards) if s == hero_seat else (s, 2000, None) for s in range(1, 10)]
    return make_hand(players, dealer=7, events=events, hero=f"p{hero_seat}")


def test_loose_early_position_open():
    h = nine_handed(["7c", "2d"], [sb(8, 10), bb(9, 20), bet(1, 60)] + [fold(s) for s in range(2, 10)]
                    + [ret(1, 40), END, win(1, 50)])
    assert h.hero.position == "UTG"
    assert kinds(h) == ["loose_ep"]


def test_good_early_position_open_is_not_flagged():
    h = nine_handed(["Ac", "Qd"], [sb(8, 10), bb(9, 20), bet(1, 60)] + [fold(s) for s in range(2, 10)]
                    + [ret(1, 40), END, win(1, 50)])
    assert kinds(h) == []


def test_open_limp():
    h = nine_handed(["9c", "8c"], [sb(8, 10), bb(9, 20), call(1, 20)] + [fold(s) for s in range(2, 8)]
                    + [fold(8), fold(9), END, win(1, 50)])
    assert "limp" in kinds(h)


def test_bb_folds_strong_hand_to_min_raise():
    players = [(1, 2000, ["Ah", "Jd"]), (2, 2000, None), (3, 2000, None)]
    h = make_hand(players, dealer=2, events=[sb(3, 10), bb(1, 20), bet(2, 40), fold(3), fold(1), ret(2, 20), END,
                                             win(2, 70)])
    assert h.hero.position == "BB"
    assert kinds(h) == ["bb_fold_minraise"]


def test_missed_and_loose_shoves():
    short = 160  # 8 BB
    fold_ace = nine_handed(["As", "9d"], [sb(8, 10), bb(9, 20)] + [fold(s) for s in range(1, 9)]
                           + [ret(9, 10), END, win(9, 20)], hero_seat=6, stack=short)
    assert fold_ace.hero.position == "CO" and kinds(fold_ace) == ["missed_shove"]

    shove_junk = nine_handed(["7c", "2d"], [sb(8, 10), bb(9, 20), bet(1, 160, allin=True)]
                             + [fold(s) for s in range(2, 10)] + [ret(1, 140), END, win(1, 50)], stack=short)
    assert kinds(shove_junk) == ["loose_shove"]

    min_raise = nine_handed(["As", "Ks"], [sb(8, 10), bb(9, 20), bet(1, 40)] + [fold(s) for s in range(2, 10)]
                            + [ret(1, 20), END, win(1, 50)], stack=120)
    assert kinds(min_raise) == ["small_open_short"]


def test_folding_big_blind_to_a_small_blind_shove():
    players = [(1, 200, ["Kd", "9c"]), (2, 200, None), (3, 200, None)]  # 10 BB each
    h = make_hand(players, dealer=2, events=[sb(3, 10), bb(1, 20), fold(2), bet(3, 200, allin=True), fold(1),
                                             ret(3, 180), END, win(3, 40)])
    assert kinds(h) == ["tight_fold_shove"]
