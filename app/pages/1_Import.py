import json
from pathlib import Path

import pandas as pd
import streamlit as st

import common
from pokeranalyzer import db

common.page_setup("Import", ":material/upload:")
common.env_note()
conn = common.get_conn()

st.write("Drop in one or more PokerNow hand-history exports (the JSON download). Hands already in the "
         "database are skipped, so re-importing a file never creates duplicates.")

with st.form("import", clear_on_submit=True):
    files = st.file_uploader("PokerNow export (.json)", type=["json"], accept_multiple_files=True)
    st.caption("Optional tournament results. They aren't in the export; you can also fill them in later below.")
    c = st.columns(4)
    place = c[0].number_input("Finishing place", min_value=0, step=1, value=0, help="0 = leave empty")
    field = c[1].number_input("Field size", min_value=0, step=1, value=0, help="0 = leave empty")
    buy_in = c[2].number_input("Buy-in", min_value=0.0, step=1.0, value=None, placeholder="e.g. 0 for a freeroll")
    prize = c[3].number_input("Prize", min_value=0.0, step=1.0, value=None)
    submitted = st.form_submit_button("Import", type="primary")

if submitted and files:
    common.RAW_DIR.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in dict(finishing_place=place or None, field_size=field or None,
                                  buy_in=buy_in, prize=prize).items() if v is not None}
    for f in files:
        raw = f.getvalue()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            st.error(f"{f.name}: not valid JSON ({e})")
            continue
        (common.RAW_DIR / Path(f.name).name).write_bytes(raw)
        with st.spinner(f"Importing {f.name} (equity for all-in hands takes a few seconds)…"):
            try:
                result = db.import_export(conn, data, source_file=f.name, meta=meta)
            except Exception as e:  # show parse problems instead of a stack trace
                st.error(f"{f.name}: could not import ({e})")
                continue
        st.success(f"**{f.name}** → session `{result.session}`: "
                   f"**{result.added} hands added, {result.duplicates} duplicates** skipped.")
        if result.problems:
            with st.expander(f"{len(result.problems)} consistency warning(s)"):
                st.write("\n".join(f"- {p}" for p in result.problems))
    common.clear_caches()

st.subheader("Sessions")
s = common.sessions()
if s.empty:
    st.caption("Nothing imported yet.")
else:
    st.caption("Edit finishing place, field size, buy-in, prize and notes, then save.")
    view = s[["slug", "hero_name", "first_hand_at", "hands", "finishing_place", "field_size", "buy_in", "prize",
              "notes"]].copy()
    view["first_hand_at"] = common.to_datetime(view["first_hand_at"]).dt.strftime("%Y-%m-%d %H:%M")
    for col in ("finishing_place", "field_size"):
        view[col] = view[col].astype("Int64")
    for col in ("buy_in", "prize"):
        view[col] = view[col].astype("Float64")
    view["notes"] = view["notes"].astype("string")
    edited = st.data_editor(
        view, hide_index=True, width="stretch", key="sessions_editor",
        disabled=["slug", "hero_name", "first_hand_at", "hands"],
        column_config={
            "slug": "Session", "hero_name": "You", "first_hand_at": "Started (UTC)", "hands": "Hands",
            "finishing_place": st.column_config.NumberColumn("Place", min_value=1, step=1),
            "field_size": st.column_config.NumberColumn("Field", min_value=1, step=1),
            "buy_in": st.column_config.NumberColumn("Buy-in", min_value=0.0, format="%.2f"),
            "prize": st.column_config.NumberColumn("Prize", min_value=0.0, format="%.2f"),
            "notes": st.column_config.TextColumn("Notes"),
        })
    if st.button("Save changes"):
        for _, row in edited.iterrows():
            fields = {}
            for col in db.SESSION_FIELDS:
                v = row[col]
                fields[col] = None if pd.isna(v) else (int(v) if col in ("finishing_place", "field_size")
                                                      else float(v) if col in ("buy_in", "prize") else str(v))
            db.update_session(conn, row["slug"], **fields)
        common.clear_caches()
        st.success("Saved.")
        st.rerun()

    with st.expander("Maintenance"):
        st.caption("Rebuild re-derives positions, stats and leak flags from the stored raw hands "
                   "(useful after updating the analyzer).")
        c = st.columns(2)
        recompute = c[0].checkbox("Also recompute all-in equity (slower)")
        if c[0].button("Rebuild derived tables"):
            with st.spinner("Rebuilding…"):
                n = db.rebuild(conn, recompute_equity=recompute)
            common.clear_caches()
            st.success(f"Rebuilt {n} hands.")
        victim = c[1].selectbox("Delete a session", [""] + s["slug"].tolist(), format_func=lambda x: x or "—")
        if victim and c[1].button(f"Delete {victim}", type="secondary"):
            db.delete_session(conn, victim)
            common.clear_caches()
            st.rerun()
