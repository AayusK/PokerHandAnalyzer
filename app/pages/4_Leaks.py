import plotly.graph_objects as go
import streamlit as st

import common
from pokeranalyzer import leaks
from pokeranalyzer.stats import format_stat

common.page_setup("Leaks", ":material/flag:")
common.env_note()
if not common.require_data():
    st.stop()
filters = common.sidebar_filters()
hands = common.apply_filters(common.hero_hands(), filters)
if hands.empty:
    st.info("No hands match the filters.")
    st.stop()


def pct(num, den):
    return (100.0 * num / den if den else None), int(den)


# ---- Headline leak stats ---------------------------------------------------------------
ep = hands[hands["position_group"] == "EP"]
c = st.columns(4)
v, n = pct(hands["limp"].sum(), hands["pf_decision"].sum())
c[0].metric("Limp rate", format_stat(v, n), help="Calling the big blind with no raise in front (SB completes count).")
v, n = pct(ep["vpip"].sum(), ep["pf_decision"].sum())
c[1].metric("Early-position VPIP", format_stat(v, n),
            help=f"How often you play from UTG/UTG+n. Hold'em hands outside {leaks.EP_RANGE_TEXT} are flagged.")
v, n = pct(hands["bb_minraise_fold"].sum(), hands["bb_minraise_opp"].sum())
c[2].metric("BB fold vs min-raise", format_stat(v, n),
            help="Folding the big blind to a single raise of at most 2 BB. Getting 3.5:1, the BB should defend wide.")
flags = common.apply_filters(common.leak_flags(), filters)
pf = flags[flags["kind"].isin(["missed_shove", "loose_shove", "small_open_short", "loose_call_shove",
                               "tight_fold_shove"])]
gto = common.apply_filters(common.gto_rows(), filters)
gto = gto[gto["player_id"] == gto["hero_id"]]
nash = gto[gto["source"] == "nash"]
c[3].metric("Push/fold deviations", f"{len(pf)} of {len(nash)}",
            help=f"Push/fold leak flags out of your push/fold spots: Hold'em decisions at or below "
                 f"{leaks.PUSH_FOLD_MAX_BB:g} BB effective, first in or facing a lone shove, compared with the "
                 f"Nash equilibrium for that spot. Flagged when the equilibrium takes your action less than 10% "
                 f"of the time and it cost at least {leaks.MIN_FLAG_EV_BB:g} BB of chip EV, or when you opened "
                 f"for less than all-in at {leaks.SHOVE_ONLY_BB:g} BB or less.")

# ---- Flagged hands ---------------------------------------------------------------------
st.subheader("Flagged hands")
if flags.empty:
    st.caption("No flags in the selected hands.")
else:
    counts = flags["kind"].value_counts()
    kinds = st.pills("Show", list(counts.index), selection_mode="multi", default=list(counts.index),
                     format_func=lambda k: f"{leaks.KINDS.get(k, k)} ({counts[k]})", key="leak_kinds")
    shown = flags[flags["kind"].isin(kinds or [])].copy()
    shown["flag"] = shown["kind"].map(leaks.KINDS)
    shown["net_bb"] = shown["net"] / shown["big_blind"]
    common.pick_and_replay(
        shown.sort_values("started_at", ascending=False),
        ["hand_id", "session", "number", "game", "position", "hole_cards", "flag", "severity", "message", "net_bb"],
        "flagged", {"hand_id": None, "number": "hand #", "hole_cards": "cards",
                    "net_bb": st.column_config.NumberColumn("net BB", format="%+.1f"),
                    "message": st.column_config.TextColumn("why", width="large")})
st.caption("Push/fold flags compare your play with the Nash equilibrium of the push-or-fold game for each hand's "
           "real stacks, blinds and antes. It is chip EV with no ICM, so near the bubble or a pay jump a flagged "
           "fold can still be right. Flags are prompts to review a hand, not verdicts.")

# ---- GTO chart comparison ----------------------------------------------------------------
st.subheader("GTO chart comparison")
if gto.empty:
    everything = common.gto_rows()
    if (everything["player_id"] == everything["hero_id"]).any() or "holdem" not in filters["games"]:
        st.caption("No compared decisions in the selected hands. GTO comparisons are Hold'em only.")
    else:
        st.caption(f"No preflop decisions compared yet. Push/fold spots at {leaks.PUSH_FOLD_MAX_BB:g} BB or less "
                   "are compared automatically; for deeper spots, add your own charts on the GTO Charts page.")
    st.page_link("pages/7_GTO_Charts.py", label="GTO charts: push/fold explorer and your own charts",
                 icon=":material/grid_on:")
else:
    c = st.columns(4)
    c[0].metric("Decisions compared", len(gto[["hand_id", "seq"]].drop_duplicates()),
                help="Your first preflop decision in each hand that matched a chart: push/fold spots and spots "
                     "covered by your own charts. A decision that more than one chart covers counts once here and "
                     "has a row per chart below.")
    c[1].metric("Agree with the chart", format_stat(100 * gto["verdict"].eq("agrees").mean(), len(gto)),
                help="Share of comparisons (one per decision and chart) where the chart takes your action at least "
                     "half the time. Mixed: 10-50%. Deviates: under 10%. Off-chart: a limp or small raise in a "
                     "push-or-fold spot, which the model doesn't include.")
    c[2].metric("EV lost vs Nash", f"{nash['ev_loss_bb'].sum():.2f} BB",
                help=f"Chip EV given up against the Nash push/fold equilibrium, summed over {len(nash)} push/fold "
                     "decisions. Your own charts carry no EVs.")
    c[3].metric("Deviations", int(gto["verdict"].eq("deviates").sum()),
                help="Comparisons where the chart takes your action less than 10% of the time.")
    st.caption("Push/fold spots (first in, or facing a lone first-in shove, at most "
               f"{leaks.PUSH_FOLD_MAX_BB:g} BB effective) are compared with the Nash equilibrium of the push-or-fold "
               "game, solved for the hand's real stacks, blinds, antes and players left to act: chip EV, no ICM, "
               "at most one caller. Deeper raise-first-in and facing-an-open spots are compared with your own "
               "charts.")
    st.page_link("pages/7_GTO_Charts.py", label="GTO charts: push/fold explorer and your own charts",
                 icon=":material/grid_on:")
    counts = gto["verdict"].value_counts()
    order = [v for v in ["deviates", "mixed", "off-chart", "agrees"] if v in counts]
    verdicts = st.pills("Verdict", order, selection_mode="multi", default=order,
                        format_func=lambda v: f"{v} ({counts[v]})", key="gto_verdicts")
    common.gto_table(gto[gto["verdict"].isin(verdicts or [])], "gto_hero")

# ---- All-in luck -------------------------------------------------------------------------
st.subheader("All-in luck: actual vs expected")
ai = common.apply_filters(common.allin(), filters)
ai = ai[ai["player_id"] == ai["hero_id"]].sort_values("started_at")
if ai.empty:
    st.caption("No all-in hands with every player's cards shown yet.")
else:
    ai = ai.assign(expected_bb=ai["expected_net"] / ai["big_blind"], actual_bb=ai["actual_net"] / ai["big_blind"])
    c = st.columns(4)
    c[0].metric("All-in hands", len(ai))
    c[1].metric("Expected", f"{ai['expected_bb'].sum():+.1f} BB", help="What your equity was worth when the chips went in.")
    c[2].metric("Actual", f"{ai['actual_bb'].sum():+.1f} BB")
    c[3].metric("Luck", f"{ai['luck_bb'].sum():+.1f} BB", help="Actual minus expected. Positive means you ran above equity.")

    idx = list(range(1, len(ai) + 1))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=ai["actual_bb"].cumsum(), mode="lines+markers", name="Actual",
                             line=dict(color=common.BLUE, width=2), marker=dict(size=7),
                             customdata=ai[["number", "hole_cards", "equity"]],
                             hovertemplate="Hand #%{customdata[0]} %{customdata[1]}<br>equity %{customdata[2]:.0%}"
                                           "<br>cumulative %{y:+.1f} BB<extra>Actual</extra>"))
    fig.add_trace(go.Scatter(x=idx, y=ai["expected_bb"].cumsum(), mode="lines+markers", name="Expected",
                             line=dict(color=common.ORANGE, width=2), marker=dict(size=7),
                             hovertemplate="cumulative %{y:+.1f} BB<extra>Expected</extra>"))
    fig.update_layout(xaxis_title="All-in hands, in order", yaxis_title="Cumulative BB", hovermode="x unified")
    st.plotly_chart(common.style_fig(fig, 300), width="stretch")

    table = ai.assign(equity_pct=100 * ai["equity"])
    common.pick_and_replay(
        table.sort_values("started_at", ascending=False),
        ["hand_id", "session", "number", "game", "hole_cards", "street", "board", "equity_pct", "expected_bb",
         "actual_bb", "luck_bb"],
        "allin", {"hand_id": None, "number": "hand #", "hole_cards": "cards", "board": "board at all-in",
                  "equity_pct": st.column_config.NumberColumn("equity %", format="%.1f"),
                  "expected_bb": st.column_config.NumberColumn("expected BB", format="%+.1f"),
                  "actual_bb": st.column_config.NumberColumn("actual BB", format="%+.1f"),
                  "luck_bb": st.column_config.NumberColumn("luck BB", format="%+.1f")})
    st.caption("Equity is measured when the last chips went in (exact with two or fewer cards to come, "
               "Monte Carlo otherwise), split across main and side pots.")
