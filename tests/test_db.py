import json

import pandas as pd
import pytest

from conftest import SAMPLE
from pokeranalyzer import db
from pokeranalyzer.charts import load_charts
from pokeranalyzer.gto import GtoDecision


@pytest.fixture(autouse=True)
def no_user_charts(tmp_path, monkeypatch):
    """Keep the user's own chart files (data/charts) out of these tests."""
    monkeypatch.setenv("POKER_CHARTS", str(tmp_path / "no-charts"))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def import_sample(conn, **kw):
    """The sample tournament, with a quick all-in equity estimate."""
    return db.import_export(conn, json.loads(SAMPLE.read_text()), equity_iterations=2000, **kw)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# Raise first in at 20+ BB with any pair, suited aces, KTs+ or ATo+; fold the rest. Made up for the tests.
CHART = {"name": "Test chart", "spots": [
    {"situation": "rfi", "stack_bb": [20, 200], "actions": {"raise": "22+, A2s+, KTs+, ATo+"}}]}


def test_import_and_dedupe(conn):
    data = json.loads(SAMPLE.read_text())
    first = db.import_export(conn, data, source_file="sample.json", equity_iterations=2000)
    assert (first.added, first.duplicates, first.problems) == (40, 0, [])
    again = db.import_export(conn, data, equity_iterations=2000)
    assert (again.added, again.duplicates) == (0, 40)
    assert conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 40


def test_partial_overlap(conn):
    data = json.loads(SAMPLE.read_text())
    head = dict(data, hands=data["hands"][:25])
    assert db.import_export(conn, head, equity_iterations=2000).added == 25
    r = db.import_export(conn, data, equity_iterations=2000)
    assert (r.added, r.duplicates) == (15, 25)
    tables = [t for (t,) in conn.execute("SELECT table_no FROM hands ORDER BY started_at, id")]
    assert tables == [1] * 22 + [2] * 6 + [3] * 12


def test_session_results(conn):
    r = db.import_file(conn, SAMPLE, meta={"finishing_place": 1, "field_size": 22, "buy_in": 0.0})
    db.update_session(conn, r.session, prize=5.0, notes="won it")
    s = db.sessions_df(conn).iloc[0]
    assert (s["finishing_place"], s["field_size"], s["prize"], s["notes"], s["hands"]) == (1, 22, 5.0, "won it", 40)
    with pytest.raises(ValueError):
        db.update_session(conn, r.session, hero_id="x")


def test_queries(conn):
    db.import_file(conn, SAMPLE)
    hero = db.player_hands_df(conn, hero=True)
    assert len(hero) == 40 and hero["vpip"].dtype == bool
    assert hero["net"].sum() == 74925 - 2000
    everyone = db.player_hands_df(conn)
    assert everyone.groupby("hand_id")["net"].sum().eq(0).all()
    allin = db.allin_df(conn, hero=True)
    assert set(allin["number"]) == {"10", "11", "14", "27", "39", "42"}
    flags = db.leak_flags_df(conn)
    assert set(flags["kind"]) == {"limp", "missed_shove", "tight_fold_shove"}
    players = db.players_df(conn)
    assert players.iloc[0]["name"] == "Kesayu" and players.iloc[0]["hands"] == 40
    h = db.load_hand(conn, hero.iloc[-1]["hand_id"])
    assert h.number == "42" and h.table == 3


def test_rebuild_and_delete(conn):
    r = db.import_file(conn, SAMPLE)
    before = db.player_hands_df(conn)
    assert db.rebuild(conn) == 40
    after = db.player_hands_df(conn)
    assert before.equals(after)
    db.delete_session(conn, r.session)
    for table in ("hands", "hand_players", "actions", "allin", "leak_flags", "gto_decisions"):
        assert count(conn, table) == 0, table
    assert db.sessions_df(conn).empty


# ---- GTO chart comparisons --------------------------------------------------------------------

def test_gto_decisions(conn):
    import_sample(conn)
    gto = db.gto_df(conn)
    assert set(gto["source"]) == {"nash"}
    assert {"session", "number", "table_no", "game", "started_at", "big_blind", "hero_id", "name",
            "hole_cards"} <= set(gto.columns)
    assert set(GtoDecision.__dataclass_fields__) <= set(gto.columns)
    assert not gto.duplicated(["hand_id", "seq", "source"]).any()

    # The hero's push/fold spots, and every opponent who showed cards in one.
    hero = db.gto_df(conn, hero=True)
    assert hero["number"].tolist() == ["24", "31", "32", "33", "39", "40"]
    assert (hero["player_id"] == hero["hero_id"]).all() and set(hero["name"]) == {"Kesayu"}
    by_number = hero.set_index("number")
    assert by_number.loc["31", "situation"] == "call" and by_number.loc["31", "action"] == "fold"
    assert by_number.loc["32", "verdict"] == "deviates" and by_number.loc["32", "hand_class"] == "A2o"
    assert by_number.loc["32", "ev_loss_bb"] == pytest.approx(0.60, abs=0.05)
    assert by_number.loc["39", "verdict"] == "off-chart" and pd.isna(by_number.loc["39", "ev_loss_bb"])
    opponents = gto[gto["player_id"] != gto["hero_id"]]
    assert len(opponents) == 9 and opponents["hole_cards"].str.len().gt(0).all()
    assert len(db.gto_df(conn, player_id=opponents.iloc[0]["player_id"])) >= 1

    # JSON columns come back as Python objects.
    row = by_number.loc["32"]
    assert row["freqs"] == {"push": 1.0, "fold": 0.0}
    assert list(row["grid"]) == ["push"] and len(row["grid"]["push"]) == 169 and len(row["evs"]) == 169

    # Re-importing the same file adds nothing.
    import_sample(conn)
    assert count(conn, "gto_decisions") == len(gto)


def test_rebuild_applies_charts(conn, tmp_path, monkeypatch):
    import_sample(conn)
    nash_rows = count(conn, "gto_decisions")
    folder = tmp_path / "charts"
    folder.mkdir()
    (folder / "test.json").write_text(json.dumps(CHART))
    monkeypatch.setenv("POKER_CHARTS", str(folder))
    db.rebuild(conn)

    charted = db.gto_df(conn)
    charted = charted[charted["source"] == "Test chart"]
    hero = charted[charted["player_id"] == charted["hero_id"]]
    assert hero["number"].tolist() == ["2", "6", "7", "17", "19", "20"]
    assert set(hero.loc[hero["verdict"] == "deviates", "number"]) == {"2", "6"}  # limped 87s and T9o
    assert set(charted.loc[charted["player_id"] != charted["hero_id"], "number"]) == {"4", "21"}
    assert hero.iloc[0]["grid"]["raise"][0] == 1.0  # AA
    assert count(conn, "gto_decisions") == nash_rows + len(charted)
    flags = db.leak_flags_df(conn)
    assert sorted(flags.loc[flags["kind"] == "chart_deviation", "number"]) == ["2", "6"]

    # Charts passed in are used instead of the folder; without charts the chart rows go away.
    db.rebuild(conn, charts=[])
    assert count(conn, "gto_decisions") == nash_rows
    assert "chart_deviation" not in set(db.leak_flags_df(conn)["kind"])
    db.rebuild(conn, charts=load_charts(folder)[0])
    assert count(conn, "gto_decisions") == nash_rows + len(charted)


def test_import_with_charts(conn, tmp_path):
    folder = tmp_path / "charts"
    folder.mkdir()
    (folder / "test.json").write_text(json.dumps(CHART))
    import_sample(conn, charts=load_charts(folder)[0])
    assert "Test chart" in set(db.gto_df(conn)["source"])


def test_database_from_before_gto_comparisons(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    import_sample(conn)
    conn.execute("DROP TABLE gto_decisions")
    conn.close()

    conn = db.connect(path)  # adds the missing table; the old hands get comparisons on rebuild
    assert db.gto_df(conn).empty
    db.rebuild(conn)
    assert len(db.gto_df(conn, hero=True)) == 6
    conn.close()
