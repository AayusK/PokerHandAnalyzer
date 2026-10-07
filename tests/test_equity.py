import pytest

from pokeranalyzer.equity import allin_results, build_pots, equity


def test_preflop_aa_vs_kk():
    eq = equity([["Ah", "Ad"], ["Kh", "Kd"]], iterations=20_000)
    assert eq[0] == pytest.approx(0.82, abs=0.01)
    assert sum(eq) == pytest.approx(1.0)


def test_flop_is_exact():
    eq = equity([["Ah", "Kh"], ["7c", "7d"]], ["2h", "7h", "Qs"])
    assert eq[0] == pytest.approx(253 / 990, abs=1e-12)


def test_tie_is_split():
    eq = equity([["As", "Kd"], ["Ah", "Kc"]], ["2c", "7d", "9h", "Js"])
    assert eq == [pytest.approx(0.5), pytest.approx(0.5)]


def test_plo_must_use_two_hole_cards():
    board = ["As", "Ks", "Qs", "2s", "7d"]
    # One spade in hand is no flush in PLO; a pocket pair of nines wins.
    assert equity([["Js", "3c", "4d", "8h"], ["9h", "9d", "5c", "6c"]], board, game="plo") == [0.0, 1.0]


def test_monte_carlo_is_seeded():
    holes = [["Ah", "Ad", "Kc", "Qc"], ["Jh", "Th", "9s", "8s"]]
    assert equity(holes, game="plo", iterations=2000, seed=7) == equity(holes, game="plo", iterations=2000, seed=7)


def test_duplicate_cards_rejected():
    with pytest.raises(ValueError):
        equity([["Ah", "Ad"], ["Ah", "Kd"]])


def test_side_pots_match_the_export(hand):
    for number, table in [(10, None), (5, None), (6, None), (9, None), (32, None)]:
        h = hand(number, table)
        from_export = [pot for _, pot in sorted({(a.pot_index, a.pot) for a in h.awards})]
        assert [p.amount for p in build_pots(h)] == from_export


def test_allin_results_sum_to_the_pot(session):
    checked = 0
    for h in session.hands:
        results = allin_results(h, iterations=3000)
        if not results:
            continue
        checked += 1
        assert sum(r.expected for r in results) == pytest.approx(h.pot)
        assert sum(r.luck for r in results) == pytest.approx(0, abs=1e-6)
    assert checked >= 10


def test_hero_allin_with_aq(hand):
    h = hand(39)  # hero AdQs vs a shove, all in preflop
    hero = next(r for r in allin_results(h, iterations=20_000) if r.player_id == h.hero_id)
    assert hero.street == "preflop" and hero.board == []
    assert hero.equity == pytest.approx(0.74, abs=0.02)
    assert hero.won == h.pot and hero.luck > 0


def test_no_result_without_cards_to_come(hand):
    assert allin_results(hand(3)) == []        # nobody all-in
