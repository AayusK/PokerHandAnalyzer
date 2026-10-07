"""SQLite storage. Every hand's raw JSON is kept, so derived tables can be rebuilt any time.

Importing is idempotent: hands are keyed by their PokerNow id and duplicates are skipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import equity, leaks, stats
from .parser import Hand, assign_tables, chip_replay, parse_export, parse_hand

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "poker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    slug            TEXT PRIMARY KEY,
    hero_id         TEXT,
    hero_name       TEXT,
    first_hand_at   INTEGER,
    last_hand_at    INTEGER,
    imported_at     TEXT,
    source_file     TEXT,
    finishing_place INTEGER,
    field_size      INTEGER,
    buy_in          REAL,
    prize           REAL,
    notes           TEXT
);
CREATE TABLE IF NOT EXISTS hands (
    id           TEXT PRIMARY KEY,
    session      TEXT NOT NULL REFERENCES sessions(slug),
    number       TEXT,
    table_no     INTEGER,
    game         TEXT,
    small_blind  INTEGER,
    big_blind    INTEGER,
    ante         INTEGER,
    dealer_seat  INTEGER,
    started_at   INTEGER,
    num_players  INTEGER,
    hero_id      TEXT,
    board        TEXT,
    pot          INTEGER,
    showdown     INTEGER,
    warnings     TEXT,
    raw          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS hands_session ON hands(session, started_at);
CREATE TABLE IF NOT EXISTS players (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    first_seen INTEGER,
    last_seen  INTEGER
);
CREATE TABLE IF NOT EXISTS player_names (
    player_id TEXT,
    name      TEXT,
    PRIMARY KEY (player_id, name)
);
CREATE TABLE IF NOT EXISTS hand_players (
    hand_id   TEXT REFERENCES hands(id),
    player_id TEXT REFERENCES players(id),
    {flag_columns},
    PRIMARY KEY (hand_id, player_id)
);
CREATE INDEX IF NOT EXISTS hand_players_player ON hand_players(player_id);
CREATE TABLE IF NOT EXISTS actions (
    hand_id       TEXT REFERENCES hands(id),
    seq           INTEGER,
    street        TEXT,
    seat          INTEGER,
    player_id     TEXT,
    kind          TEXT,
    amount        INTEGER,
    to_amount     INTEGER,
    all_in        INTEGER,
    pot_before    INTEGER,
    to_call       INTEGER,
    stack_before  INTEGER,
    raises_before INTEGER,
    eff_stack_bb  REAL,
    PRIMARY KEY (hand_id, seq)
);
CREATE TABLE IF NOT EXISTS allin (
    hand_id   TEXT REFERENCES hands(id),
    player_id TEXT,
    street    TEXT,
    board     TEXT,
    equity    REAL,
    expected  REAL,
    won       INTEGER,
    invested  INTEGER,
    PRIMARY KEY (hand_id, player_id)
);
CREATE TABLE IF NOT EXISTS leak_flags (
    hand_id  TEXT REFERENCES hands(id),
    kind     TEXT,
    severity TEXT,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS leak_flags_hand ON leak_flags(hand_id);
"""

# hand_players columns come from stats.player_hand_flags (minus the key columns).
_FLAG_TYPES = {bool: "INTEGER", int: "INTEGER", float: "REAL", str: "TEXT"}


def _flag_columns() -> list[tuple[str, str]]:
    sample = _sample_flags()
    return [(k, _FLAG_TYPES[type(v)]) for k, v in sample.items() if k not in ("hand_id", "player_id")]


def _sample_flags() -> dict:
    from .parser import Player
    hand = Hand(id="x", number="1", game="holdem", small_blind=1, big_blind=2, ante=0, dealer_seat=1,
                started_at=0, hero_id="a", tournament="", players=[
                    Player(id="a", seat=1, name="a", stack=100, hole_cards=None, position="BTN"),
                ])
    return stats.player_hand_flags(hand, "a")


FLAG_COLUMNS = _flag_columns()
BOOL_COLUMNS = [k for k, v in _sample_flags().items() if isinstance(v, bool)]


def default_path() -> Path:
    return Path(os.environ.get("POKER_DB", DEFAULT_DB))


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else default_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.format(flag_columns=",\n    ".join(f"{c} {t}" for c, t in FLAG_COLUMNS)))
    return conn


@dataclass
class ImportResult:
    session: str
    added: int
    duplicates: int
    problems: list[str] = field(default_factory=list)


def import_export(conn: sqlite3.Connection, data: dict | str | bytes, source_file: str = "",
                  meta: dict | None = None, equity_iterations: int = equity.DEFAULT_ITERATIONS) -> ImportResult:
    """Import one PokerNow export. Hands already in the database are skipped.

    ``meta`` may hold finishing_place, field_size, buy_in, prize and notes for the session.
    """
    if isinstance(data, (str, bytes)):
        data = json.loads(data)
    session = parse_export(data)
    if not session.hands:
        return ImportResult(session.tournament, 0, 0, ["the file has no hands"])
    problems = chip_replay(session.hands)
    for h in session.hands:
        problems += [f"hand {h.number} ({h.id}): {w}" for w in h.warnings]
    slug = session.tournament or f"import-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"

    ids = [h.id for h in session.hands]
    existing: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        existing |= {r[0] for r in conn.execute(
            f"SELECT id FROM hands WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
    new_hands = [h for h in session.hands if h.id not in existing]

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (slug, hero_id, hero_name, imported_at, source_file) VALUES (?, ?, ?, ?, ?)",
            (slug, session.hero_id, session.hero_name, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             source_file))
        for h in new_hands:
            h.tournament = slug
            _insert_hand(conn, h, equity_iterations)
        _refresh_session(conn, slug)
        if meta:
            update_session(conn, slug, **meta)
    return ImportResult(slug, len(new_hands), len(existing), problems)


def import_file(conn: sqlite3.Connection, path: str | Path, meta: dict | None = None) -> ImportResult:
    with open(path, encoding="utf-8") as f:
        return import_export(conn, json.load(f), source_file=Path(path).name, meta=meta)


def _insert_hand(conn: sqlite3.Connection, h: Hand, equity_iterations: int) -> None:
    conn.execute(
        "INSERT INTO hands (id, session, number, table_no, game, small_blind, big_blind, ante, dealer_seat,"
        " started_at, num_players, hero_id, board, pot, showdown, warnings, raw)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (h.id, h.tournament, h.number, h.table, h.game, h.small_blind, h.big_blind, h.ante, h.dealer_seat,
         h.started_at, h.num_players, h.hero_id, " ".join(h.board), h.pot, int(h.showdown),
         "\n".join(h.warnings), json.dumps(h.raw)))
    _insert_derived(conn, h, equity_iterations)


def _insert_derived(conn: sqlite3.Connection, h: Hand, equity_iterations: int | None) -> None:
    for p in h.players:
        conn.execute(
            "INSERT INTO players (id, name, first_seen, last_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            "  name = CASE WHEN excluded.last_seen >= players.last_seen THEN excluded.name ELSE players.name END,"
            "  first_seen = MIN(players.first_seen, excluded.first_seen),"
            "  last_seen = MAX(players.last_seen, excluded.last_seen)",
            (p.id, p.name, h.started_at, h.started_at))
        conn.execute("INSERT OR IGNORE INTO player_names (player_id, name) VALUES (?, ?)", (p.id, p.name))
        row = stats.player_hand_flags(h, p.id)
        cols = ["hand_id", "player_id"] + [c for c, _ in FLAG_COLUMNS]
        conn.execute(f"INSERT OR REPLACE INTO hand_players ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [row[c] for c in cols])
    conn.executemany(
        "INSERT OR REPLACE INTO actions (hand_id, seq, street, seat, player_id, kind, amount, to_amount, all_in,"
        " pot_before, to_call, stack_before, raises_before, eff_stack_bb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(h.id, a.seq, a.street, a.seat, a.player_id, a.kind, a.amount, a.to_amount, int(a.all_in), a.pot_before,
          a.to_call, a.stack_before, a.raises_before, a.eff_stack_bb) for a in h.actions])
    conn.execute("DELETE FROM leak_flags WHERE hand_id = ?", (h.id,))
    conn.executemany("INSERT INTO leak_flags (hand_id, kind, severity, message) VALUES (?, ?, ?, ?)",
                     [(f.hand_id, f.kind, f.severity, f.message) for f in leaks.hand_flags(h)])
    if equity_iterations is not None:
        conn.execute("DELETE FROM allin WHERE hand_id = ?", (h.id,))
        conn.executemany(
            "INSERT INTO allin (hand_id, player_id, street, board, equity, expected, won, invested)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(r.hand_id, r.player_id, r.street, " ".join(r.board), r.equity, r.expected, r.won, r.invested)
             for r in equity.allin_results(h, iterations=equity_iterations)])


def _session_hands(conn: sqlite3.Connection, slug: str) -> list[Hand]:
    rows = conn.execute(
        "SELECT h.raw, h.hero_id, h.session FROM hands h WHERE h.session = ? ORDER BY h.started_at, h.id", (slug,))
    hands = [parse_hand(json.loads(raw), hero, session) for raw, hero, session in rows]
    assign_tables(hands)
    return hands


def _refresh_session(conn: sqlite3.Connection, slug: str) -> None:
    """Renumber tables over all of a session's hands and update its first/last hand times."""
    hands = _session_hands(conn, slug)
    conn.executemany("UPDATE hands SET table_no = ? WHERE id = ?", [(h.table, h.id) for h in hands])
    if hands:
        conn.execute("UPDATE sessions SET first_hand_at = ?, last_hand_at = ? WHERE slug = ?",
                     (hands[0].started_at, hands[-1].started_at, slug))


def rebuild(conn: sqlite3.Connection, recompute_equity: bool = False) -> int:
    """Re-derive players, flags, actions and leak flags from the stored raw JSON."""
    slugs = [r[0] for r in conn.execute("SELECT slug FROM sessions")]
    count = 0
    with conn:
        for slug in slugs:
            _refresh_session(conn, slug)
            for h in _session_hands(conn, slug):
                _insert_derived(conn, h, equity.DEFAULT_ITERATIONS if recompute_equity else None)
                count += 1
    return count


SESSION_FIELDS = ("finishing_place", "field_size", "buy_in", "prize", "notes")


def update_session(conn: sqlite3.Connection, slug: str, **fields) -> None:
    bad = set(fields) - set(SESSION_FIELDS)
    if bad:
        raise ValueError(f"unknown session fields: {sorted(bad)}")
    if fields:
        with conn:
            conn.execute(f"UPDATE sessions SET {', '.join(f'{k} = ?' for k in fields)} WHERE slug = ?",
                         [*fields.values(), slug])


def delete_session(conn: sqlite3.Connection, slug: str) -> None:
    with conn:
        ids = "SELECT id FROM hands WHERE session = ?"
        for table in ("leak_flags", "allin", "actions", "hand_players"):
            conn.execute(f"DELETE FROM {table} WHERE hand_id IN ({ids})", (slug,))
        conn.execute("DELETE FROM hands WHERE session = ?", (slug,))
        conn.execute("DELETE FROM sessions WHERE slug = ?", (slug,))


# ---------------------------------------------------------------------------------------
# Queries for the dashboard


def sessions_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT s.*, COUNT(h.id) AS hands FROM sessions s LEFT JOIN hands h ON h.session = s.slug"
        " GROUP BY s.slug ORDER BY s.first_hand_at", conn)


def hands_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT id, session, number, table_no, game, small_blind, big_blind, ante, started_at, num_players,"
        " hero_id, board, pot, showdown, warnings FROM hands ORDER BY started_at, id", conn)


def player_hands_df(conn: sqlite3.Connection, player_id: str | None = None, hero: bool = False) -> pd.DataFrame:
    """hand_players rows joined with hand info. ``hero=True`` keeps only each hand's hero."""
    where, params = [], []
    if player_id:
        where.append("hp.player_id = ?")
        params.append(player_id)
    if hero:
        where.append("hp.player_id = h.hero_id")
    sql = ("SELECT hp.*, h.session, h.number, h.table_no, h.game, h.big_blind, h.small_blind, h.started_at,"
           " h.num_players, h.hero_id, h.board, h.pot, h.showdown FROM hand_players hp JOIN hands h ON h.id = hp.hand_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY h.started_at, h.id")
    df = pd.read_sql_query(sql, conn, params=params)
    df[BOOL_COLUMNS] = df[BOOL_COLUMNS].astype(bool)
    return stats.add_derived(df)


def allin_df(conn: sqlite3.Connection, player_id: str | None = None, hero: bool = False) -> pd.DataFrame:
    where, params = [], []
    if player_id:
        where.append("a.player_id = ?")
        params.append(player_id)
    if hero:
        where.append("a.player_id = h.hero_id")
    sql = ("SELECT a.*, h.session, h.number, h.game, h.big_blind, h.started_at, h.hero_id, hp.name, hp.position,"
           " hp.hole_cards"
           " FROM allin a JOIN hands h ON h.id = a.hand_id"
           " JOIN hand_players hp ON hp.hand_id = a.hand_id AND hp.player_id = a.player_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY h.started_at")
    df = pd.read_sql_query(sql, conn, params=params)
    df["expected_net"] = df["expected"] - df["invested"]
    df["actual_net"] = df["won"] - df["invested"]
    df["luck"] = df["won"] - df["expected"]
    df["luck_bb"] = df["luck"] / df["big_blind"]
    return df


def leak_flags_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT f.*, h.session, h.number, h.game, h.started_at, h.big_blind, hp.position, hp.hole_cards, hp.net,"
        " hp.eff_stack_bb FROM leak_flags f JOIN hands h ON h.id = f.hand_id"
        " JOIN hand_players hp ON hp.hand_id = f.hand_id AND hp.player_id = h.hero_id ORDER BY h.started_at", conn)


def players_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT p.id, p.name, p.first_seen, p.last_seen,"
        " (SELECT COUNT(*) FROM hand_players hp WHERE hp.player_id = p.id) AS hands,"
        " (SELECT SUM(hp.showed) FROM hand_players hp WHERE hp.player_id = p.id) AS hands_shown,"
        " (SELECT GROUP_CONCAT(pn.name, ', ') FROM player_names pn WHERE pn.player_id = p.id) AS names"
        " FROM players p ORDER BY hands DESC, p.name", conn)


def load_hand(conn: sqlite3.Connection, hand_id: str) -> Hand | None:
    row = conn.execute("SELECT raw, hero_id, session, table_no FROM hands WHERE id = ?", (hand_id,)).fetchone()
    if row is None:
        return None
    hand = parse_hand(json.loads(row[0]), row[1], row[2])
    hand.table = row[3] or 1
    return hand


def load_hands(conn: sqlite3.Connection, hand_ids: list[str]) -> list[Hand]:
    return [h for h in (load_hand(conn, i) for i in hand_ids) if h is not None]
