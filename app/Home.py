"""Poker Analyzer: Streamlit entry point. Run with `streamlit run app/Home.py`."""

import streamlit as st

import common

common.page_setup("Poker Analyzer", "♠")
common.env_note()

st.write("Reads PokerNow hand-history exports, finds leaks in your play, tracks results over time "
         "and scouts opponents.")

if common.require_data():
    hands = common.hero_hands()
    s = common.sessions()
    ai = common.allin()
    luck_bb = ai.loc[ai["player_id"] == ai["hero_id"], "luck_bb"].sum()

    c = st.columns(4)
    c[0].metric("Sessions", len(s))
    c[1].metric("Hands", f"{len(hands):,}")
    c[2].metric("Net result", f"{hands['net_bb'].sum():+.1f} BB")
    c[3].metric("All-in luck", f"{luck_bb:+.1f} BB",
                help="Chips won in all-in hands above what your equity was worth. Positive means you ran good.")

    if len(hands) < 500:
        st.caption(f"{len(hands)} hands so far. Tracker stats need hundreds to thousands of hands to settle, "
                   "so judge single sessions hand by hand (Leaks and Hands pages) and read the stats as early signals.")

st.subheader("Pages")
st.page_link("pages/1_Import.py", label="Import: add exports, enter tournament results", icon=":material/upload:")
st.page_link("pages/2_Results.py", label="Results: stack graphs, results by position, game and stack depth",
             icon=":material/show_chart:")
st.page_link("pages/3_My_Stats.py", label="My stats: VPIP, PFR, 3-bet, c-bet and more, with sample sizes",
             icon=":material/table_chart:")
st.page_link("pages/4_Leaks.py", label="Leaks: flagged hands, push/fold checks, all-in luck", icon=":material/flag:")
st.page_link("pages/5_Hands.py", label="Hands: filter and replay any hand", icon=":material/playing_cards:")
st.page_link("pages/6_Opponents.py", label="Opponents: stats and every hand they showed", icon=":material/groups:")
