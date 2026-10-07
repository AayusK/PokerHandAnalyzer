import json
import re

import pytest

from conftest import END, bb, bet, call, fold, make_hand, ret, sb, win
from pokeranalyzer import leaks
from pokeranalyzer.charts import load_charts
from pokeranalyzer.leaks import all_flags, hand_flags
from pokeranalyzer.ranges import hand_class, parse_range, percentile, top_percent


@pytest.fixture(autouse=True)
def no_user_charts(tmp_path, monkeypatch):
    """Keep the user's own chart files (data/charts) out of these tests."""
    monkeypatch.setenv("POKER_CHARTS", str(tmp_path / "no-charts"))


def kinds(h, charts=None):
    return [f.kind for f in hand_flags(h, charts)]


def ev_lost(message):
    return float(re.search(r"EV lost: ([\d.]+) BB", message).group(1))


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


def test_kinds_cover_every_flag():
    assert set(leaks.KINDS) == {"limp", "loose_ep", "bb_fold_minraise", "missed_shove", "loose_shove",
                                "small_open_short", "loose_call_shove", "tight_fold_shove", "chart_deviation"}
    for name in ("PUSH_PCT_AT_10BB", "CALL_PCT_AT_10BB", "push_percent", "call_percent", "CHART_TOLERANCE"):
        assert not hasattr(leaks, name)


# ---- Flags on the sample -------------------------------------------------------------------

def test_sample_flags(session):
    flags = all_flags(session.hands)
    numbers = {h.id: h.number for h in session.hands}
    by_kind = {}
    for f in flags:
        by_kind.setdefault(f.kind, []).append(numbers[f.hand_id])
    # Push/fold, against the Nash equilibrium of each spot: A2o and A5s folded as the big stack with only
    # tiny stacks left to act, and T9o folded on the button to a 2.3 BB shove.
    assert by_kind["missed_shove"] == ["32", "33"]
    assert by_kind["tight_fold_shove"] == ["31"]
    assert len(by_kind["limp"]) == 8
    assert set(by_kind) == {"limp", "missed_shove", "tight_fold_shove"}

    nash = {numbers[f.hand_id]: f for f in flags if f.kind in ("missed_shove", "tight_fold_shove")}
    assert nash["32"].message.startswith("Folded A2o (CO first in · 3.5 BB · 3 left to act)")
    assert "shoves it 100% of the time" in nash["32"].message
    assert nash["33"].message.startswith("Folded A5s (CO first in")
    assert nash["31"].message.startswith("Folded T9o (BTN vs HJ shove · 2.3 BB)")
    assert "calls it 100% of the time" in nash["31"].message
    assert ev_lost(nash["32"].message) == pytest.approx(0.60, abs=0.05)
    assert ev_lost(nash["33"].message) == pytest.approx(0.42, abs=0.05)
    assert ev_lost(nash["31"].message) == pytest.approx(0.37, abs=0.05)
    assert nash["32"].severity == "high" and nash["33"].severity == nash["31"].severity == "medium"


def test_sample_spots_that_agree_are_not_flagged(hand):
    # SB folds T2o at 7.1 BB heads-up with the BB, BTN folds J4o at 6.9 BB: the equilibrium folds both.
    assert "missed_shove" not in kinds(hand(24))
    assert kinds(hand(40)) == []


# ---- Synthetic spots -------------------------------------------------------------------------

def nine_handed(hero_cards, events, hero_seat=1, stack=2000):
    """Nine players, dealer seat 7: seat 1 is UTG, 6 is CO, 8 is SB, 9 is BB."""
    players = [(s, stack, hero_cards) if s == hero_seat else (s, 2000, None) for s in range(1, 10)]
    return make_hand(players, dealer=7, events=events, hero=f"p{hero_seat}")


def folded_round(hero_cards, hero_seat, stack):
    """Everyone folds to the big blind, who wins the small blind."""
    return nine_handed(hero_cards, [sb(8, 10), bb(9, 20)] + [fold(s) for s in range(1, 9)]
                       + [ret(9, 10), END, win(9, 20)], hero_seat=hero_seat, stack=stack)


def utg_shove(hero_cards, stack=160):
    return nine_handed(hero_cards, [sb(8, 10), bb(9, 20), bet(1, stack, allin=True)] + [fold(s) for s in range(2, 10)]
                       + [ret(1, stack - 20), END, win(1, 50)], stack=stack)


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


def test_missed_shove():
    fold_ace = folded_round(["As", "9d"], hero_seat=6, stack=160)  # 8 BB
    assert fold_ace.hero.position == "CO"
    [flag] = hand_flags(fold_ace)
    assert flag.kind == "missed_shove" and flag.severity == "high"
    assert flag.message.startswith("Folded A9o (CO first in · 8.0 BB · 3 left to act)")
    assert "shoves it 100% of the time" in flag.message
    assert ev_lost(flag.message) == pytest.approx(0.65, abs=0.05)


def test_loose_shove():
    [flag] = hand_flags(utg_shove(["7c", "2d"]))
    assert flag.kind == "loose_shove"
    assert flag.message.startswith("Shoved 72o (UTG first in · 8.0 BB · 8 left to act)")
    assert "shoves it 0% of the time" in flag.message
    assert ev_lost(flag.message) > 0.5


def test_push_fold_play_that_agrees_with_nash():
    assert kinds(utg_shove(["As", "Ad"])) == []
    assert kinds(folded_round(["7c", "2d"], hero_seat=1, stack=160)) == []


def test_deviation_below_the_ev_threshold_is_not_flagged(monkeypatch):
    monkeypatch.setattr(leaks, "MIN_FLAG_EV_BB", 0.8)
    assert kinds(folded_round(["As", "9d"], hero_seat=6, stack=160)) == []  # loses about 0.65 BB
    assert kinds(utg_shove(["7c", "2d"])) == ["loose_shove"]                # loses about 1 BB


def test_small_open_when_short():
    min_raise = nine_handed(["As", "Ks"], [sb(8, 10), bb(9, 20), bet(1, 40)] + [fold(s) for s in range(2, 10)]
                            + [ret(1, 20), END, win(1, 50)], stack=120)
    assert kinds(min_raise) == ["small_open_short"]
    limp = nine_handed(["As", "Ks"], [sb(8, 10), bb(9, 20), call(1, 20)] + [fold(s) for s in range(2, 10)]
                       + [END, win(1, 50)], stack=120)
    assert kinds(limp) == ["small_open_short"]
    # Completing against a big blind that posted all-in is a shove, not a limp.
    all_in_bb = make_hand([(1, 2000, None), (2, 200, ["Kd", "7c"]), (3, 20, None)], dealer=1, hero="p2",
                          events=[sb(2, 10), bb(3, 20, allin=True), fold(1), call(2, 20), END, win(2, 40)])
    assert kinds(all_in_bb) == []


def test_folding_big_blind_to_a_small_blind_shove():
    players = [(1, 200, ["Kd", "9c"]), (2, 200, None), (3, 200, None)]  # 10 BB each
    h = make_hand(players, dealer=2, events=[sb(3, 10), bb(1, 20), fold(2), bet(3, 200, allin=True), fold(1),
                                             ret(3, 180), END, win(3, 40)])
    [flag] = hand_flags(h)
    assert flag.kind == "tight_fold_shove"
    assert flag.message.startswith("Folded K9o (BB vs SB shove · 10.0 BB)")
    assert ev_lost(flag.message) > 0.5


def test_big_blind_calls_a_shove_too_loose():
    players = [(s, 200, ["7c", "2d"] if s == 9 else None) for s in range(1, 10)]  # 10 BB each
    h = make_hand(players, dealer=7, hero="p9",
                  events=[sb(8, 10), bb(9, 20), bet(1, 200, allin=True)] + [fold(s) for s in range(2, 9)]
                  + [call(9, 180, allin=True), END, win(1, 410)])
    [flag] = hand_flags(h)
    assert flag.kind == "loose_call_shove"
    assert flag.message.startswith("Called 72o (BB vs UTG shove · 10.0 BB)")
    assert "calls it 0% of the time" in flag.message
    assert ev_lost(flag.message) > 1


# ---- User charts ----------------------------------------------------------------------------

CHART = {
    "name": "Test chart",
    "source": "made up for the tests",
    "spots": [
        {"situation": "rfi", "position": "UTG", "stack_bb": [20, 200], "actions": {"raise": "77+, AJs+, AQo+"}},
        {"situation": "vs_open", "position": "BB", "actions": {"3bet": "QQ+", "call": "22-JJ, AJo:0.75"}},
    ],
}


@pytest.fixture
def charts(tmp_path):
    folder = tmp_path / "charts"
    folder.mkdir()
    (folder / "test.json").write_text(json.dumps(CHART))
    loaded, errors = load_charts(folder)
    assert errors == []
    return loaded


def utg_open(cards):
    return nine_handed(cards, [sb(8, 10), bb(9, 20), bet(1, 60)] + [fold(s) for s in range(2, 10)]
                       + [ret(1, 40), END, win(1, 50)])


def bb_folds_to_utg_open(cards):
    return nine_handed(cards, [sb(8, 10), bb(9, 20), bet(1, 60)] + [fold(s) for s in range(2, 10)]
                       + [ret(1, 40), END, win(1, 50)], hero_seat=9)


def test_chart_deviation(charts):
    flags = hand_flags(utg_open(["7c", "2d"]), charts)
    assert [f.kind for f in flags] == ["loose_ep", "chart_deviation"]
    assert flags[1].message == ('Raised 72o (UTG raise first in · 100.0 BB); your chart "Test chart" raises it 0% '
                                "of the time (fold 100%)")

    folded = folded_round(["Ah", "Ad"], hero_seat=1, stack=2000)
    [flag] = hand_flags(folded, charts)
    assert flag.kind == "chart_deviation" and flag.message.startswith("Folded AA (UTG raise first in · 100.0 BB)")
    assert "folds it 0% of the time (raise 100%)" in flag.message

    [flag] = hand_flags(bb_folds_to_utg_open(["Qh", "Qd"]), charts)
    assert flag.message.startswith("Folded QQ (BB vs UTG open · 100.0 BB)")


def test_frequency_just_under_the_flag_threshold_is_not_shown_as_10_percent(tmp_path):
    (tmp_path / "c.json").write_text(json.dumps({"name": "Mine", "spots": [
        {"situation": "rfi", "position": "UTG", "actions": {"raise": "AQo:0.096"}}]}))
    [flag] = hand_flags(utg_open(["Ac", "Qd"]), load_charts(tmp_path)[0])
    assert flag.message.endswith('your chart "Mine" raises it 9.6% of the time (fold 90.4%, raise 9.6%)')


def test_chart_agreement_and_mixed_play_are_not_flagged(charts):
    assert kinds(utg_open(["Ac", "Qd"]), charts) == []
    assert kinds(bb_folds_to_utg_open(["7c", "2d"]), charts) == []
    assert kinds(bb_folds_to_utg_open(["Ac", "Jd"]), charts) == []  # the chart folds AJo 25%: mixed
    assert kinds(utg_open(["7c", "2d"]), []) == ["loose_ep"]          # no charts given


def test_charts_are_loaded_from_the_charts_folder(tmp_path, monkeypatch):
    folder = tmp_path / "my-charts"
    folder.mkdir()
    (folder / "test.json").write_text(json.dumps(CHART))
    h = utg_open(["7c", "2d"])
    assert kinds(h) == ["loose_ep"]
    monkeypatch.setenv("POKER_CHARTS", str(folder))
    assert kinds(h) == ["loose_ep", "chart_deviation"]
    assert [f.kind for f in all_flags([h])] == ["loose_ep", "chart_deviation"]
