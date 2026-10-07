import json
import re
from pathlib import Path

import pytest

from conftest import END, bb, bet, call, fold, make_hand, sb
from pokeranalyzer import gto
from pokeranalyzer.charts import (DEFAULT_DIR, all_decisions, chart_decisions, charts_dir, load_chart, load_charts,
                                  parse_chart)
from pokeranalyzer.ranges import CLASS_INDEX, CLASSES, parse_range, parse_weighted_range

DOCS = Path(__file__).resolve().parents[1] / "docs"


# ---- Weighted ranges ---------------------------------------------------------------------------

def test_weighted_range_weights():
    r = parse_weighted_range("AKs:0.5, 77+:0.25, A2s-A5s:0.75, KQo")
    assert len(r) == 1 + 8 + 4 + 1
    assert r["AKs"] == 0.5 and r["AA"] == r["77"] == 0.25 and "66" not in r
    assert r["A2s"] == r["A5s"] == 0.75 and "A6s" not in r
    assert r["KQo"] == 1.0
    assert parse_weighted_range("") == {}
    assert parse_weighted_range("aks:0.5, kqO") == {"AKs": 0.5, "KQo": 1.0}


def test_weighted_range_percentages():
    r = parse_weighted_range("AK:50, QJs:30%, T9s:1%, 22:1; 33:0, 44:100")
    assert r == pytest.approx({"AKs": 0.5, "AKo": 0.5, "QJs": 0.3, "T9s": 0.01, "22": 1.0, "33": 0.0, "44": 1.0})


def test_weighted_range_later_tokens_override():
    r = parse_weighted_range("77+, 99:0.5, AKs:0.2, AK")
    assert r["88"] == 1.0 and r["99"] == 0.5 and r["AKs"] == r["AKo"] == 1.0
    assert parse_weighted_range("22+:0.5, AA") == {**{c: 0.5 for c in parse_range("22-KK")}, "AA": 1.0}


def test_weighted_range_matches_parse_range_classes():
    text = "66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo, A2s-A5s, 22-44"
    assert set(parse_weighted_range(text)) == parse_range(text)


@pytest.mark.parametrize("text", ["AhKh", "AsKd:0.5", "AKx", "A", "AAs", "10s", "77-A", "AKs-QJs", "A2s-A5o",
                                  "22-A5s", "AA KK", "AKs:", "AKs:half", "AKs:0.5:0.5", "AKs:101", "AKs:-0.1",
                                  "AKs:nan", ":0.5"])
def test_weighted_range_errors(text):
    with pytest.raises(ValueError):
        parse_weighted_range(text)


# ---- Chart files: validation --------------------------------------------------------------------

DROP = object()
GOOD_SPOT = {"situation": "rfi", "position": "UTG", "actions": {"raise": "77+"}}


def chart_data(**top):
    return {k: v for k, v in {"name": "Test", "spots": [GOOD_SPOT], **top}.items() if v is not DROP}


def spot_data(**kw):
    return chart_data(spots=[{k: v for k, v in {**GOOD_SPOT, **kw}.items() if v is not DROP}])


def only_error(tmp_path, content) -> str:
    (tmp_path / "bad.json").write_text(content if isinstance(content, str) else json.dumps(content))
    charts, errors = load_charts(tmp_path)
    assert charts == [] and len(errors) == 1, errors
    assert errors[0].startswith("bad.json: ")
    return errors[0]


VS_OPEN = {"situation": "vs_open", "actions": {"call": "AA"}}

BAD_FILES = [
    ('{"name": "x", "spots": [', "bad JSON at line 1"),
    ("[1, 2]", "must hold a JSON object"),
    ('{"name": "a", "name": "b", "spots": []}', "duplicate key 'name'"),
    ('{"spots": [{"situation": "rfi", "actions": {"raise": "AA", "raise": "KK"}}]}', "duplicate key 'raise'"),
    (chart_data(notes="x"), "unknown key 'notes'"),
    (chart_data(spots=DROP), '"spots" must be a non-empty list'),
    (chart_data(spots=[]), '"spots" must be a non-empty list'),
    (chart_data(spots={"situation": "rfi"}), '"spots" must be a non-empty list'),
    (chart_data(name=""), '"name" must be a non-empty string'),
    (chart_data(name=3), '"name" must be a non-empty string'),
    (chart_data(name="NASH"), "reserved"),
    (chart_data(source=["x"]), '"source" must be a string'),
    (chart_data(spots=["rfi"]), "spots[0]: must be an object"),
    (spot_data(postion="UTG"), "spots[0] (rfi): unknown key 'postion'"),
    (spot_data(situation=DROP), 'spots[0]: missing "situation"'),
    (spot_data(situation="3bet"), "unknown situation '3bet'"),
    (spot_data(situation=["rfi"]), "unknown situation"),
    (spot_data(actions={"3bet": "AA"}), "unknown rfi action '3bet' (allowed: raise, limp, allin)"),
    (spot_data(situation="vs_open", opener="CO", actions={"raise": "AA"}), "unknown vs_open action 'raise'"),
    (spot_data(actions={"raise": "AA", "fold": "72o"}), 'leave out "fold"'),
    (spot_data(actions={}), '"actions" must be an object'),
    (spot_data(actions=DROP), '"actions" must be an object'),
    (spot_data(actions={"raise": ["AA"]}), "actions.raise must be a range string"),
    (spot_data(actions={"raise": "AA, AKx"}), "actions.raise: bad range token 'AKx'"),
    (spot_data(actions={"raise": "AhKh"}), "single combos are not supported"),
    (spot_data(actions={"raise": "AA:2000"}), "weight out of range"),
    (spot_data(actions={"raise": "AA, KK:0.6", "limp": "KK:0.5, QQ:0.5"}), "more than 1 for KK 1.1"),
    (spot_data(actions={"raise": "22+", "limp": "22+:0.1"}), "and 8 more"),
    (spot_data(position="MP"), "unknown position 'MP'"),
    (spot_data(position=DROP, positions=[]), "positions must be a position name or a non-empty list"),
    (spot_data(position=DROP, positions=["UTG", 3]), "positions must be"),
    (spot_data(positions=["UTG"]), 'give "position" or "positions", not both'),
    (spot_data(position="BB"), "the BB can't raise first in"),
    (spot_data(opener="BTN"), '"opener" only applies to vs_open'),
    (spot_data(**VS_OPEN, opener="BB"), "the BB never opens"),
    (spot_data(**VS_OPEN, opener=["CO", "button"]), "unknown opener 'BUTTON'"),
    (spot_data(stack_bb=[60, 20]), "bad stack_bb [60, 20]"),
    (spot_data(stack_bb=[-1, 20]), "bad stack_bb"),
    (spot_data(stack_bb=[20]), "stack_bb must be [low, high]"),
    (spot_data(stack_bb=20), "stack_bb must be [low, high]"),
    (spot_data(stack_bb=["20", 60]), "stack_bb must be [low, high]"),
    (spot_data(stack_bb=[True, 60]), "stack_bb must be [low, high]"),
    ('{"spots": [{"situation": "rfi", "stack_bb": [20, Infinity], "actions": {"raise": "AA"}}]}', "bad stack_bb"),
    (spot_data(stack_bb=[0, 10 ** 400]), "bad stack_bb"),                   # too big for a float
    ('{"spots": [{"situation": "rfi", "players": [2, 1' + "0" * 5000 + '], "actions": {"raise": "AA"}}]}',
     "can't read the chart"),                                                # too many digits for Python's int()
    ("[" * 100_000 + "]" * 100_000, "can't read the chart"),                 # nested too deep
    (spot_data(players=[6.5, 9]), "players must be [low, high] with two whole numbers"),
    (spot_data(players=[9, 6]), "bad players [9, 6]"),
]


@pytest.mark.parametrize("content, message", BAD_FILES)
def test_bad_chart_files(tmp_path, content, message):
    assert message in only_error(tmp_path, content)


def test_unreadable_file(tmp_path):
    (tmp_path / "bad.json").write_bytes(b'{"name": "\xff"}')
    charts, errors = load_charts(tmp_path)
    assert charts == [] and len(errors) == 1 and errors[0].startswith("bad.json: can't read the file")


def test_good_chart_parses():
    chart = parse_chart({"name": " Mine ", "source": "notes", "spots": [
        {"situation": "rfi", "positions": ["utg", "UTG+1"], "stack_bb": [20, 60.5], "players": [8, 9],
         "actions": {"raise": "77+, AKo:0.5", "limp": "AKo:25%", "allin": "66"}},
        {"situation": "vs_open", "opener": "btn", "actions": {"3bet": "QQ+", "call": ""}},
    ]})
    rfi, vs = chart.spots
    assert chart.name == "Mine" and chart.source == "notes" and chart.path is None
    assert rfi.positions == ("UTG", "UTG+1") and rfi.stack_bb == (20.0, 60.5) and rfi.players == (8, 9)
    assert rfi.freqs("AKo") == {"raise": 0.5, "limp": 0.25, "allin": 0.0, "fold": 0.25}
    assert rfi.freqs("72o") == {"raise": 0.0, "limp": 0.0, "allin": 0.0, "fold": 1.0}
    assert rfi.ranges["raise"] == "77+, AKo:0.5" and rfi.index == 0 and vs.index == 1
    assert vs.positions == () and vs.openers == ("BTN",) and vs.stack_bb is None and vs.players is None
    assert vs.actions["call"] == [0.0] * 169
    assert rfi.label() == "rfi · UTG/UTG+1 · 20-60.5 BB · 8-9 players"
    assert vs.label() == "vs_open · any position vs BTN"


# ---- Chart files: directory loading ----------------------------------------------------------------

def write_chart(directory: Path, filename: str, name: str | None = None, spots=None) -> None:
    data = {"spots": spots or [GOOD_SPOT], **({"name": name} if name is not None else {})}
    (directory / filename).write_text(json.dumps(data))


def test_load_charts_from_env_directory(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    write_chart(d, "b.json", "Bravo")
    write_chart(d, "a.json", "Alpha")
    write_chart(d, "nameless.json")
    (d / "c.json").write_text("{oops")
    (d / ".scratch.json").write_text("{oops")
    (d / "README.md").write_text("# my charts")
    monkeypatch.setenv("POKER_CHARTS", str(d))
    assert charts_dir() == d
    charts, errors = load_charts()
    assert [c.name for c in charts] == ["Alpha", "Bravo", "nameless"]
    assert charts[0].path == d / "a.json"
    assert len(errors) == 1 and errors[0].startswith("c.json: bad JSON")


def test_missing_directory_means_no_charts(tmp_path, monkeypatch):
    monkeypatch.setenv("POKER_CHARTS", str(tmp_path / "missing"))
    assert load_charts() == ([], [])
    (tmp_path / "file").write_text("")
    assert load_charts(tmp_path / "file") == ([], [f"{tmp_path / 'file'}: not a directory"])


def test_default_directory(monkeypatch):
    monkeypatch.delenv("POKER_CHARTS", raising=False)
    assert charts_dir() == DEFAULT_DIR == Path(__file__).resolve().parents[1] / "data" / "charts"
    monkeypatch.setenv("POKER_CHARTS", "")
    assert charts_dir() == DEFAULT_DIR


def test_chart_names_must_be_unique(tmp_path):
    write_chart(tmp_path, "a.json", "Same")
    write_chart(tmp_path, "b.json", "Same")
    charts, errors = load_charts(tmp_path)
    assert [c.path.name for c in charts] == ["a.json"]
    assert errors == ["b.json: chart name 'Same' is already used by a.json"]


# ---- Example chart and docs ------------------------------------------------------------------------

def test_example_chart_loads(tmp_path):
    chart = load_chart(DOCS / "example-chart.json")
    assert chart.name == "Format example — not real GTO ranges"
    assert {s.situation for s in chart.spots} == {"rfi", "vs_open"}
    assert all(abs(sum(s.freqs(cls).values()) - 1) < 1e-3 for s in chart.spots for cls in CLASSES)
    (tmp_path / "example-chart.json").write_text((DOCS / "example-chart.json").read_text(encoding="utf-8"))
    charts, errors = load_charts(tmp_path)
    assert errors == [] and len(charts) == 1


def test_example_in_the_docs_is_valid():
    text = (DOCS / "gto-charts.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)```", text, re.S).group(1)
    assert len(parse_chart(json.loads(block)).spots) == 2


# ---- Matching -------------------------------------------------------------------------------------

def table(cards, events, hero_seat=1, stack=2000, n=9, game="th", others=None):
    """n players with 2000 chips (100 BB), the blinds on the last two seats; 9-handed: seat 1 is UTG,
    6 CO, 7 BTN, 8 SB, 9 BB. ``others`` gives other seats' cards."""
    others = others or {}
    players = [(s, stack if s == hero_seat else 2000, cards if s == hero_seat else others.get(s))
               for s in range(1, n + 1)]
    return make_hand(players, dealer=n - 2, events=events, hero=f"p{hero_seat}", game=game)


def first_in(seat, act, n=9):
    """Folded to ``seat``, which does ``act`` (a list of events); everyone after it folds."""
    return ([sb(n - 1, 10), bb(n, 20)] + [fold(s) for s in range(1, seat)] + act
            + [fold(s) for s in range(seat + 1, n + 1)] + [END])


def facing_open(opener, seat, act, open_to=50, allin=False, between=None, n=9):
    """``opener`` raises first in, the seats up to ``seat`` fold (or do ``between``), then ``seat`` does ``act``."""
    middle = between if between is not None else [fold(s) for s in range(opener + 1, seat)]
    return ([sb(n - 1, 10), bb(n, 20)] + [fold(s) for s in range(1, opener)] + [bet(opener, open_to, allin)]
            + middle + act + [END])


def charts_with(*spots, name="Mine"):
    return [parse_chart({"name": name, "spots": list(spots)})]


def rfi(actions, **kw):
    return {"situation": "rfi", "actions": actions, **kw}


def vs_open(actions, **kw):
    return {"situation": "vs_open", "actions": actions, **kw}


UTG_RAISE = charts_with(rfi({"raise": "77+, AKo:0.3"}, position="UTG"))


def only(decisions):
    assert len(decisions) == 1, decisions
    return decisions[0]


def test_rfi_decision():
    h = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]))
    d = only(chart_decisions(h, UTG_RAISE))
    assert (d.hand_id, d.player_id, d.source, d.situation, d.position) == ("h1", "p1", "Mine", "rfi", "UTG")
    assert d.seq == h.decisions("p1")[0].seq
    assert (d.hand_class, d.action, d.chart_action, d.verdict) == ("AA", "raise", "raise", "agrees")
    assert d.freqs == {"raise": 1.0, "fold": 0.0} and d.action_freq == 1.0
    assert d.eff_stack_bb == 100.0 and d.players_behind == 8
    assert d.title == "UTG raise first in · 100.0 BB"
    assert d.ev_loss_bb is None and d.evs is None
    row = d.to_row()
    assert row["source"] == "Mine" and row["evs"] is None and len(json.loads(row["grid"])["raise"]) == 169


def test_grid_has_169_weights_per_action_and_no_fold():
    charts = charts_with(rfi({"raise": "77+, AKo:0.3", "limp": "22-66:0.5", "allin": "A5s"}, position="UTG"))
    d = only(chart_decisions(table(["Ah", "Kd"], first_in(1, [bet(1, 50)])), charts))
    assert set(d.grid) == {"raise", "limp", "allin"}
    assert all(len(v) == 169 for v in d.grid.values())
    assert d.grid["raise"][CLASS_INDEX["AKo"]] == 0.3 and d.grid["limp"][CLASS_INDEX["44"]] == 0.5
    assert d.grid["allin"][CLASS_INDEX["A5s"]] == 1.0 and sum(d.grid["allin"]) == 1.0
    assert sum(d.freqs.values()) == pytest.approx(1.0)
    d.grid["raise"][0] = 0.0  # the decision gets its own copy
    assert charts[0].spots[0].actions["raise"][0] == 1.0


@pytest.mark.parametrize("weight, verdict", [("AKo", "agrees"), ("AKo:0.5", "agrees"), ("AKo:0.3", "mixed"),
                                             ("AKo:0.1", "mixed"), ("AKo:0.05", "deviates"), ("QQ", "deviates")])
def test_verdicts(weight, verdict):
    d = only(chart_decisions(table(["Ah", "Kd"], first_in(1, [bet(1, 50)])),
                             charts_with(rfi({"raise": weight}, position="UTG"))))
    assert d.verdict == verdict


def test_folds_and_mixed_spots():
    folded_junk = only(chart_decisions(table(["7h", "2d"], first_in(1, [fold(1)])), UTG_RAISE))
    assert (folded_junk.action, folded_junk.action_freq, folded_junk.verdict) == ("fold", 1.0, "agrees")
    folded_aces = only(chart_decisions(table(["Ah", "Ad"], first_in(1, [fold(1)])), UTG_RAISE))
    assert (folded_aces.action_freq, folded_aces.verdict, folded_aces.chart_action) == (0.0, "deviates", "raise")
    mixed = only(chart_decisions(table(["Ah", "Kd"], first_in(1, [bet(1, 50)])), UTG_RAISE))
    assert mixed.freqs == {"raise": 0.3, "fold": 0.7} and mixed.chart_action == "fold"
    assert mixed.verdict == "mixed"


def test_limp_maps_to_limp():
    h = table(["Ah", "Ad"], first_in(1, [call(1, 20)]))
    d = only(chart_decisions(h, UTG_RAISE))
    assert (d.action, d.action_freq, d.verdict) == ("limp", 0.0, "deviates")
    d = only(chart_decisions(h, charts_with(rfi({"raise": "AA:0.4", "limp": "AA:0.6"}))))
    assert (d.action, d.action_freq, d.chart_action, d.verdict) == ("limp", 0.6, "limp", "agrees")


def test_small_blind_first_in():
    charts = charts_with(rfi({"raise": "AA", "limp": "K2s+"}, position="SB"))
    completed = only(chart_decisions(table(["Kh", "3h"], first_in(8, [call(8, 20)]), hero_seat=8), charts))
    assert (completed.position, completed.action, completed.verdict) == ("SB", "limp", "agrees")
    assert completed.players_behind == 1 and completed.title == "SB raise first in · 100.0 BB"


def test_rfi_all_in_mapping():
    shove = first_in(1, [bet(1, 2000, allin=True)])
    with_allin = charts_with(rfi({"raise": "QQ-KK, AA:0.5", "allin": "AA:0.5"}, position="UTG"))
    assert only(chart_decisions(table(["Ah", "Ad"], shove), with_allin)).action == "allin"
    assert only(chart_decisions(table(["Ah", "Ad"], shove), UTG_RAISE)).action == "raise"
    # All-in without the export's allIn flag: the raise used the whole stack.
    unflagged = table(["Ah", "Ad"], first_in(1, [bet(1, 800)]), stack=800)
    assert only(chart_decisions(unflagged, with_allin)).action == "allin"
    assert only(chart_decisions(table(["Ah", "Ad"], first_in(1, [bet(1, 50)])), with_allin)).action == "raise"


def test_position_filter():
    co_only = charts_with(rfi({"raise": "22+"}, position="CO"))
    assert chart_decisions(table(["Ah", "Ad"], first_in(1, [bet(1, 50)])), co_only) == []
    co = only(chart_decisions(table(["Ah", "Ad"], first_in(6, [bet(6, 50)]), hero_seat=6), co_only))
    assert co.position == "CO" and co.players_behind == 3
    both = charts_with(rfi({"raise": "22+"}, positions=["CO", "UTG"]))
    assert only(chart_decisions(table(["Ah", "Ad"], first_in(1, [bet(1, 50)])), both)).position == "UTG"
    anywhere = charts_with(rfi({"raise": "22+"}))
    assert only(chart_decisions(table(["Ah", "Ad"], first_in(7, [bet(7, 50)]), hero_seat=7), anywhere)).position == "BTN"


def test_stack_filter_is_inclusive_on_effective_stack():
    deep = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]))                # 100 BB
    short = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]), stack=800)    # 40 BB, others 100 BB
    mid = charts_with(rfi({"raise": "22+"}, stack_bb=[20, 40]))
    assert chart_decisions(deep, mid) == []
    d = only(chart_decisions(short, mid))
    assert d.eff_stack_bb == 40.0 and d.title == "UTG raise first in · 40.0 BB"
    assert chart_decisions(short, charts_with(rfi({"raise": "22+"}, stack_bb=[40.5, 60]))) == []


def test_players_filter():
    six_max = charts_with(rfi({"raise": "22+"}, position="UTG", players=[5, 6]))
    assert chart_decisions(table(["Ah", "Ad"], first_in(1, [bet(1, 50)])), six_max) == []
    six = table(["Ah", "Ad"], first_in(1, [bet(1, 50)], n=6), n=6)
    assert six.hero.position == "UTG"
    assert only(chart_decisions(six, six_max)).players_behind == 5
    nine = charts_with(rfi({"raise": "22+"}, players=[9, 9]))
    assert only(chart_decisions(table(["Ah", "Ad"], first_in(1, [bet(1, 50)])), nine))


def test_vs_open_decisions():
    charts = charts_with(vs_open({"3bet": "QQ+", "call": "22-JJ, AKo:0.5"}, position="BB", opener="UTG"))
    threebet = table(["Qh", "Qd"], facing_open(1, 9, [bet(9, 150)]), hero_seat=9)
    d = only(chart_decisions(threebet, charts))
    assert (d.situation, d.position, d.action, d.chart_action, d.verdict) == ("vs_open", "BB", "3bet", "3bet", "agrees")
    assert d.title == "BB vs UTG open · 100.0 BB" and d.players_behind == 0
    assert d.freqs == {"3bet": 1.0, "call": 0.0, "fold": 0.0}
    called = only(chart_decisions(table(["9h", "9d"], facing_open(1, 9, [call(9, 50)]), hero_seat=9), charts))
    assert (called.action, called.verdict) == ("call", "agrees")
    folded = only(chart_decisions(table(["Ah", "Kd"], facing_open(1, 9, [fold(9)]), hero_seat=9), charts))
    assert (folded.action, folded.action_freq, folded.verdict) == ("fold", 0.5, "agrees")
    btn = only(chart_decisions(table(["Qh", "Qd"], facing_open(1, 7, [bet(7, 150)]), hero_seat=7),
                               charts_with(vs_open({"3bet": "QQ+"}))))
    assert btn.position == "BTN" and btn.players_behind == 2


def test_opener_filter():
    late = charts_with(vs_open({"call": "22+"}, position="BB", opener=["BTN", "CO"]))
    assert chart_decisions(table(["9h", "9d"], facing_open(1, 9, [call(9, 50)]), hero_seat=9), late) == []
    d = only(chart_decisions(table(["9h", "9d"], facing_open(7, 9, [call(9, 50)]), hero_seat=9), late))
    assert d.title == "BB vs BTN open · 100.0 BB"


def test_vs_open_all_in_mapping():
    shove_back = facing_open(1, 9, [bet(9, 2000, allin=True)])
    with_allin = charts_with(vs_open({"3bet": "QQ-KK", "allin": "AA"}))
    assert only(chart_decisions(table(["Ah", "Ad"], shove_back, hero_seat=9), with_allin)).action == "allin"
    without = charts_with(vs_open({"3bet": "QQ+"}))
    assert only(chart_decisions(table(["Ah", "Ad"], shove_back, hero_seat=9), without)).action == "3bet"


def test_spots_that_are_not_rfi_or_vs_open():
    charts = charts_with(rfi({"raise": "22+"}), vs_open({"3bet": "22+", "call": ""}))
    # An all-in open is the push/fold "call" spot, not vs_open.
    allin_open = facing_open(1, 9, [call(9, 2000)], open_to=2000, allin=True)
    assert chart_decisions(table(["Ah", "Ad"], allin_open, hero_seat=9), charts) == []
    # A cold caller between the open and the player.
    cold_call = facing_open(1, 9, [bet(9, 150)], between=[call(2, 50)] + [fold(s) for s in range(3, 9)])
    assert chart_decisions(table(["Ah", "Ad"], cold_call, hero_seat=9), charts) == []
    # A raise over a limper is not an open.
    iso = [sb(8, 10), bb(9, 20), call(1, 20), bet(2, 80)] + [fold(s) for s in range(3, 9)] + [call(9, 80), END]
    assert chart_decisions(table(["Ah", "Ad"], iso, hero_seat=9), charts) == []
    # Raising over a limper is not first in either.
    assert chart_decisions(table(["Ah", "Ad"], iso, hero_seat=2), charts) == []
    # Facing a 3-bet.
    three_bet = facing_open(1, 9, [fold(9)], between=[bet(2, 150)] + [fold(s) for s in range(3, 9)])
    assert chart_decisions(table(["Ah", "Ad"], three_bet, hero_seat=9), charts) == []
    # Only the situation's own spots apply.
    assert chart_decisions(table(["Ah", "Ad"], facing_open(1, 9, [fold(9)]), hero_seat=9),
                           charts_with(rfi({"raise": "22+"}))) == []


def test_first_matching_spot_wins(tmp_path):
    h = table(["Ah", "Kd"], first_in(1, [bet(1, 50)]))
    charts = charts_with(rfi({"raise": "AKo"}, position="CO"),       # doesn't fit
                         rfi({"raise": "AKo:0.2"}),                   # first that fits
                         rfi({"raise": "AKo"}, position="UTG"))
    assert only(chart_decisions(h, charts)).action_freq == 0.2
    write_chart(tmp_path, "b.json", "Bravo", [rfi({"raise": "AKo"})])
    write_chart(tmp_path, "a.json", "Alpha", [rfi({"raise": "AKo:0.4"}, players=[9, 9])])
    files, errors = load_charts(tmp_path)
    assert errors == []
    d = only(chart_decisions(h, files))
    assert (d.source, d.action_freq) == ("Alpha", 0.4)


def test_holdem_only_and_known_cards_only():
    plo = table(["Ah", "Ad", "Kh", "Kd"], first_in(1, [bet(1, 50)]), game="omaha")
    assert plo.game == "plo" and chart_decisions(plo, UTG_RAISE) == []
    h = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]))
    assert chart_decisions(h, UTG_RAISE, player_id="p2") == []      # cards unknown
    assert chart_decisions(h, UTG_RAISE, player_id="nobody") == []
    assert chart_decisions(h, []) == []
    half_shown = table(["Ah", None], first_in(1, [bet(1, 50)]))
    assert chart_decisions(half_shown, UTG_RAISE) == []
    straddled = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]))
    straddled.warnings.append("straddleSeat is set; positions and stats may be off for this hand")
    assert chart_decisions(straddled, UTG_RAISE) == []


def test_opponent_with_shown_cards():
    h = table(["2h", "2d"], facing_open(1, 9, [call(9, 50)]), hero_seat=9, others={1: ["Ac", "Kc"]})
    d = only(chart_decisions(h, charts_with(rfi({"raise": "AKs"}), vs_open({"call": "22+"}))))
    assert (d.player_id, d.situation) == ("p9", "vs_open")
    opp = only(chart_decisions(h, charts_with(rfi({"raise": "AKs"}), vs_open({"call": "22+"})), player_id="p1"))
    assert (opp.player_id, opp.situation, opp.position, opp.hand_class, opp.verdict) == \
        ("p1", "rfi", "UTG", "AKs", "agrees")


# ---- all_decisions -----------------------------------------------------------------------------

def test_all_decisions_adds_nash_and_chart_decisions(tmp_path, monkeypatch):
    calls = []

    def fake_nash(hand, player_id=None):
        calls.append((hand.id, player_id))
        return ["nash decision"]

    monkeypatch.setattr(gto, "nash_decisions", fake_nash, raising=False)
    h = table(["Ah", "Ad"], first_in(1, [bet(1, 50)]))
    out = all_decisions(h, UTG_RAISE)
    assert out[0] == "nash decision" and out[1].source == "Mine" and len(out) == 2
    assert all_decisions(h, [], player_id="p2") == ["nash decision"]
    write_chart(tmp_path, "mine.json", "From the folder", [rfi({"raise": "AA"})])
    monkeypatch.setenv("POKER_CHARTS", str(tmp_path))
    assert [d.source for d in all_decisions(h)[1:]] == ["From the folder"]
    assert calls == [("h1", None), ("h1", "p2"), ("h1", None)]
