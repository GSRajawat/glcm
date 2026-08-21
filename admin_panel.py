"""
admin_panel.py — Admin-facing screens.

Currently wires in:
  - Upload Data Files (wraps data_ingestion.render_upload_ui)
  - Update Timetable Details (assigns date/shift/time to papers staged
    by PDF ingestion, matching the original app's two-phase workflow)
  - Assign Rooms & Seats to Students (wraps seat_assignment.render)
  - Reports (wraps reporting.render — Room Occupancy / Room Chart / Statistics)
  - Remuneration Bill Generation (wraps remuneration.render)
"""

import datetime

import streamlit as st

import data_ingestion
import seat_assignment
import auto_seat_planner
import reporting
import remuneration
import db


SHIFTS = ["Morning", "Evening"]


# ---------------------------------------------------------------------------
# Update Timetable Details
# ---------------------------------------------------------------------------

def _load_timetable(center_id: str):
    ok, data = db.select("timetable", center_id, order="paper_code")
    if not ok:
        st.error(data)
        return []
    return data


def render_update_timetable(center_id: str):
    st.subheader("✏️ Update Timetable Details")

    rows = _load_timetable(center_id)
    if not rows:
        st.info("No timetable entries yet. Upload sitting plan PDFs first, in 'Upload Data Files'.")
        return

    unscheduled = [r for r in rows if not r.get("date") or not r.get("shift")]
    scheduled = [r for r in rows if r.get("date") and r.get("shift")]

    st.write(f"**{len(unscheduled)} paper(s) awaiting a date/shift**, {len(scheduled)} already scheduled.")

    with st.expander("View all timetable entries"):
        st.dataframe(rows, use_container_width=True)

    if not unscheduled:
        st.success("Every paper has a date and shift assigned.")
        return

    st.markdown("---")
    st.write("Select which unscheduled paper(s) to assign a date/shift to:")

    labels = [
        f"{r['paper_code']} — {r.get('paper_name') or r.get('paper_short') or ''} ({r.get('class') or ''})"
        for r in unscheduled
    ]
    selected_idx = st.multiselect(
        "Papers", range(len(unscheduled)), format_func=lambda i: labels[i],
        key="tt_update_selected",
    )

    if not selected_idx:
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        new_date = st.date_input("Date", value=datetime.date.today(), key="tt_update_date")
    with col2:
        new_shift = st.selectbox("Shift", SHIFTS, key="tt_update_shift")
    with col3:
        new_time = st.text_input(
            "Time", value="09:00 AM - 12:00 NOON", key="tt_update_time"
        )

    if st.button("Apply to Selected Papers"):
        success_count = 0
        for i in selected_idx:
            row = unscheduled[i]
            ok, result = db.update(
                "timetable", center_id,
                match={"id": row["id"]},
                data={
                    "date": new_date.isoformat(),
                    "shift": new_shift,
                    "time_slot": new_time,
                },
            )
            if ok:
                success_count += 1
            else:
                st.error(f"Failed to update {row['paper_code']}: {result}")

        if success_count:
            st.success(f"Scheduled {success_count} paper(s).")
            st.rerun()


# ---------------------------------------------------------------------------
# Panel entrypoint
# ---------------------------------------------------------------------------

def render(center_id: str):
    st.header("⚙️ Admin Panel")

    admin_option = st.radio(
        "Select Admin Task:",
        ["Upload Data Files", "Update Timetable Details", "Assign Rooms & Seats to Students",
         "Auto-Propose Seating", "Reports", "Remuneration Bill Generation"],
        key="admin_task_radio",
    )

    st.markdown("---")

    if admin_option == "Upload Data Files":
        data_ingestion.render_upload_ui(center_id)
    elif admin_option == "Update Timetable Details":
        render_update_timetable(center_id)
    elif admin_option == "Assign Rooms & Seats to Students":
        seat_assignment.render(center_id)
    elif admin_option == "Auto-Propose Seating":
        auto_seat_planner.render(center_id)
    elif admin_option == "Reports":
        reporting.render(center_id)
    elif admin_option == "Remuneration Bill Generation":
        remuneration.render(center_id)
