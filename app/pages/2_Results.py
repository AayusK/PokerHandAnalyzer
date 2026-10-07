import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
from pokeranalyzer.stats import STACK_BUCKET_ORDER
from pokeranalyzer.parser import POSITION_ORDER

common.page_setup("Results", ":material/show_chart:")
common.env_note()
if not common.require_data():
    st.stop()
filters = common.sidebar_filters()
hands = common.apply_filters(common.hero_hands(), filters)
if hands.empty:
    st.info("No hands match the filters.")
    st.stop()

hands = hands.sort_values(["started_at", "hand_id"]).reset_index(drop=True)
s = common.sessions()
s = s[s["slug"].isin(filters["sessions"])]

c = st.columns(5)
c[0].metric("Hands", f"{len(hands):,}")
c[1].metric("Net", f"{hands['net_bb'].sum():+.1f} BB")
c[2].metric("BB/100", f"{100 * hands['net_bb'].mean():+.1f}")
paid = s.dropna(subset=["prize", "buy_in"], how="all")
profit = paid["prize"].fillna(0).sum() - paid["buy_in"].fillna(0).sum()
c[3].metric("Tournament profit", f"{profit:+,.2f}" if not paid.empty else "—",
            help="Prize minus buy-in, for sessions where you entered them on the Import page.")
c[4].metric("Sessions", len(s))

# ---- Stack across a tournament ---------------------------------------------------------
st.subheader("Stack across the tournament")
slugs = [x for x in s["slug"] if x in set(hands["session"])]
col1, col2 = st.columns([3, 1])
slug = col1.selectbox("Session", slugs, index=len(slugs) - 1, format_func=common.session_label)
unit = col2.segmented_control("Show", ["Chips", "Big blinds"], default="Chips", key="stack_unit") or "Chips"
one = hands[hands["session"] == slug].reset_index(drop=True)
if not one.empty:
    x = list(range(len(one) + 1))
    if unit == "Chips":
        y = [one["start_stack"].iloc[0]] + one["end_stack"].tolist()
        fmt = ",.0f"
    else:
        y = [one["start_stack"].iloc[0] / one["big_blind"].iloc[0]] + (one["end_stack"] / one["big_blind"]).tolist()
        fmt = ".1f"
    labels = ["start"] + [f"Hand #{n} · table {t} · {g} {sb}/{bb} · {pos}<br>net {nb:+.1f} BB"
                          for n, t, g, sb, bb, pos, nb in zip(one["number"], one["table_no"],
                                                             one["game"].map(common.GAME_LABELS), one["small_blind"],
                                                             one["big_blind"], one["position"], one["net_bb"])]
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers", line=dict(color=common.BLUE, width=2),
                               marker=dict(size=6), text=labels,
                               hovertemplate=f"%{{text}}<br>stack %{{y:{fmt}}}<extra></extra>", name="Stack"))
    for i in range(1, len(one)):
        if one["table_no"].iloc[i] != one["table_no"].iloc[i - 1]:
            fig.add_vline(x=i, line=dict(color=common.GRAY, width=1))
            fig.add_annotation(x=i, y=1, yref="paper", text=f"table {one['table_no'].iloc[i]}", showarrow=False,
                               xanchor="left", font=dict(size=11, color=common.GRAY))
    fig.update_layout(xaxis_title="Hands played", yaxis_title=unit)
    st.plotly_chart(common.style_fig(fig, 340), width="stretch")
    st.caption(f"{len(one)} hands · {one['net_bb'].sum():+.1f} BB · finished with {one['end_stack'].iloc[-1]:,} chips")

# ---- Results by position, game, stack depth -------------------------------------------
st.subheader("Results by position, game and stack depth")
metric = st.segmented_control("Measure", ["Net BB", "BB/100"], default="Net BB", key="res_metric") or "Net BB"


def results_bar(by: str, order: list[str] | None, title: str, labels: dict | None = None):
    g = hands.groupby(by).agg(hands=("net_bb", "size"), net=("net_bb", "sum"), mean=("net_bb", "mean")).reset_index()
    if order:
        g[by] = pd.Categorical(g[by], [o for o in order if o in set(g[by])], ordered=True)
        g = g.sort_values(by)
    g["value"] = g["net"] if metric == "Net BB" else 100 * g["mean"]
    names = g[by].astype(str).map(lambda v: (labels or {}).get(v, v))
    fig = go.Figure(go.Bar(
        x=names, y=g["value"], marker=dict(color=[common.BLUE if v >= 0 else common.RED for v in g["value"]],
                                           cornerradius=4),
        customdata=g[["hands", "net"]], text=[f"n={n}" for n in g["hands"]], textposition="outside",
        textfont=dict(size=11, color=common.GRAY),
        hovertemplate="%{x}<br>" + metric + " %{y:+.1f}<br>%{customdata[0]} hands<extra></extra>"))
    fig.update_layout(title=dict(text=title, font=dict(size=14)), yaxis_title=metric, bargap=0.35)
    return common.style_fig(fig, 300)


c = st.columns(3)
c[0].plotly_chart(results_bar("position", POSITION_ORDER, "By position"), width="stretch")
c[1].plotly_chart(results_bar("game", ["holdem", "plo"], "By game", common.GAME_LABELS), width="stretch")
c[2].plotly_chart(results_bar("stack_depth", STACK_BUCKET_ORDER, "By starting stack"), width="stretch")
st.caption("n = hands. Single tournaments are far too small for these to mean much; they firm up as sessions add up.")

# ---- Cumulative across sessions -------------------------------------------------------
st.subheader("Cumulative results")
ai = common.allin()
luck = ai[ai["player_id"] == ai["hero_id"]].set_index("hand_id")["luck_bb"]
hands["luck_bb"] = hands["hand_id"].map(luck).fillna(0.0)
hands["cum_net"] = hands["net_bb"].cumsum()
hands["cum_adj"] = (hands["net_bb"] - hands["luck_bb"]).cumsum()
idx = list(range(1, len(hands) + 1))
fig = go.Figure()
fig.add_trace(go.Scatter(x=idx, y=hands["cum_net"], mode="lines", name="Actual", line=dict(color=common.BLUE, width=2),
                         customdata=hands[["number", "session"]],
                         hovertemplate="Hand #%{customdata[0]} (%{customdata[1]})<br>%{y:+.1f} BB<extra>Actual</extra>"))
fig.add_trace(go.Scatter(x=idx, y=hands["cum_adj"], mode="lines", name="All-in adjusted",
                         line=dict(color=common.ORANGE, width=2),
                         hovertemplate="%{y:+.1f} BB<extra>All-in adjusted</extra>"))
starts = hands.index[hands["session"] != hands["session"].shift()].tolist()[1:]
for i in starts:
    fig.add_vline(x=i + 0.5, line=dict(color=common.GRAY, width=1))
fig.update_layout(xaxis_title="Hands (all sessions, in order)", yaxis_title="Cumulative BB", hovermode="x unified")
st.plotly_chart(common.style_fig(fig, 340), width="stretch")
st.caption("All-in adjusted replaces the result of each all-in hand with what your equity was worth when the "
           "chips went in. Vertical lines separate sessions.")

if not paid.empty:
    t = s[["slug", "first_hand_at", "finishing_place", "field_size", "buy_in", "prize"]].copy()
    t["profit"] = t["prize"].fillna(0) - t["buy_in"].fillna(0)
    t["cumulative profit"] = t["profit"].cumsum()
    t["first_hand_at"] = common.to_datetime(t["first_hand_at"]).dt.strftime("%Y-%m-%d")
    st.dataframe(t.rename(columns={"slug": "session", "first_hand_at": "date", "finishing_place": "place",
                                   "field_size": "field", "buy_in": "buy-in"}), hide_index=True, width="stretch")

# ---- Biggest pots ----------------------------------------------------------------------
st.subheader("Biggest wins and losses")
cols = ["hand_id", "session", "number", "game", "position", "hole_cards", "board", "net_bb"]
cfg = {"hand_id": None, "number": "hand #", "hole_cards": "cards",
       "net_bb": st.column_config.NumberColumn("net BB", format="%+.1f")}
wins, losses = st.tabs(["Biggest wins", "Biggest losses"])
with wins:
    common.pick_and_replay(hands[hands["net_bb"] > 0].nlargest(10, "net_bb"), cols, "wins", cfg)
with losses:
    common.pick_and_replay(hands[hands["net_bb"] < 0].nsmallest(10, "net_bb"), cols, "losses", cfg)
