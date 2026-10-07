import pandas as pd
import streamlit as st

import common
from pokeranalyzer.stats import STAT_HELP, STAT_LABELS, display_table, format_stat, summarize

common.page_setup("My stats", ":material/table_chart:")
common.env_note()
if not common.require_data():
    st.stop()
filters = common.sidebar_filters()
hands = common.apply_filters(common.hero_hands(), filters)
if hands.empty:
    st.info("No hands match the filters.")
    st.stop()

hands = hands.assign(table_size=hands["num_players"].map(
    lambda n: "Heads-up" if n == 2 else ("3–6 handed" if n <= 6 else "7+ handed")))

SLICES = {
    "Overall": None,
    "Position": "position",
    "Position group": "position_group",
    "Game": "game",
    "Stack depth": "stack_depth",
    "Table size": "table_size",
    "Session": "session",
}
choice = st.segmented_control("Slice by", list(SLICES), default="Overall", key="stats_slice") or "Overall"
by = SLICES[choice]
summary = summarize(hands, by)
if by is None:
    row = summary.iloc[0]
    c = st.columns(3)
    c[0].metric("Hands", f"{int(row['hands']):,}")
    c[1].metric("Net", f"{row['net_bb']:+.1f} BB")
    c[2].metric("BB/100", f"{row['bb_per_100']:+.1f}")
    table = pd.DataFrame({
        "stat": list(STAT_LABELS.values()),
        "value": [format_stat(row[k], int(row[f"{k}_n"]), ratio=k == "af").split(" (")[0] for k in STAT_LABELS],
        "sample (n)": [int(row[f"{k}_n"]) for k in STAT_LABELS],
        "what it measures": [STAT_HELP[k] for k in STAT_LABELS],
    })
    st.dataframe(table, hide_index=True, width="stretch", height=35 * (len(table) + 1) + 3,
                 column_config={"what it measures": st.column_config.TextColumn(width="large")})
else:
    table = display_table(summary, [by])
    if by == "game":
        table["game"] = table["game"].map(common.GAME_LABELS)
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={label: st.column_config.TextColumn(label, help=STAT_HELP[key])
                                for key, label in STAT_LABELS.items()})
n = len(hands)
st.caption(f"n is the number of chances each stat was measured on. {n} hands in total"
           + (" — far too few for these to settle; treat them as early signals." if n < 500 else "."))

with st.expander("What the stats mean"):
    for key, label in STAT_LABELS.items():
        st.markdown(f"**{label}**: {STAT_HELP[key]}")
    st.markdown("Hands where you never had a preflop decision (a walk in the big blind, or all-in from posting) "
                "are left out of the preflop stats.")
