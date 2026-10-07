"""Shared helpers for the Streamlit pages: database access, caching, filters, colors."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokeranalyzer import db  # noqa: E402
from pokeranalyzer.replay import replay_text  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"

# Chart colors (validated default palette, see the dataviz reference): one hue per job.
BLUE = "#2a78d6"     # main series / gains
RED = "#e34948"      # losses (diverging pole)
ORANGE = "#eb6834"   # second series
GRAY = "#898781"     # muted marks, reference lines
GAME_LABELS = {"holdem": "Hold'em", "plo": "PLO"}


@st.cache_resource
def get_conn():
    return db.connect()


def db_version() -> float:
    """Changes whenever the database file is written; used as a cache key."""
    path = db.default_path()
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(show_spinner=False)
def _hero_hands(version: float) -> pd.DataFrame:
    return db.player_hands_df(get_conn(), hero=True)


@st.cache_data(show_spinner=False)
def _all_player_hands(version: float) -> pd.DataFrame:
    return db.player_hands_df(get_conn())


@st.cache_data(show_spinner=False)
def _sessions(version: float) -> pd.DataFrame:
    return db.sessions_df(get_conn())


@st.cache_data(show_spinner=False)
def _allin(version: float) -> pd.DataFrame:
    return db.allin_df(get_conn())


@st.cache_data(show_spinner=False)
def _leaks(version: float) -> pd.DataFrame:
    return db.leak_flags_df(get_conn())


@st.cache_data(show_spinner=False)
def _players(version: float) -> pd.DataFrame:
    return db.players_df(get_conn())


def hero_hands() -> pd.DataFrame:
    return _hero_hands(db_version())


def all_player_hands() -> pd.DataFrame:
    return _all_player_hands(db_version())


def sessions() -> pd.DataFrame:
    return _sessions(db_version())


def allin() -> pd.DataFrame:
    return _allin(db_version())


def leak_flags() -> pd.DataFrame:
    return _leaks(db_version())


def players() -> pd.DataFrame:
    return _players(db_version())


def clear_caches() -> None:
    st.cache_data.clear()


def session_label(slug: str) -> str:
    s = sessions()
    row = s[s["slug"] == slug]
    if row.empty or pd.isna(row.iloc[0]["first_hand_at"]):
        return slug
    when = pd.to_datetime(row.iloc[0]["first_hand_at"], unit="ms", utc=True)
    return f"{when:%Y-%m-%d} · {slug}"


def sidebar_filters() -> dict:
    """Session and game filters shared by the pages; returns the selection."""
    s = sessions()
    slugs = s["slug"].tolist()
    with st.sidebar:
        st.caption("Filters")
        chosen = st.multiselect("Sessions", slugs, default=slugs, format_func=session_label,
                                key="filter_sessions", placeholder="All sessions")
        games = st.multiselect("Game", ["holdem", "plo"], default=["holdem", "plo"],
                               format_func=GAME_LABELS.get, key="filter_games", placeholder="All games")
    return {"sessions": chosen or slugs, "games": games or ["holdem", "plo"]}


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[df["session"].isin(filters["sessions"])]
    if "game" in out:
        out = out[out["game"].isin(filters["games"])]
    return out


def require_data() -> bool:
    if sessions().empty:
        st.info("No hands yet. Import a PokerNow export on the **Import** page.")
        st.page_link("pages/1_Import.py", label="Go to Import", icon=":material/upload:")
        return False
    return True


def show_replay(hand_id: str) -> None:
    hand = db.load_hand(get_conn(), hand_id)
    if hand is None:
        st.warning("Hand not found.")
        return
    st.code(replay_text(hand), language=None)
    flags = leak_flags()
    mine = flags[flags["hand_id"] == hand_id]
    for _, f in mine.iterrows():
        st.warning(f"**{f['kind'].replace('_', ' ')}**: {f['message']}", icon=":material/flag:")
    ai = allin()
    rows = ai[ai["hand_id"] == hand_id]
    if not rows.empty:
        t = rows[["name", "hole_cards", "street", "board", "equity", "expected_net", "actual_net"]].copy()
        bb = rows["big_blind"].iloc[0]
        t["equity"] = (100 * t["equity"]).round(1)
        t["expected_net"] = (t["expected_net"] / bb).round(1)
        t["actual_net"] = (t["actual_net"] / bb).round(1)
        st.caption(f"All-in equity when the last chips went in ({rows['street'].iloc[0]})")
        st.dataframe(t.rename(columns={"name": "player", "hole_cards": "cards", "equity": "equity %",
                                       "expected_net": "expected BB", "actual_net": "actual BB"}),
                     hide_index=True, width="stretch")


def pick_and_replay(df: pd.DataFrame, columns: list[str], key: str, column_config: dict | None = None,
                    height: int | None = None) -> None:
    """Show a table of hands; selecting a row shows that hand's replay below."""
    if df.empty:
        st.caption("No hands match.")
        return
    event = st.dataframe(df[columns], hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=key, column_config=column_config,
                         height=height or "auto")
    rows = event.selection.rows if event and event.selection else []
    if rows:
        show_replay(df.iloc[rows[0]]["hand_id"])
    else:
        st.caption("Select a row to replay the hand.")


def to_datetime(ms: pd.Series) -> pd.Series:
    return pd.to_datetime(ms, unit="ms", utc=True)


def style_fig(fig, height: int = 320):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=36, b=8), hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(zeroline=True, zerolinewidth=1)
    return fig


def page_setup(title: str, icon: str) -> None:
    st.set_page_config(page_title=f"{title} · Poker Analyzer", page_icon=icon, layout="wide")
    st.title(title)


def env_note() -> None:
    path = db.default_path()
    try:
        path = os.path.relpath(path, ROOT)
    except ValueError:  # different drive on Windows
        pass
    st.sidebar.caption(f"Database: `{path}`")
