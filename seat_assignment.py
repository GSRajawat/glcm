"""
seat_assignment.py — Room & seat assignment tool.

Ported from the original "Assign Rooms & Seats to Students" admin screen.
Same three seat formats (1 to N / 1A to NA / 1B to NB), same
capacity-warning and seat-conflict behaviour. Reads roll numbers out of
sitting_plan's JSONB roll_numbers arrays instead of wide CSV columns, and
writes to assigned_seats scoped by center_id.
"""

import streamlit as st

import db


def generate_sequential_seats(seat_range_str: str, num_students: int) -> list:
    """Kept for parity with the original — not currently wired into the UI
    below (which uses the capacity+format inputs instead), but useful if
    you want a free-form '1-60' / '1A-60A' range entry later."""
    seat_range_str = seat_range_str.strip().upper()
    generated = []

    if '-' in seat_range_str:
        start_s, end_s = seat_range_str.split('-')
        import re
        if re.match(r'^\d+[A-Z]$', start_s) and re.match(r'^\d+[A-Z]$', end_s):
            start_num = int(re.match(r'^(\d+)', start_s).group(1))
            start_char = re.search(r'([A-Z])$', start_s).group(1)
            end_num = int(re.match(r'^(\d+)', end_s).group(1))
            end_char = re.search(r'([A-Z])$', end_s).group(1)
            if start_char != end_char:
                raise ValueError("Alphabet part must match on both ends of an alphanumeric range.")
            generated = [f"{i}{start_char}" for i in range(start_num, end_num + 1)]
        elif start_s.isdigit() and end_s.isdigit():
            generated = [str(i) for i in range(int(start_s), int(end_s) + 1)]
        else:
            raise ValueError("Invalid seat range format. Use '1-60' or '1A-60A'.")
    elif seat_range_str.isdigit():
        generated = [seat_range_str]
    else:
        raise ValueError("Invalid seat number format.")

    return generated[:num_students]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_scheduled_papers(center_id: str, date_iso: str, shift: str) -> list:
    ok, rows = db.select("timetable", center_id, {"date": date_iso, "shift": shift})
    if not ok:
        st.error(rows)
        return []
    return rows


def _get_roll_numbers_for_paper(center_id: str, paper_code: str) -> list:
    ok, rows = db.select("sitting_plan", center_id, {"paper_code": paper_code})
    if not ok:
        st.error(rows)
        return []
    all_rolls = []
    for row in rows:
        all_rolls.extend(row.get("roll_numbers") or [])
    return sorted(set(all_rolls))


def _get_assigned_for_paper_session(center_id: str, paper_code: str, date_iso: str, shift: str) -> list:
    ok, rows = db.select(
        "assigned_seats", center_id,
        {"paper_code": paper_code, "date": date_iso, "shift": shift},
    )
    if not ok:
        st.error(rows)
        return []
    return rows


def _get_room_assigned_seats(center_id: str, room: str, date_iso: str, shift: str) -> list:
    ok, rows = db.select(
        "assigned_seats", center_id,
        {"room_number": room, "date": date_iso, "shift": shift},
    )
    if not ok:
        st.error(rows)
        return []
    return rows


def get_session_paper_summary(center_id: str, date_iso: str, shift: str) -> list:
    """[{paper_code, paper_name, class, total_students, assigned, unassigned}, ...]"""
    papers = _get_scheduled_papers(center_id, date_iso, shift)
    summary = []
    for p in papers:
        total_rolls = _get_roll_numbers_for_paper(center_id, p["paper_code"])
        assigned_rows = _get_assigned_for_paper_session(center_id, p["paper_code"], date_iso, shift)
        summary.append({
            "Paper Code": p["paper_code"],
            "Paper Name": p.get("paper_name"),
            "Class": p.get("class"),
            "Total Students": len(total_rolls),
            "Assigned": len(assigned_rows),
            "Unassigned": max(0, len(total_rolls) - len(assigned_rows)),
        })
    return summary


# ---------------------------------------------------------------------------
# Assignment logic
# ---------------------------------------------------------------------------

def assign_seats(center_id: str, date_iso: str, shift: str, paper_code: str,
                  paper_name: str, room: str, seat_format: str,
                  total_capacity: int, capacity_per_format: int) -> tuple[bool, str, list]:
    """
    Returns (success, message, newly_assigned_rows).
    """
    all_rolls = _get_roll_numbers_for_paper(center_id, paper_code)
    if not all_rolls:
        return False, f"No students found in sitting plan for paper {paper_code}.", []

    already_assigned = _get_assigned_for_paper_session(center_id, paper_code, date_iso, shift)
    already_assigned_rolls = {r["roll_number"] for r in already_assigned}

    unassigned_rolls = [r for r in all_rolls if r not in already_assigned_rolls]
    if not unassigned_rolls:
        return False, "All students for this paper are already assigned for this date/shift.", []

    if seat_format == "1 to N":
        suffix = ""
        format_capacity = total_capacity
    elif seat_format == "1A to NA":
        suffix = "A"
        format_capacity = capacity_per_format
    elif seat_format == "1B to NB":
        suffix = "B"
        format_capacity = capacity_per_format
    else:
        return False, f"Unknown seat format: {seat_format}", []

    room_seats = _get_room_assigned_seats(center_id, room, date_iso, shift)
    occupied_seat_numbers = {r["seat_number"] for r in room_seats}

    available_seat_numbers = [
        i for i in range(1, format_capacity + 1)
        if f"{i}{suffix}" not in occupied_seat_numbers
    ]

    if not available_seat_numbers:
        return False, (
            f"No seats available in {seat_format} format for Room {room}. "
            "Try a different format or room."
        ), []

    warning = ""
    if len(available_seat_numbers) < len(unassigned_rolls):
        warning = (
            f"Only {len(available_seat_numbers)} seats available in {seat_format} "
            f"format, but {len(unassigned_rolls)} students need assignment. "
            f"Assigning the first {len(available_seat_numbers)}; the rest need "
            "another room or format."
        )

    seats_to_assign_count = min(len(available_seat_numbers), len(unassigned_rolls))
    students_to_assign = unassigned_rolls[:seats_to_assign_count]

    new_rows = []
    for i, roll in enumerate(students_to_assign):
        seat_num_str = f"{available_seat_numbers[i]}{suffix}"
        new_rows.append({
            "roll_number": roll,
            "paper_code": paper_code,
            "paper_name": paper_name,
            "room_number": room,
            "seat_number": seat_num_str,
            "date": date_iso,
            "shift": shift,
        })

    ok, result = db.upsert(
        "assigned_seats", center_id, new_rows,
        on_conflict="center_id,roll_number,date,shift,paper_code",
    )
    if not ok:
        return False, result, []

    message = f"Assigned {len(new_rows)} students to Room {room} using {seat_format} format."
    if warning:
        message += " " + warning
    return True, message, new_rows


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def render(center_id: str):
    st.subheader("📘 Room & Seat Assignment Tool")

    ok, timetable_rows = db.select("timetable", center_id)
    if not ok:
        st.error(timetable_rows)
        return

    scheduled = [r for r in timetable_rows if r.get("date") and r.get("shift")]
    if not scheduled:
        st.warning(
            "No papers have a date/shift assigned yet. Use Admin \u2192 "
            "Update Timetable Details first."
        )
        return

    date_options = sorted({r["date"] for r in scheduled})
    date_iso = st.selectbox("Select Exam date", date_options, key="assign_date_select")

    shift_options = sorted({r["shift"] for r in scheduled if r["date"] == date_iso})
    shift = st.selectbox("Select shift", shift_options, key="assign_shift_select")

    st.markdown("---")
    st.subheader("Session Student Summary (Assigned vs. Unassigned)")
    summary = get_session_paper_summary(center_id, date_iso, shift)
    if summary:
        st.dataframe(summary, use_container_width=True)
    else:
        st.info("No student data found for the selected date and shift.")
    st.markdown("---")

    papers_for_session = [r for r in scheduled if r["date"] == date_iso and r["shift"] == shift]
    paper_display = [f"{p['paper_code']} - {p.get('paper_name') or ''}" for p in papers_for_session]
    selected = st.selectbox("Select Paper Code and Name", paper_display, key="assign_paper_select")
    if not selected:
        return

    selected_paper = papers_for_session[paper_display.index(selected)]
    paper_code = selected_paper["paper_code"]
    paper_name = selected_paper.get("paper_name") or ""

    st.subheader("Room & Seat Configuration")
    room = st.text_input("Enter Room Number (e.g., 1, G230)", key="room_input").strip()

    col1, col2 = st.columns(2)
    with col1:
        total_capacity = st.number_input(
            "Total Room Capacity (for '1 to N' format)",
            min_value=1, max_value=2000, value=60, key="total_capacity_input",
        )
    with col2:
        capacity_per_format = st.number_input(
            "Capacity per Format (for 'A/B' formats)",
            min_value=1, max_value=100, value=30, key="capacity_per_format_input",
        )

    seat_format = st.radio(
        "Select Seat Assignment Format:",
        ["1 to N", "1A to NA", "1B to NB"], key="seat_format_radio",
    )

    if room:
        room_seats = _get_room_assigned_seats(center_id, room, date_iso, shift)
        seat_numbers = [r["seat_number"] for r in room_seats]
        a_used = len([s for s in seat_numbers if s.endswith("A")])
        b_used = len([s for s in seat_numbers if s.endswith("B")])
        plain_used = len([s for s in seat_numbers if not s.endswith("A") and not s.endswith("B")])

        st.subheader("📊 Current Room Status")
        if seat_format in ["1A to NA", "1B to NB"]:
            st.info(f"A-format: **{capacity_per_format - a_used}** remaining ({a_used}/{capacity_per_format} used)")
            st.info(f"B-format: **{capacity_per_format - b_used}** remaining ({b_used}/{capacity_per_format} used)")
        else:
            st.info(f"Total: **{total_capacity - plain_used}** seats remaining ({plain_used}/{total_capacity} used)")

    st.markdown("---")

    if st.button("✅ Assign Seats", key="assign_button"):
        if not room:
            st.error("Please enter a Room Number before assigning seats.")
            return

        success, message, new_rows = assign_seats(
            center_id, date_iso, shift, paper_code, paper_name,
            room, seat_format, total_capacity, capacity_per_format,
        )

        if success:
            st.success(f"✅ {message}")
            st.dataframe(new_rows, use_container_width=True)
        else:
            st.error(message) if "already assigned" not in message else st.warning(message)
