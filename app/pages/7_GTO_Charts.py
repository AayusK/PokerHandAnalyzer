import os

import numpy as np
import streamlit as st

import common
from pokeranalyzer import charts, gto
from pokeranalyzer.parser import assign_positions
from pokeranalyzer.ranges import CLASSES

common.page_setup("GTO charts", ":material/grid_on:")
common.env_note()
st.write("Preflop charts your decisions are compared with: the computed Nash push/fold equilibrium for short "
         "stacks, and your own charts for deeper spots. The Leaks page lists the comparisons for your hands.")


def table_positions(k: int) -> tuple[str, list[str]]:
    """Pusher and caller positions with k players left to act, the pusher first in at a (k + 1)-handed table."""
    if k == 1:
        return "SB", ["BB"]
    n = k + 1
    names = assign_positions(list(range(1, n + 1)), dealer_seat=n - 2, sb_seat=n - 1, bb_seat=n)
    return names[1], [names[s] for s in range(2, n + 1)]


# ---- Nash push/fold explorer -------------------------------------------------------------
st.subheader("Nash push/fold explorer")
c = st.columns(3, gap="large")
k = c[0].slider("Players left to act", 1, 8, 3, key="nash_k",
                help="Players still to act behind the shover; the last two are the blinds. "
                     "With one, the small blind shoves into the big blind.")
stack = c[1].slider("Effective stack (BB)", 1.0, 20.0, 10.0, step=0.5, format="%.1f", key="nash_stack",
                    help="Everyone's stack after the ante, in big blinds.")
ante = c[2].slider("Ante (BB)", 0.0, 0.25, 0.0, step=0.01, format="%.2f", key="nash_ante",
                   help="Ante per player in big blinds, posted by the shover and every player behind.")
pusher, callers = table_positions(k)
c = st.columns([3, 1], gap="large")
caller = c[0].segmented_control("Caller", callers, default=callers[-1], key=f"nash_caller_{k}",
                                help="Whose call chart to show. Each caller's range assumes everyone between "
                                     "the shover and them folded.") or callers[-1]
outline = c[1].selectbox("Outline a hand", [""] + CLASSES, format_func=lambda x: x or "None", key="nash_outline")

spot = gto.symmetric_spot(k, stack, ante)
with st.spinner("Solving…"):
    sol = gto.solve(spot)
j = callers.index(caller)
left, right = st.columns(2, gap="large")
with left:
    st.metric(f"{pusher} shoves", f"{sol.push_percent():.1f}% of hands",
              help=f"{pusher} first in with {k} left to act, {stack:g} BB each.")
    common.show_grid(sol.push, "push", "nash_push", highlight=outline or None, evs=sol.push_ev)
    st.caption(common.range_summary(sol.push, "push"))
with right:
    st.metric(f"{caller} calls the shove", f"{sol.call_percent(j):.1f}% of hands",
              help=f"{caller} facing {pusher}'s shove" + (", everyone between folded." if j else "."))
    common.show_grid(sol.call[j], "call", f"nash_call_{j}", highlight=outline or None, evs=sol.call_ev[j])
    st.caption(common.range_summary(sol.call[j], "call"))
common.grid_legend(outline=f"{outline} outlined" if outline else None)
gain = f"{sol.exploitability:.4f} BB" if sol.exploitability >= 1e-4 else "0.0001 BB"
st.caption(f"Hover a cell for the frequency and the chip EV of shoving (or calling) instead of folding. "
           f"Solved in {sol.iterations} iterations; no player can gain more than {gain} by deviating.")
if stack > gto.NASH_MAX_BB:
    st.caption(f"Your hands are compared with these charts only at {gto.NASH_MAX_BB:g} BB effective or less; "
               "deeper, the push-or-fold model leaves out too many options.")

with st.expander("How the push/fold charts are computed", expanded=False):
    st.markdown(
        "The charts are the Nash equilibrium of the push-or-fold game: the first player in either folds or "
        "shoves all-in, and each player behind, in turn, calls or folds. Neither side can do better by changing "
        "strategy. Equities come from a precomputed all-in preflop equity table for every pair of hand classes, "
        "card removal included. For your hands, each spot is solved with that hand's real stacks, blinds, antes "
        "and players left to act; here everyone has the same stack.\n\n"
        "Simplifications:\n"
        "- **Chip EV, no ICM.** Near the bubble or a pay jump, survival is worth more than chips and tighter "
        "play is often right.\n"
        "- **Push or fold only.** Limps and smaller raises aren't modelled; at these depths they are rarely "
        "much better.\n"
        "- **At most one caller.** Once someone calls, everyone behind folds. Equilibrium overcall ranges are "
        "very tight, so this costs little.\n"
        "- **Independent callers.** Card removal is applied between the shover and each caller, not between "
        "callers. Players who fold tend to hold low cards, which leaves more high cards for those behind, so "
        "with many players left to act this overstates the shover's fold equity: early-position push ranges "
        "come out slightly too wide, and the EV of a marginal hand can be off by up to about 0.07 BB.")

# ---- Your charts -----------------------------------------------------------------------
st.subheader("Your charts")
directory = charts.charts_dir()
try:
    shown_dir = os.path.relpath(directory, common.ROOT)
except ValueError:  # different drive on Windows
    shown_dir = str(directory)
if shown_dir.startswith(".."):
    shown_dir = str(directory)
loaded, errors = charts.load_charts()
st.caption(f"Charts folder: `{shown_dir}`" + ("" if directory.is_dir() else " (doesn't exist yet)")
           + ". Set the `POKER_CHARTS` environment variable to use another folder.")
for e in errors:
    st.error(f"Skipped {e}", icon=":material/error:")

if not loaded:
    st.info("No charts loaded. For deeper spots than push/fold, add raise-first-in and facing-an-open ranges "
            "from a solver or training tool, one JSON file per chart. See how to add charts below.",
            icon=":material/info:")
else:
    compared = common.gto_rows()
    c = st.columns(3, gap="large")
    ci = c[0].selectbox("Chart", range(len(loaded)), format_func=lambda i: loaded[i].name, key="chart_pick")
    chart = loaded[ci]
    si = c[1].selectbox("Spot", range(len(chart.spots)), key=f"chart_spot_{ci}",
                        format_func=lambda i: f"{i + 1}. {chart.spots[i].label()}")
    spot = chart.spots[si]
    action = c[2].segmented_control("Action", list(spot.actions) + ["fold"], default=next(iter(spot.actions)),
                                    key=f"chart_action_{ci}_{si}") or next(iter(spot.actions))
    if action == "fold":
        freqs = np.clip(1.0 - np.sum(list(spot.actions.values()), axis=0), 0.0, 1.0)
    else:
        freqs = np.asarray(spot.actions[action])

    left, right = st.columns([3, 2], gap="large")
    with left:
        common.show_grid(freqs, action, f"user_{ci}_{si}_{action}")
    with right:
        st.metric(common.ACTION_LABELS.get(action, action), f"{gto.range_percent(freqs):.1f}% of hands")
        st.caption(common.range_summary(freqs, action))
        if action in spot.ranges:
            st.caption("Range as written in the file")
            st.code(spot.ranges[action], language=None, wrap_lines=True)
        else:
            st.caption("Fold is whatever weight the other actions leave.")
        if chart.source:
            st.caption(f"Source: {chart.source}")
        if chart.path is not None:
            st.caption(f"File: `{chart.path.name}`")
        n = int((compared["source"] == chart.name).sum()) if not compared.empty else 0
        mine = compared[(compared["source"] == chart.name) & (compared["player_id"] == compared["hero_id"])]
        st.caption(f"{len(mine)} of your decisions and {n - len(mine)} shown opponent decisions are compared "
                   "with this chart (Leaks and Opponents pages).")
        if chart.path is not None and chart.path.stat().st_mtime > common.db_version() > 0:
            st.warning("This file changed after the database was last written. Rebuild on the Import page to "
                       "compare your hands with the current version.", icon=":material/sync:")
    common.grid_legend()

with st.expander("How to add charts", expanded=not loaded):
    st.markdown(
        f"1. Create the folder `{shown_dir}` (or point `POKER_CHARTS` at a folder of your choice).\n"
        "2. Save each chart as a `.json` file there: a name, where the ranges came from, and a list of spots "
        "(`rfi`: raise first in; `vs_open`: facing one open raise), each with a weighted range per action, "
        "e.g. `\"raise\": \"77+, ATs+, KQs, AJo:0.5\"`.\n"
        "3. Reload this page to check the file loads, then **Rebuild** on the Import page (Maintenance) to "
        "compare your existing hands with it. New imports use it automatically.\n\n"
        "The full format, how spots are matched and how actions map to the chart are in `docs/gto-charts.md`. "
        "Ranges exported from a paid tool are usually licensed to you: keep the files private.")
    example = common.ROOT / "docs" / "example-chart.json"
    if example.exists():
        st.caption("`docs/example-chart.json`: shows the format only; its ranges are placeholders, not GTO.")
        text = example.read_text(encoding="utf-8")
        st.code(text, language="json", wrap_lines=True, height=360)
        st.download_button("Download the example", text, file_name="example-chart.json", mime="application/json",
                           icon=":material/download:")
