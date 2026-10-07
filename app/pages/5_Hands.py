import streamlit as st

import common
from pokeranalyzer.parser import POSITION_ORDER

common.page_setup("Hands", ":material/playing_cards:")
common.env_note()
if not common.require_data():
    st.stop()
filters = common.sidebar_filters()
hands = common.apply_filters(common.hero_hands(), filters)
if hands.empty:
    st.info("No hands match the filters.")
    st.stop()

flags = common.leak_flags()
ai = common.allin()
flagged_ids = set(flags["hand_id"])
allin_ids = set(ai.loc[ai["player_id"] == ai["hero_id"], "hand_id"])

c = st.columns([2, 2, 2, 3])
positions = c[0].multiselect("Position", [p for p in POSITION_ORDER if p in set(hands["position"])],
                             placeholder="Any position")
result = c[1].segmented_control("Result", ["Won", "Lost", "Even"], selection_mode="multi", key="hand_result")
only = c[2].pills("Only", ["Showdown", "All-in", "Flagged", "Saw flop"], selection_mode="multi", key="hand_only")
cards = c[3].text_input("Cards contain", placeholder="e.g. A or Ks", help="Matches your hole cards.")

view = hands
if positions:
    view = view[view["position"].isin(positions)]
if result:
    keep = ((view["net"] > 0) & ("Won" in result)) | ((view["net"] < 0) & ("Lost" in result)) | \
           ((view["net"] == 0) & ("Even" in result))
    view = view[keep]
for o in only or []:
    if o == "Showdown":
        view = view[view["at_showdown"]]
    elif o == "All-in":
        view = view[view["hand_id"].isin(allin_ids)]
    elif o == "Flagged":
        view = view[view["hand_id"].isin(flagged_ids)]
    elif o == "Saw flop":
        view = view[view["saw_flop"]]
if cards:
    terms = cards.replace(",", " ").split()
    view = view[view["hole_cards"].map(lambda hc: all(t.lower() in hc.lower() for t in terms))]

view = view.sort_values("started_at", ascending=False).assign(
    time=lambda d: common.to_datetime(d["started_at"]).dt.strftime("%Y-%m-%d %H:%M"),
    flag=lambda d: d["hand_id"].isin(flagged_ids).map({True: "⚑", False: ""}),
    game_label=lambda d: d["game"].map(common.GAME_LABELS),
    blinds=lambda d: d["small_blind"].astype(str) + "/" + d["big_blind"].astype(str))
st.caption(f"{len(view)} of {len(hands)} hands")
common.pick_and_replay(
    view, ["hand_id", "time", "session", "number", "table_no", "game_label", "blinds", "num_players", "position",
           "hole_cards", "board", "net_bb", "flag"],
    "hand_list", {"hand_id": None, "number": "hand #", "table_no": "table", "game_label": "game",
                  "num_players": "players", "hole_cards": "cards",
                  "net_bb": st.column_config.NumberColumn("net BB", format="%+.1f")}, height=420)
