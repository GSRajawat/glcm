"""
cs_panel.py — Centre Superintendent panel.

Covers: Report Exam Session (absent/UFM reporting, with the same three
validation rules as the original), Manage Exam Team & Shift Assignments,
Generate UFM Print Form, and View Full Timetable (delegates to
student_portal). Room Chart Report lives in reporting.py instead, since
it's shared with the Admin side.
"""

import datetime
import io
import os
import re

import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from pypdf import PdfReader, PdfWriter

import db
import student_portal
import reporting


SHIFTS = ["Morning", "Evening"]

MAX_SELECTIONS = {
    "senior_center_superintendent": 1,
    "center_superintendent": 1,
    "assistant_center_superintendent": 3,
    "permanent_invigilator": 3,
    "assistant_permanent_invigilator": 5,
    "class_3_worker": 10,
    "class_4_worker": 10,
}
ROLE_LABELS = {
    "senior_center_superintendent": "Senior Center Superintendent (Max 1)",
    "center_superintendent": "Center Superintendent (Max 1)",
    "assistant_center_superintendent": "Assistant Center Superintendent (Max 3)",
    "permanent_invigilator": "Permanent Invigilator (Max 3)",
    "assistant_permanent_invigilator": "Assistant Permanent Invigilator (Max 5)",
    "class_3_worker": "Class 3 Worker (Max 10)",
    "class_4_worker": "Class 4 Worker (Max 10)",
}


# ---------------------------------------------------------------------------
# Report Exam Session
# ---------------------------------------------------------------------------

def render_report_exam_session(center_id: str):
    st.subheader("📝 Report Exam Session")

    report_date = st.date_input("Select date", value=datetime.date.today(), key="cs_report_date")
    report_shift = st.selectbox("Select shift", SHIFTS, key="cs_report_shift")
    date_iso = report_date.isoformat()

    ok, seats = db.select("assigned_seats", center_id, {"date": date_iso, "shift": report_shift})
    if not ok:
        st.error(seats)
        return
    if not seats:
        st.warning("No assigned seats found for the selected date and shift. Assign seats via the Admin Panel first.")
        return

    sessions = {}
    for s in seats:
        key = (s["room_number"], s["paper_code"], s["paper_name"])
        sessions.setdefault(key, []).append(s["roll_number"])

    session_labels = sorted(
        [f"{room} - {code} ({name})" for (room, code, name) in sessions.keys()]
    )
    selected_label = st.selectbox(
        "Select Exam Session (Room - Paper Code (Paper Name))",
        [""] + session_labels, key="cs_exam_session_select",
    )
    if not selected_label:
        return

    room_num, rest = selected_label.split(" - ", 1)
    paper_code, paper_name = rest.rstrip(")").split(" (", 1)
    expected_students = sorted(set(sessions[(room_num, paper_code, paper_name)]))

    ok, tt_rows = db.select("timetable", center_id, {"paper_code": paper_code, "date": date_iso, "shift": report_shift})
    selected_class = tt_rows[0].get("class", "") if ok and tt_rows else ""

    report_key = f"{report_date.strftime('%Y%m%d')}_{report_shift.lower()}_{room_num}_{paper_code}"

    ok, existing = db.select("cs_reports", center_id, {"report_key": report_key})
    loaded_report = existing[0] if ok and existing else {}
    if loaded_report:
        st.info("Existing report loaded.")
    else:
        st.info("No existing report found for this session. Starting new.")

    st.write(f"**Reporting for:** Room {room_num}, Paper: {paper_name} ({paper_code})")

    absent_selected = st.multiselect(
        "Absent Roll Numbers", options=expected_students,
        default=loaded_report.get("absent_roll_numbers", []),
        key="absent_roll_numbers_multiselect",
    )
    ufm_selected = st.multiselect(
        "UFM (Unfair Means) Roll Numbers", options=expected_students,
        default=loaded_report.get("ufm_roll_numbers", []),
        key="ufm_roll_numbers_multiselect",
    )

    if st.button("Save Report", key="save_cs_report"):
        expected_set, absent_set, ufm_set = set(expected_students), set(absent_selected), set(ufm_selected)
        errors = []
        if not absent_set.issubset(expected_set):
            errors.append(f"Absent roll numbers {list(absent_set - expected_set)} are not in the expected list.")
        if not ufm_set.issubset(expected_set):
            errors.append(f"UFM roll numbers {list(ufm_set - expected_set)} are not in the expected list.")
        if not absent_set.isdisjoint(ufm_set):
            errors.append(f"Roll numbers {list(absent_set & ufm_set)} are marked both Absent and UFM.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            report_data = {
                "report_key": report_key,
                "date": date_iso,
                "shift": report_shift,
                "room_num": room_num,
                "paper_code": paper_code,
                "paper_name": paper_name,
                "class": selected_class,
                "absent_roll_numbers": absent_selected,
                "ufm_roll_numbers": ufm_selected,
            }
            ok, result = db.upsert("cs_reports", center_id, report_data, on_conflict="center_id,report_key")
            (st.success("Report saved.") if ok else st.error(result))
            if ok:
                st.rerun()

    st.markdown("---")
    st.subheader("All Saved Reports")
    ok, all_reports = db.select("cs_reports", center_id, order="date")
    if ok and all_reports:
        ok2, room_invs = db.select("room_invigilator_assignments", center_id)
        inv_lookup = {}
        if ok2:
            for ri in room_invs:
                inv_lookup[(ri["date"], ri["shift"], ri["room_num"])] = ri.get("invigilators", [])

        display_rows = []
        for r in all_reports:
            display_rows.append({
                "date": r["date"], "shift": r["shift"], "Room": r["room_num"],
                "Paper Code": r["paper_code"], "Paper Name": r["paper_name"], "Class": r.get("class"),
                "Invigilators": inv_lookup.get((r["date"], r["shift"], r["room_num"]), []),
                "Absent Roll Numbers": r.get("absent_roll_numbers", []),
                "UFM Roll Numbers": r.get("ufm_roll_numbers", []),
                "Report Key": r["report_key"],
            })
        st.dataframe(display_rows, use_container_width=True)
    else:
        st.info("No reports saved yet.")


# ---------------------------------------------------------------------------
# Manage Exam Team & Shift Assignments
# ---------------------------------------------------------------------------

def render_manage_team(center_id: str):
    st.subheader("👥 Manage Exam Team Members")

    ok, members_rows = db.select("exam_team_members", center_id, order="name")
    if not ok:
        st.error(members_rows)
        return
    current_members = [m["name"] for m in members_rows]

    new_member_name = st.text_input("Add New Team Member Name")
    if st.button("Add Member"):
        if new_member_name and new_member_name not in current_members:
            ok, result = db.insert("exam_team_members", center_id, {"name": new_member_name})
            (st.success("Member added.") if ok else st.error(result))
            if ok:
                st.rerun()
        elif new_member_name:
            st.warning("Member already exists.")
        else:
            st.warning("Please enter a name.")

    if current_members:
        st.write("Current Team Members:")
        st.write(current_members)

        member_to_remove = st.selectbox("Select Member to Remove", [""] + current_members)
        if st.button("Remove Selected Member"):
            if member_to_remove:
                ok, result = db.delete("exam_team_members", center_id, {"name": member_to_remove})
                (st.success("Member removed.") if ok else st.error(result))
                if ok:
                    st.rerun()
            else:
                st.warning("Please select a member to remove.")
    else:
        st.info("No team members added yet.")

    st.markdown("---")
    st.subheader("🗓️ Assign Roles for Exam Shift")

    assignment_date = st.date_input("Select date for Assignment", value=datetime.date.today(), key="assignment_date")
    assignment_shift = st.selectbox("Select shift for Assignment", SHIFTS, key="assignment_shift")
    date_iso = assignment_date.isoformat()

    if not current_members:
        st.warning("Please add exam team members first.")
        return

    ok, existing = db.select("shift_assignments", center_id, {"date": date_iso, "shift": assignment_shift})
    loaded = existing[0] if ok and existing else {}

    selected = {}
    for role_key, label in ROLE_LABELS.items():
        selected[role_key] = st.multiselect(
            label, current_members, default=loaded.get(role_key, []),
            max_selections=MAX_SELECTIONS[role_key], key=f"role_{role_key}",
        )

    if st.button("Save Shift Assignments"):
        all_selected = [name for names in selected.values() for name in names]
        if len(all_selected) != len(set(all_selected)):
            st.error("A team member cannot be assigned to multiple roles for the same shift.")
        else:
            data = {"date": date_iso, "shift": assignment_shift, **selected}
            ok, result = db.upsert("shift_assignments", center_id, data, on_conflict="center_id,date,shift")
            (st.success("Shift assignments saved.") if ok else st.error(result))
            if ok:
                st.rerun()

    st.markdown("---")
    st.subheader("Current Shift Assignments")
    ok, all_shift_assignments = db.select("shift_assignments", center_id, order="date")
    if ok and all_shift_assignments:
        st.dataframe(all_shift_assignments, use_container_width=True)
    else:
        st.info("No shift assignments saved yet.")

    render_room_invigilator_assignment(center_id, current_members)


def render_room_invigilator_assignment(center_id: str, current_members: list):
    st.markdown("---")
    st.subheader("🚪 Assign Invigilators to Rooms")

    room_inv_date = st.date_input("Select date for Room Invigilators", value=datetime.date.today(), key="room_inv_date")
    room_inv_shift = st.selectbox("Select shift for Room Invigilators", SHIFTS, key="room_inv_shift")
    date_iso = room_inv_date.isoformat()

    ok, relevant_seats = db.select("assigned_seats", center_id, {"date": date_iso, "shift": room_inv_shift})
    if not ok:
        st.error(relevant_seats)
        return
    if not relevant_seats:
        st.info("No rooms with assigned seats for this date/shift yet. Assign seats via the Admin Panel first.")
        return

    unique_rooms = sorted({s["room_number"] for s in relevant_seats if s.get("room_number")})
    selected_room = st.selectbox("Select Room to Assign Invigilators", [""] + unique_rooms, key="selected_room_for_inv")

    if selected_room:
        if not current_members:
            st.warning("Please add exam team members first.")
        else:
            ok, existing = db.select("room_invigilator_assignments", center_id, {
                "date": date_iso, "shift": room_inv_shift, "room_num": selected_room,
            })
            loaded_invigilators = existing[0].get("invigilators", []) if ok and existing else []

            invigilators_for_room = st.multiselect(
                "Invigilators for this Room", options=current_members,
                default=loaded_invigilators, key="invigilators_for_room_multiselect",
            )

            if st.button("Save Room Invigilators"):
                data = {"date": date_iso, "shift": room_inv_shift, "room_num": selected_room, "invigilators": invigilators_for_room}
                ok, result = db.upsert(
                    "room_invigilator_assignments", center_id, data,
                    on_conflict="center_id,date,shift,room_num",
                )
                (st.success("Room invigilators saved.") if ok else st.error(result))
                if ok:
                    st.rerun()
    else:
        st.info("Select a date, shift, and room to assign invigilators.")

    st.markdown("---")
    st.subheader("Current Room Invigilator Assignments")
    ok, all_room_inv = db.select("room_invigilator_assignments", center_id, order="date")
    if ok and all_room_inv:
        st.dataframe(all_room_inv, use_container_width=True)
    else:
        st.info("No room invigilator assignments saved yet.")


# ---------------------------------------------------------------------------
# Generate UFM Print Form
#
# Produces an actual PDF using the official Jiwaji University UFM form
# (assets/UFM_Form.pdf) as a template — the original Hindi text (set in
# the legacy KrutiDev font) is left completely untouched, and only field
# values are overlaid on top in English: the exam-name banner (class +
# session month/year — from the student's own attestation session data,
# NOT the actual date the exam is being conducted), Roll Number, Name
# (with address), Father's Name, Institution, Center Name, Subject, Day,
# and Date. The "Time" field on the Subject line is deliberately left
# blank — that's the exact time the UFM was caught, filled by hand — and
# the itemized confiscated-materials section (part 2 of the form, below
# the divider) is untouched for the same reason.
# ---------------------------------------------------------------------------

_UFM_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "UFM_Form.pdf")

_MONTH_ABBR = {
    "JAN": "January", "FEB": "February", "MAR": "March", "APR": "April",
    "MAY": "May", "JUN": "June", "JUL": "July", "AUG": "August",
    "SEP": "September", "OCT": "October", "NOV": "November", "DEC": "December",
}


def _fit_text(c, text, x, y, max_width, font="Helvetica", base_size=10, min_size=5):
    """Draws text at (x, y), shrinking the font until it fits max_width."""
    text = text or ""
    size = base_size
    while pdfmetrics.stringWidth(text, font, size) > max_width and size > min_size:
        size -= 0.5
    c.setFont(font, size)
    c.drawString(x, y, text)


def _parse_session(session_str: str) -> str:
    """'DEC-2025' -> 'December - 2025'. Falls back to the raw string."""
    if not session_str:
        return ""
    m = re.match(r"([A-Za-z]{3,})[-\s]*(\d{4})", session_str.strip())
    if not m:
        return session_str
    month = _MONTH_ABBR.get(m.group(1)[:3].upper(), m.group(1))
    return f"{month} - {m.group(2)}"


def generate_ufm_print_form_pdf(center_id: str, ufm_roll: str, report: dict) -> bytes:
    ok, tt = db.select("timetable", center_id, {
        "paper_code": report["paper_code"], "date": report["date"], "shift": report["shift"],
    })
    exam_time = (tt[0].get("time_slot") if ok and tt else None) or ""

    ok_c, center_info = db.get_center_info(center_id)
    center_name = center_info.get("center_name", "") if ok_c else ""

    ok_a, attestation = db.select("attestation_data", center_id, {"roll_number": ufm_roll})
    student = (attestation[0].get("raw_row") or {}) if ok_a and attestation else {}

    try:
        day_name = datetime.date.fromisoformat(report["date"]).strftime("%A")
    except (ValueError, TypeError):
        day_name = ""

    subject_display = f"{report.get('paper_name') or ''} ({report['paper_code']})"
    class_text = report.get("class") or ""
    session_text = _parse_session(student.get("session"))
    name_with_address = ", ".join(p for p in [student.get("name"), student.get("address")] if p)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    _fit_text(c, class_text, 190, 633.61, 95, base_size=13)             # banner left: class (before परीक्षा)
    _fit_text(c, session_text, 320, 633.61, 100, base_size=13)          # banner right: session month - year (after परीक्षा)
    _fit_text(c, ufm_roll, 140, 596.17, 98, base_size=11)               # परीक्षार्थी का अनुक्रमांक
    _fit_text(c, name_with_address, 170, 570.49, 388, base_size=11)     # परीक्षार्थी का नाम (पता सहित)
    _fit_text(c, student.get("fathers_name", ""), 152, 544.82, 405, base_size=11)  # परीक्षार्थी के पिता का नाम
    _fit_text(c, student.get("college_name", ""), 236, 519.15, 315, base_size=11)  # संस्था का नाम
    _fit_text(c, center_name, 100, 493.47, 458, base_size=11)           # केन्द्र का नाम
    _fit_text(c, subject_display, 65, 442, 180, base_size=11)           # विषय
    # समय (subject line) — left blank: exact time of UFM, filled by hand
    _fit_text(c, day_name, 60, 416.45, 187, base_size=11)               # दिन
    _fit_text(c, report["date"], 292, 416.45, 103, base_size=11)        # दिनांक
    _fit_text(c, exam_time, 433, 416.45, 127, base_size=11)             # समय (date line) — exam time slot
    c.save()
    buf.seek(0)

    overlay_reader = PdfReader(buf)
    base_reader = PdfReader(_UFM_TEMPLATE_PATH)
    writer = PdfWriter()
    page = base_reader.pages[0]
    page.merge_page(overlay_reader.pages[0])
    writer.add_page(page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


def render_ufm_print_form(center_id: str):
    st.subheader("🖨️ Generate UFM Print Form")
    st.info("Select a session date and shift to view reported UFM cases and generate their print forms.")

    ok, reports = db.select("cs_reports", center_id)
    if not ok:
        st.error(reports)
        return

    reports_with_ufm = [r for r in reports if r.get("ufm_roll_numbers")]
    if not reports_with_ufm:
        st.info("No UFM cases have been reported yet in any session.")
        return

    dates = sorted({r["date"] for r in reports_with_ufm})
    sel_date = st.selectbox("Select date of UFM Report", dates, key="ufm_report_date_select")

    shifts = sorted({r["shift"] for r in reports_with_ufm if r["date"] == sel_date})
    sel_shift = st.selectbox("Select shift of UFM Report", shifts, key="ufm_report_shift_select")

    session_reports = [r for r in reports_with_ufm if r["date"] == sel_date and r["shift"] == sel_shift]
    roll_to_report = {}
    for r in session_reports:
        for roll in r["ufm_roll_numbers"]:
            roll_to_report[roll] = r

    if not roll_to_report:
        st.info("No UFM cases found for the selected date and shift.")
        return

    selected_rolls = st.multiselect(
        "Select UFM Roll Number(s) to Generate Form",
        sorted(roll_to_report.keys()), key="ufm_roll_select",
    )

    if st.button("Generate UFM Form(s)"):
        if not selected_rolls:
            st.warning("Please select at least one UFM roll number.")
            return
        for roll in selected_rolls:
            pdf_bytes = generate_ufm_print_form_pdf(center_id, roll, roll_to_report[roll])
            st.write(f"**UFM Form for Roll Number: {roll}** (student details filled from attestation data — only the exact incident time is left blank for hand entry)")
            st.download_button(
                f"Download PDF for {roll}", pdf_bytes,
                file_name=f"ufm_form_{roll}_{sel_date}.pdf", mime="application/pdf",
                key=f"ufm_download_{roll}",
            )


# ---------------------------------------------------------------------------
# Panel entrypoint
# ---------------------------------------------------------------------------

def render(center_id: str):
    st.header("🎓 Centre Superintendent Panel")

    cs_panel_option = st.radio("Select CS Task:", [
        "Report Exam Session",
        "Manage Exam Team & Shift Assignments",
        "View Full Timetable",
        "Reports",
        "Generate UFM Print Form",
    ])
    st.markdown("---")

    if cs_panel_option == "Report Exam Session":
        render_report_exam_session(center_id)
    elif cs_panel_option == "Manage Exam Team & Shift Assignments":
        render_manage_team(center_id)
    elif cs_panel_option == "View Full Timetable":
        timetable = student_portal.get_full_timetable(center_id)
        st.dataframe(timetable, use_container_width=True) if timetable else st.warning("Timetable is empty.")
    elif cs_panel_option == "Reports":
        reporting.render(center_id)
    elif cs_panel_option == "Generate UFM Print Form":
        render_ufm_print_form(center_id)