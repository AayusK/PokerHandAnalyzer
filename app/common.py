"""Shared helpers for the Streamlit pages: database access, caching, filters, colors, hand charts."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokeranalyzer import db  # noqa: E402
from pokeranalyzer.gto import range_percent  # noqa: E402
from pokeranalyzer.ranges import CLASS_INDEX, CLASSES  # noqa: E402
from pokeranalyzer.replay import replay_text  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"

# Chart colors (validated default palette, see the dataviz reference): one hue per job.
BLUE = "#2a78d6"     # main series / gains
RED = "#e34948"      # losses (diverging pole)
ORANGE = "#eb6834"   # second series
GRAY = "#898781"     # muted marks, reference lines
GAME_LABELS = {"holdem": "Hold'em", "plo": "PLO"}

# 13x13 hand charts: one-hue sequential blue ramp, light (rarely) to dark (always); never = gray.
GRID_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
GRID_NEVER = "#f0efec"
GRID_NEVER_BELOW = 0.005          # frequencies that round to 0% are drawn as never
INK_DARK, INK_LIGHT = "#0b0b0b", "#ffffff"

ACTION_LABELS = {"push": "Push", "call": "Call", "fold": "Fold", "raise": "Raise", "limp": "Limp", "allin": "All-in",
                 "3bet": "3-bet"}
VERDICT_HELP = {
    "agrees": "the chart takes this action at least half the time",
    "mixed": "the chart takes this action 10-50% of the time",
    "deviates": "the chart takes this action less than 10% of the time",
    "off-chart": "an action the chart doesn't model, such as a limp or a small raise in a push-or-fold spot",
}


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


@st.cache_data(show_spinner=False)
def _gto(version: float) -> pd.DataFrame:
    return db.gto_df(get_conn())


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


def gto_rows() -> pd.DataFrame:
    """Every stored GTO chart comparison (hero and opponents who showed)."""
    return _gto(db_version())


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


def show_replay(hand_id: str, hero_flags: bool = True) -> None:
    """The hand's replay, your leak flags in it (unless ``hero_flags`` is False) and all-in equities."""
    hand = db.load_hand(get_conn(), hand_id)
    if hand is None:
        st.warning("Hand not found.")
        return
    st.code(replay_text(hand), language=None)
    flags = leak_flags()
    mine = flags[flags["hand_id"] == hand_id] if hero_flags else flags.iloc[:0]
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


def pick_row(df: pd.DataFrame, columns: list[str], key: str, column_config: dict | None = None,
             height: int | None = None) -> pd.Series | None:
    """Show a table with single-row selection; returns the selected row of ``df``, if any.

    The selection is kept by row position, so the widget key includes the rows shown: when a
    filter changes them the selection resets instead of landing on a different hand.
    """
    rows_id = hashlib.sha1(repr(df.index.tolist()).encode()).hexdigest()[:12]
    event = st.dataframe(df[columns], hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"{key}_{rows_id}", column_config=column_config,
                         height=height or "auto")
    rows = event.selection.rows if event and event.selection else []
    return df.iloc[rows[0]] if rows and rows[0] < len(df) else None


def pick_and_replay(df: pd.DataFrame, columns: list[str], key: str, column_config: dict | None = None,
                    height: int | None = None) -> None:
    """Show a table of hands; selecting a row shows that hand's replay below."""
    if df.empty:
        st.caption("No hands match.")
        return
    row = pick_row(df, columns, key, column_config, height)
    if row is not None:
        show_replay(row["hand_id"])
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


# ---------------------------------------------------------------------------------------
# 13x13 hand charts and GTO comparisons


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _grid_scale() -> list[list]:
    """Plotly colorscale on 0..1: gray below GRID_NEVER_BELOW, then the blue ramp."""
    n = len(GRID_RAMP)
    scale = [[0.0, GRID_NEVER], [GRID_NEVER_BELOW, GRID_NEVER]]
    return scale + [[GRID_NEVER_BELOW + (1 - GRID_NEVER_BELOW) * i / (n - 1), c] for i, c in enumerate(GRID_RAMP)]


def _cell_rgb(v: float) -> tuple[float, ...]:
    """The fill Plotly draws for frequency v (linear RGB interpolation along the ramp)."""
    if v < GRID_NEVER_BELOW:
        return _rgb(GRID_NEVER)
    t = min(1.0, (v - GRID_NEVER_BELOW) / (1 - GRID_NEVER_BELOW)) * (len(GRID_RAMP) - 1)
    lo = min(int(t), len(GRID_RAMP) - 2)
    a, b = _rgb(GRID_RAMP[lo]), _rgb(GRID_RAMP[lo + 1])
    return tuple(x + (y - x) * (t - lo) for x, y in zip(a, b))


def _luminance(rgb: tuple[float, ...]) -> float:
    c = [x / 255 for x in rgb]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ink(v: float) -> str:
    """Dark or white label text, whichever contrasts more with the cell."""
    lum = _luminance(_cell_rgb(v))
    dark = (lum + 0.05) / (_luminance(_rgb(INK_DARK)) + 0.05)
    return INK_DARK if dark >= 1.05 / (lum + 0.05) else INK_LIGHT


def pct(freq: float) -> str:
    return f"{100 * freq:.0f}%"


def signed_bb(x: float) -> str:
    """'+0.42 BB'; never '-0.00 BB'."""
    return f"{round(float(x), 2) + 0.0:+.2f} BB"


def range_grid(freqs, action: str, highlight: str | None = None, evs=None, height: int = 460) -> go.Figure:
    """13x13 chart of how often a range takes ``action`` with each hand class (values in CLASSES order).

    ``highlight`` outlines one class; ``evs`` (BB, action vs fold per class) is added to the hover.
    """
    z = np.asarray(freqs, dtype=float).reshape(13, 13)
    hover = [[f"{CLASSES[r * 13 + c]}<br><b>{action} {pct(z[r, c])}</b>"
              + (f"<br>{action} vs fold: {signed_bb(evs[r * 13 + c])}" if evs is not None else "")
              for c in range(13)] for r in range(13)]
    fig = go.Figure(go.Heatmap(
        z=z, x=list(range(13)), y=list(range(13)), zmin=0, zmax=1, colorscale=_grid_scale(), showscale=False,
        xgap=2, ygap=2, text=hover, hovertemplate="%{text}<extra></extra>"))
    cells = [(r, c) for r in range(13) for c in range(13)]
    labels = [f"<b>{CLASSES[r * 13 + c]}</b>" if CLASSES[r * 13 + c] == highlight else CLASSES[r * 13 + c]
              for r, c in cells]
    fig.add_trace(go.Scatter(
        x=[c for _, c in cells], y=[r for r, _ in cells], mode="text", text=labels, hoverinfo="skip",
        textfont=dict(size=max(8, min(12, height // 40)), color=[_ink(z[r, c]) for r, c in cells])))
    if highlight in CLASS_INDEX:
        r, c = divmod(CLASS_INDEX[highlight], 13)
        fig.add_shape(type="rect", x0=c - 0.5, x1=c + 0.5, y0=r - 0.5, y1=r + 0.5, layer="above",
                      line=dict(color=ORANGE, width=3))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0), showlegend=False, dragmode=False,
                      hovermode="closest", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(visible=False, range=[-0.5, 12.5], fixedrange=True, constrain="domain", constraintoward="left")
    fig.update_yaxes(visible=False, range=[12.5, -0.5], fixedrange=True, constrain="domain", constraintoward="top",
                     scaleanchor="x")
    return fig


def show_grid(freqs, action: str, key: str, highlight: str | None = None, evs=None, height: int = 460) -> None:
    st.plotly_chart(range_grid(freqs, action, highlight, evs, height), width="stretch", key=key,
                    config={"displayModeBar": False, "scrollZoom": False})


def grid_legend(outline: str | None = None) -> None:
    """Caption-sized key for the hand charts (swatches at full strength, text muted); ``outline`` labels
    the orange outline when a hand is outlined."""
    box = "display:inline-block;height:12px;vertical-align:-2px;border-radius:2px;margin:0 4px 0 12px"
    muted = '<span style="opacity:.6">{}</span>'
    parts = [f'<span style="{box};width:12px;margin-left:0;background:{GRID_NEVER};'
             f'box-shadow:inset 0 0 0 1px rgba(128,128,128,.35)"></span>' + muted.format("never"),
             f'<span style="{box};width:80px;background:linear-gradient(90deg,{",".join(GRID_RAMP)})"></span>'
             + muted.format("sometimes → always")]
    if outline:
        parts.append(f'<span style="{box};width:12px;box-sizing:border-box;border:3px solid {ORANGE}"></span>'
                     + muted.format(outline))
    parts.append(muted.format(" · Rows and columns run A to 2: suited above the diagonal, offsuit below."))
    st.markdown(f'<div style="font-size:14px;line-height:1.6">{"".join(parts)}</div>', unsafe_allow_html=True)


def range_summary(freqs, action: str) -> str:
    """E.g. "push 34.5% of hands · mixed: K9o 43%, Q8s 71%"."""
    f = np.asarray(freqs, dtype=float)
    mixed = [f"{CLASSES[i]} {pct(v)}" for i, v in enumerate(f) if GRID_NEVER_BELOW <= v < 1 - GRID_NEVER_BELOW]
    text = f"{action} {range_percent(f):.1f}% of hands"
    if mixed:
        text += " · mixed: " + ", ".join(mixed[:12]) + (f" and {len(mixed) - 12} more" if len(mixed) > 12 else "")
    return text


def chart_says(freqs: dict) -> str:
    """Chart frequencies, most frequent first: "push 100%", "3bet 30% · call 50% · fold 20%"."""
    shown = sorted(((a, f) for a, f in freqs.items() if f >= GRID_NEVER_BELOW), key=lambda x: -x[1])
    return " · ".join(f"{a} {pct(f)}" for a, f in shown)


def source_label(source: str) -> str:
    return "Nash push/fold" if source == "nash" else f"chart: {source}"


def show_gto_spot(row: pd.Series, key: str) -> None:
    """One compared decision: the spot's chart with the hand outlined, what the chart says, then the replay."""
    cls, grid = row["hand_class"], row["grid"]
    who = "You" if row["player_id"] == row["hero_id"] else row["name"]
    source = ("the computed Nash push/fold equilibrium" if row["source"] == "nash"
              else f"your chart “{row['source']}”")
    st.markdown(f"#### {row['title']}")
    st.caption(f"Hand #{row['number']} · {session_label(row['session'])} · compared with {source}")
    details = [f"{who} held **{cls}** ({row['hole_cards']}) and chose **{row['action']}**.",
               f"Chart: {chart_says(row['freqs'])}.",
               f"Verdict: **{row['verdict']}** ({VERDICT_HELP.get(row['verdict'], '')})."]
    if row["evs"] is not None and cls in CLASS_INDEX:
        act = next(iter(grid))
        details.append(f"{ACTION_LABELS.get(act, act)} vs fold with {cls}: {signed_bb(row['evs'][CLASS_INDEX[cls]])} "
                       "of chip EV.")
    if pd.notna(row["ev_loss_bb"]):
        details.append(f"EV lost: **{row['ev_loss_bb']:.2f} BB**.")
    left, right = st.columns([3, 2], gap="large")
    action = next(iter(grid))
    with left:
        if len(grid) > 1:
            # One readable grid at a time: the action taken (or the chart's main one) first, fold included.
            options = list(grid) + ["fold"]
            default = next((a for a in (row["action"], row["chart_action"]) if a in options), action)
            action = st.segmented_control(
                "Chart for", options, default=default, format_func=lambda a: ACTION_LABELS.get(a, a),
                key=f"{key}_action_{row['hand_id']}_{row['seq']}_{row['source']}") or default
        freqs = (np.clip(1.0 - np.sum(list(grid.values()), axis=0), 0.0, 1.0) if action not in grid
                 else grid[action])
        show_grid(freqs, action, f"{key}_grid_{action}", highlight=cls, evs=row["evs"] if len(grid) == 1 else None)
    with right:
        st.markdown("  \n".join(details))
        st.caption(range_summary(freqs, action))
    grid_legend(outline="the hand held")
    show_replay(row["hand_id"], hero_flags=row["player_id"] == row["hero_id"])


def gto_table(df: pd.DataFrame, key: str, action_label: str = "your action", height: int | None = None) -> None:
    """Table of GTO chart comparisons; selecting a row shows that spot's chart and the hand's replay."""
    if df.empty:
        st.caption("No decisions match.")
        return
    view = df.sort_values(["started_at", "seq"], ascending=False).assign(
        says=lambda d: d["freqs"].map(chart_says), chart=lambda d: d["source"].map(source_label),
        ev_lost=lambda d: d["ev_loss_bb"].map(lambda v: f"{v:.2f}" if pd.notna(v) else ""))
    row = pick_row(
        view, ["hand_id", "number", "title", "hole_cards", "hand_class", "action", "says", "verdict", "ev_lost",
               "chart", "session"],
        key, {"hand_id": None, "number": "hand #",
              "title": st.column_config.TextColumn("spot", width="medium"),
              "hole_cards": "cards", "hand_class": "class", "action": action_label, "says": "chart says",
              "verdict": st.column_config.TextColumn("verdict", help="agrees: the chart takes this action at "
                                                     "least half the time; mixed: 10-50%; deviates: under 10%; "
                                                     "off-chart: an action the chart doesn't model."),
              "ev_lost": st.column_config.TextColumn("EV lost BB", alignment="right",
                                                     help="Chip EV given up against the Nash push/fold "
                                                          "equilibrium. Your own charts carry no EVs."),
              "chart": "compared with"}, height)
    if row is None:
        st.caption("Select a row to see the chart for that spot and replay the hand.")
    else:
        show_gto_spot(row, key)
