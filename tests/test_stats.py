import pytest

from pokeranalyzer.stats import flags_frame, player_hand_flags, stack_bucket, summarize


def test_open_raise_and_cbet(hand):
    # Hand 3: two limpers, hero raises on the button, HJ calls, hero c-bets, HJ folds.
    h = hand(3)
    hero = player_hand_flags(h, h.hero_id)
    assert hero["vpip"] and hero["pfr"] and not hero["limp"]
    assert not hero["open_opp"]                      # limpers in front: not a raise-first-in spot
    assert not hero["three_bet_opp"]                 # limps are not a raise
    assert hero["cbet_opp"] and hero["cbet"]
    assert hero["saw_flop"] and not hero["wtsd"]

    hj = player_hand_flags(h, h.at_seat(2).id)
    assert hj["vpip"] and not hj["pfr"] and hj["limp"]
    assert hj["three_bet_opp"] and not hj["three_bet"]
    assert hj["fold_to_cbet_opp"] and hj["fold_to_cbet"]

    lj = player_hand_flags(h, h.at_seat(1).id)
    assert lj["limp"] and not lj["saw_flop"] and not lj["fold_to_cbet_opp"]


def test_small_blind_complete(hand):
    h = hand(2)
    hero = player_hand_flags(h, h.hero_id)
    assert hero["position"] == "SB"
    assert hero["vpip"] and hero["limp"] and hero["open_opp"] and not hero["open_raise"] and not hero["pfr"]
    assert hero["saw_flop"] and not hero["wtsd"]                 # folded the river
    assert hero["post_checks"] == 2 and hero["post_calls"] == 2 and hero["post_folds"] == 1


def test_walk_has_no_preflop_decision(hand):
    h = hand(1)
    hero = player_hand_flags(h, h.hero_id)
    assert hero["position"] == "BB" and not hero["pf_decision"] and not hero["vpip"]
    assert hero["net"] == 10


def test_three_bet(hand):
    h = hand(4)
    bb = player_hand_flags(h, h.at_seat(7).id)
    assert bb["three_bet_opp"] and bb["three_bet"] and bb["pfr"]
    hero = player_hand_flags(h, h.hero_id)
    assert hero["pfr"] and not hero["three_bet_opp"] and hero["net"] == -80


def test_bb_vs_min_raise(hand):
    h = hand(35)  # SB raises to 2 BB, hero 3-bets from the big blind
    hero = player_hand_flags(h, h.hero_id)
    assert hero["bb_minraise_opp"] and not hero["bb_minraise_fold"] and hero["three_bet"]


def test_showdown_flags(hand):
    h = hand(11)  # hero all-in preflop in PLO and wins
    hero = player_hand_flags(h, h.hero_id)
    assert hero["at_showdown"] and hero["wtsd"] and hero["wsd"]


def test_summary_matches_manual_count(session):
    # Hands 1-4 by hand: walk, SB complete, BTN raise over limpers, CO iso-raise.
    df = flags_frame(session.hands[:4], session.hero_id)
    s = summarize(df).iloc[0]
    assert s["hands"] == 4
    assert (s["vpip_n"], s["vpip"]) == (3, 100.0)
    assert s["pfr"] == pytest.approx(200 / 3)
    assert s["limp"] == pytest.approx(100 / 3)
    assert (s["cbet_n"], s["cbet"]) == (1, 100.0)
    assert s["net_bb"] == pytest.approx(0.5 - 3 + 7.5 - 2)


def test_summary_by_position_has_sample_sizes(session):
    df = flags_frame(session.hands, session.hero_id)
    by = summarize(df, "position")
    assert by["hands"].sum() == 40
    assert list(by["position"])[-2:] == ["SB", "BB"]
    assert all(f"{k}_n" in by for k in ("vpip", "pfr", "three_bet", "cbet", "fold_to_cbet", "wtsd", "wsd", "af"))


def test_flags_for_every_player(session):
    df = flags_frame(session.hands)
    assert len(df) == sum(h.num_players for h in session.hands)
    assert df.groupby("hand_id")["net"].sum().eq(0).all()


@pytest.mark.parametrize("bb, label", [(5, "<10 BB"), (10, "10–20 BB"), (39.9, "20–40 BB"), (100, "100+ BB")])
def test_stack_bucket(bb, label):
    assert stack_bucket(bb) == label
