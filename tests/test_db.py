import json

import pytest

from conftest import SAMPLE
from pokeranalyzer import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


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
    assert set(flags["kind"]) == {"limp", "missed_shove"}
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
    assert conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 0
    assert db.sessions_df(conn).empty
