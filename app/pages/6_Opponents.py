import pandas as pd
import streamlit as st

import common
from pokeranalyzer import db
from pokeranalyzer.replay import betting_line
from pokeranalyzer.stats import STAT_HELP, STAT_LABELS, display_table, format_stat, summarize

common.page_setup("Opponents", ":material/groups:")
common.env_note()
if not common.require_data():
    st.stop()
filters = common.sidebar_filters()
everyone = common.apply_filters(common.all_player_hands(), filters)
opps = everyone[everyone["player_id"] != everyone["hero_id"]]
if opps.empty:
    st.info("No opponents in the selected hands.")
    st.stop()

# ---- Overview of every opponent -------------------------------------------------------
summary = summarize(opps, "player_id")
names = common.players().set_index("id")
summary["name"] = summary["player_id"].map(names["name"])
summary["aliases"] = summary["player_id"].map(names["names"]).fillna("")
summary["shown"] = summary["player_id"].map(opps.groupby("player_id")["showed"].sum()).astype(int)
summary["sessions"] = summary["player_id"].map(opps.groupby("player_id")["session"].nunique())
summary = summary.sort_values(["hands", "name"], ascending=[False, True]).reset_index(drop=True)

overview = pd.DataFrame({
    "player_id": summary["player_id"],
    "name": summary["name"],
    "hands": summary["hands"],
    "sessions": summary["sessions"],
    "shown": summary["shown"],
    "VPIP": [format_stat(v, n) for v, n in zip(summary["vpip"], summary["vpip_n"])],
    "PFR": [format_stat(v, n) for v, n in zip(summary["pfr"], summary["pfr_n"])],
    "3-bet": [format_stat(v, n) for v, n in zip(summary["three_bet"], summary["three_bet_n"])],
    "WTSD": [format_stat(v, n) for v, n in zip(summary["wtsd"], summary["wtsd_n"])],
    "AF": [format_stat(v, n, ratio=True) for v, n in zip(summary["af"], summary["af_n"])],
    "net BB": summary["net_bb"].round(1),
})
query = st.text_input("Find a player", placeholder="Name")
if query:
    keep = summary["name"].str.contains(query, case=False, na=False) | \
        summary["aliases"].str.contains(query, case=False, na=False)
    overview = overview[keep.values]
st.caption(f"{len(overview)} opponents. Select one for their full stats and every hand they showed.")
event = st.dataframe(overview, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                     key="opp_table", column_config={"player_id": None, "shown": "hands shown"}, height=360)
rows = event.selection.rows if event and event.selection else []
if not rows:
    st.stop()

pid = overview.iloc[rows[0]]["player_id"]
mine = opps[opps["player_id"] == pid]
name = names.loc[pid, "name"] if pid in names.index else pid
st.subheader(name)
aliases = names.loc[pid, "names"] if pid in names.index else ""
if aliases and aliases != name:
    st.caption(f"Also seen as: {aliases}")

c = st.columns(4)
c[0].metric("Hands together", len(mine))
c[1].metric("Sessions", mine["session"].nunique())
c[2].metric("Their net", f"{mine['net_bb'].sum():+.1f} BB")
c[3].metric("Hands shown", int(mine["showed"].sum()))

by = st.segmented_control("Slice by", ["Overall", "Position group", "Game"], default="Overall", key="opp_slice")
col = {"Position group": "position_group", "Game": "game"}.get(by or "Overall")
table = display_table(summarize(mine, col), [col] if col else [])
st.dataframe(table, hide_index=True, width="stretch",
             column_config={label: st.column_config.TextColumn(label, help=STAT_HELP[key])
                            for key, label in STAT_LABELS.items()})

# ---- Hands they showed ------------------------------------------------------------------
st.subheader("Hands they showed")
shown = mine[mine["showed"]]
if shown.empty:
    st.caption("They haven't shown any cards yet.")
    st.stop()


@st.cache_data(show_spinner=False)
def shown_rows(player_id: str, hand_ids: tuple[str, ...], version: float) -> pd.DataFrame:
    out = []
    for h in db.load_hands(common.get_conn(), list(hand_ids)):
        p = h.player(player_id)
        desc = next((a.hand_description for a in h.awards if a.player_id == player_id and a.hand_description), "")
        out.append({"hand_id": h.id, "line": betting_line(h, player_id), "made": desc,
                    "result": "won" if p.won > p.invested else ("lost" if p.net < 0 else "even")})
    return pd.DataFrame(out)


extra = shown_rows(pid, tuple(shown["hand_id"]), common.db_version())
shown = shown.merge(extra, on="hand_id").assign(
    time=lambda d: common.to_datetime(d["started_at"]).dt.strftime("%Y-%m-%d %H:%M"),
    game_label=lambda d: d["game"].map(common.GAME_LABELS)).sort_values("started_at", ascending=False)
common.pick_and_replay(
    shown, ["hand_id", "time", "session", "number", "game_label", "position", "hole_cards", "board", "line", "made",
            "net_bb"],
    "opp_shown", {"hand_id": None, "number": "hand #", "game_label": "game", "hole_cards": "cards",
                  "line": st.column_config.TextColumn("betting line", help="X check, C call, B bet, R raise, F fold, "
                                                                           "* all-in"),
                  "made": "winning hand", "net_bb": st.column_config.NumberColumn("their net BB", format="%+.1f")})
