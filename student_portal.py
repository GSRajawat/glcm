"""
student_portal.py — Public student-facing search. No login.

Three options, matching the original Student View:
  1. Search by Roll Number + date  -> uses assigned_seats (post seat-assignment)
  2. Get Full Exam Schedule by Roll Number -> uses sitting_plan (works even
     before seats are assigned, since sitting_plan just needs the roll
     number to appear in a paper's roll list)
  3. View Full Timetable
"""

import datetime
import json

import streamlit as st

import db


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def search_by_roll_and_date(center_id: str, roll_number: str, date_iso: str) -> list:
    ok, seats = db.select("assigned_seats", center_id, {"roll_number": roll_number, "date": date_iso})
    if not ok:
        st.error(seats)
        return []

    results = []
    for seat in seats:
        ok, tt_rows = db.select(
            "timetable", center_id,
            {"paper_code": seat["paper_code"], "date": date_iso, "shift": seat["shift"]},
        )
        tt = tt_rows[0] if ok and tt_rows else {}

        results.append({
            "Room Number": seat.get("room_number"),
            "Seat Number": seat.get("seat_number"),
            "Class": tt.get("class", "N/A"),
            "Paper": tt.get("paper_short", ""),
            "Paper Code": seat.get("paper_code"),
            "Paper Name": seat.get("paper_name"),
            "date": seat.get("date"),
            "shift": seat.get("shift"),
            "Mode": tt.get("mode", "REGULAR"),
            "Type": tt.get("type", "REGULAR"),
        })
    return results


def get_full_schedule(center_id: str, roll_number: str) -> list:
    client = db.get_client()
    response = (
        client.table("sitting_plan")
        .select("*")
        .eq("center_id", center_id)
        .contains("roll_numbers", json.dumps([roll_number]))
        .execute()
    )
    sitting_rows = response.data or []

    schedule = []
    seen = set()
    for sp in sitting_rows:
        raw = sp.get("raw_row") or {}
        paper_code = sp.get("paper_code")
        class_val = raw.get("class")

        ok, tt_rows = db.select("timetable", center_id, {"paper_code": paper_code, "class": class_val})
        if not ok:
            continue

        for tt in tt_rows:
            key = (tt.get("paper_code"), tt.get("date"), tt.get("shift"))
            if key in seen:
                continue
            seen.add(key)
            schedule.append({
                "date": tt.get("date") or "Not yet scheduled",
                "shift": tt.get("shift") or "-",
                "Class": tt.get("class"),
                "Paper": tt.get("paper_short"),
                "Paper Code": tt.get("paper_code"),
                "Paper Name": tt.get("paper_name"),
            })

    schedule.sort(key=lambda r: (r["date"] == "Not yet scheduled", r["date"]))
    return schedule


def get_full_timetable(center_id: str) -> list:
    ok, rows = db.select("timetable", center_id, order="date")
    if not ok:
        st.error(rows)
        return []
    return rows


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def render(center_id: str):
    st.subheader("🎓 Student Portal")

    option = st.radio("Choose Search Option:", [
        "Search by Roll Number and date",
        "Get Full Exam Schedule by Roll Number",
        "View Full Timetable",
    ])

    if option == "Search by Roll Number and date":
        roll = st.text_input("Enter Roll Number", max_chars=9)
        date_input = st.date_input("Enter Exam date", value=datetime.date.today())

        if st.button("Search"):
            if not roll.strip():
                st.warning("Please enter a roll number.")
                return

            results = search_by_roll_and_date(center_id, roll.strip(), date_input.isoformat())

            if results:
                st.success(f"Found {len(results)} exam(s) for Roll Number {roll} on {date_input.strftime('%d-%m-%Y')}:")
                for result in results:
                    st.markdown("---")
                    st.write(f"**Room Number:** {result['Room Number']}")
                    st.write(f"**🪑 Seat Number:** {result['Seat Number']}")

                    paper_display = f"{result['Paper Name']} ({result['Paper Code']})"
                    if result["Paper"]:
                        paper_display = f"{result['Paper']} - {paper_display}"
                    st.write(f"**📚 Paper:** {paper_display}")

                    st.write(f"**🎓 Student type:** {result['Mode']} - {result['Type']}")
                    st.write(f"**🕐 shift:** {result['shift']}, **📅 date:** {result['date']}")
            else:
                st.warning("No data found. Check that the Roll Number and date are correct and seats have been assigned.")

    elif option == "Get Full Exam Schedule by Roll Number":
        roll = st.text_input("Enter Roll Number", key="schedule_roll_input")
        if st.button("Get Schedule"):
            if not roll.strip():
                st.warning("Please enter a roll number.")
                return

            schedule = get_full_schedule(center_id, roll.strip())
            if schedule:
                st.dataframe(schedule, use_container_width=True)
            else:
                st.warning("No exam records found for this roll number.")

    elif option == "View Full Timetable":
        st.subheader("Full Examination Timetable")
        timetable = get_full_timetable(center_id)
        if timetable:
            st.dataframe(timetable, use_container_width=True)
        else:
            st.warning("Timetable data is missing. Please check back once it's uploaded.")
