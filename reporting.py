"""
reporting.py — Room Occupancy Report, Room Chart Report, and Session
Statistics (paper-wise / class-wise attendance). Shared between the Admin
and CS panels — both call render(center_id).
"""

import datetime
import io
import re
from collections import defaultdict

import streamlit as st

import db


SHIFTS = ["Morning", "Evening"]


# ---------------------------------------------------------------------------
# Room Occupancy Report
# ---------------------------------------------------------------------------

def _seat_sort_key(seat: str):
    if isinstance(seat, str):
        m = re.match(r'(\d+)', seat)
        if m:
            return int(m.group(1))
    return 0


def render_room_occupancy(center_id: str):
    st.subheader("📊 Room Occupancy Report")
    st.info("View detailed occupancy for each room based on Assigned Seats.")

    ok, tt_rows = db.select("timetable", center_id)
    if not ok or not tt_rows:
        st.warning("Please upload/schedule the timetable first.")
        return

    dates = sorted({r["date"] for r in tt_rows if r.get("date")})
    if not dates:
        st.warning("No scheduled (date-assigned) papers yet.")
        return

    col1, col2 = st.columns(2)
    with col1:
        sel_date = st.selectbox("Select Date", dates, key="room_report_date")
    with col2:
        sel_shift = st.selectbox("Select Shift", SHIFTS, key="room_report_shift")

    if not st.button("Generate Room Occupancy Report"):
        return

    ok, seats = db.select("assigned_seats", center_id, {"date": sel_date, "shift": sel_shift})
    if not ok:
        st.error(seats)
        return
    if not seats:
        st.warning(f"No students have been assigned seats for {sel_date} ({sel_shift}).")
        return

    room_stats = defaultdict(lambda: {"count": 0, "details": []})
    for s in seats:
        room = s["room_number"]
        room_stats[room]["count"] += 1
        room_stats[room]["details"].append((s["roll_number"], s["seat_number"]))

    rows = []
    for room, stats in room_stats.items():
        stats["details"].sort(key=lambda rs: _seat_sort_key(rs[1]))
        details_display = ", ".join(f"{roll} ({seat})" for roll, seat in stats["details"])
        rows.append({"Room Number": room, "Student Count": stats["count"], "Assigned Student Details": details_display})

    def room_sort(row):
        try:
            return (0, int(row["Room Number"]))
        except (ValueError, TypeError):
            return (1, str(row["Room Number"]))

    rows.sort(key=room_sort)
    st.dataframe(rows, use_container_width=True)

    csv_lines = ["Room Number,Student Count,Assigned Student Details"]
    for r in rows:
        csv_lines.append(f'{r["Room Number"]},{r["Student Count"]},"{r["Assigned Student Details"]}"')
    st.download_button(
        "Download Room Occupancy Report as CSV", "\n".join(csv_lines),
        file_name=f"room_occupancy_{sel_date}_{sel_shift}.csv", mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Room Chart Report (CSV-formatted text export, same layout as original)
# ---------------------------------------------------------------------------

def _room_chart_seat_sort_key(seat: str):
    if isinstance(seat, str):
        m_a = re.match(r'(\d+)A', seat)
        m_b = re.match(r'(\d+)B', seat)
        if m_a:
            return (0, int(m_a.group(1)))
        elif m_b:
            return (1, int(m_b.group(1)))
        elif seat.isdigit():
            return (2, int(seat))
    return (3, seat)


def generate_room_chart_report(center_id: str, date_str: str, shift: str) -> str:
    ok, tt_rows = db.select("timetable", center_id, {"date": date_str, "shift": shift})
    if not ok:
        return f"Error: {tt_rows}"
    if not tt_rows:
        return "No exams found for the selected date and shift to generate room chart."

    exam_time = tt_rows[0].get("time_slot") or ""
    unique_classes = sorted({t.get("class") for t in tt_rows if t.get("class")})
    if len(unique_classes) == 1:
        class_summary_header = f"{unique_classes[0]} Examination {datetime.datetime.now().year}"
    elif len(unique_classes) > 1:
        class_summary_header = f"Various Classes Examination {datetime.datetime.now().year}"
    else:
        class_summary_header = f"Examination {datetime.datetime.now().year}"

    tt_by_paper_code = {t["paper_code"]: t for t in tt_rows}

    ok_c, center_info = db.get_center_info(center_id)
    university_name = center_info.get("university_name", "").upper() if ok_c else ""
    center_name = center_info.get("center_name", "") if ok_c else ""
    center_code = center_info.get("center_code", "") if ok_c else ""

    header_block = (
        f",,,,,,,,,\n{university_name} ,,,,,,,,,\n"
        f"\"Examination Centre :- {center_name} Code :- {center_code} \",,,,,,,,,\n"
        f"{class_summary_header},,,,,,,,,\n"
        f"date :- ,,{date_str},,shift :-,{shift},,Time :- ,{exam_time},\n"
    )

    parts = []

    ok, seats = db.select("assigned_seats", center_id, {"date": date_str, "shift": shift})
    if not ok:
        return f"Error: {seats}"
    if not seats:
        return header_block + "\nNo students assigned seats for this date and shift."

    for s in seats:
        tt = tt_by_paper_code.get(s["paper_code"], {})
        s["_class"] = s.get("class") or tt.get("class", "")
        s["_mode"] = s.get("mode") or tt.get("mode") or "REGULAR"
        s["_type"] = s.get("type") or tt.get("type") or "REGULAR"
        s["_paper_name"] = tt.get("paper_name") or s.get("paper_name") or ""

    by_room = defaultdict(list)
    for s in seats:
        by_room[s["room_number"]].append(s)

    for room_num in sorted(by_room.keys(), key=lambda r: (0, int(r)) if str(r).isdigit() else (1, str(r))):
        room_data = sorted(by_room[room_num], key=lambda s: _room_chart_seat_sort_key(s["seat_number"]))
        parts.append("\n" + header_block)
        parts.append(f",,,Room  :-,{room_num}  ,,,,\n")

        seen_papers = set()
        for s in room_data:
            key = (s["_class"], s["paper_code"], s["_mode"], s["_type"], s["_paper_name"])
            if key in seen_papers:
                continue
            seen_papers.add(key)
            count_for_paper = sum(
                1 for x in room_data
                if x["paper_code"] == s["paper_code"] and x["_paper_name"] == s["_paper_name"]
                and x["_mode"] == s["_mode"] and x["_type"] == s["_type"]
            )
            parts.append(
                "Name of Exam (Class - mode - Type),,,Paper  (paper- paper code - paper name),,,,Answer Sheets (number of students),,\n"
                ",,,,,,,Received  ,Used  ,Balance   \n"
                f"{s['_class']} - {s['_mode']} - {s['_type']} ,,,{s['paper_code']} - {s['_paper_name']}        ,,,,{count_for_paper},,\n"
            )
            parts.append(",,,,,,,,,\n")

        parts.append(",,,,,,,,,\n")
        parts.append(f",,,Total,,,,{len(room_data)},,\n")
        parts.append(",,,,,,,,,\n")
        parts.append("roll number - (room number-seat number) - 20 letters of paper name,,,,,,,,,\n")

        line_students = []
        for s in room_data:
            truncated = (s["_paper_name"] or "")[:20]
            line_students.append(f"{s['roll_number']}( Room -{room_num}-Seat -{s['seat_number']})-{truncated}")
            if len(line_students) == 10:
                parts.append(",".join(line_students) + "\n")
                line_students = []
        if line_students:
            parts.append(",".join(line_students) + "\n")

        parts.append(_absent_ufm_block(center_id, room_num, date_str, shift, room_data))
        parts.append("\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Room Chart Report — PDF version
# ---------------------------------------------------------------------------

def generate_room_chart_pdf(center_id: str, date_str: str, shift: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    ok, tt_rows = db.select("timetable", center_id, {"date": date_str, "shift": shift})
    if not ok or not tt_rows:
        return b""

    exam_time = tt_rows[0].get("time_slot") or ""
    unique_classes = sorted({t.get("class") for t in tt_rows if t.get("class")})
    if len(unique_classes) == 1:
        class_summary_header = f"{unique_classes[0]} Examination {datetime.datetime.now().year}"
    elif len(unique_classes) > 1:
        class_summary_header = f"Various Classes Examination {datetime.datetime.now().year}"
    else:
        class_summary_header = f"Examination {datetime.datetime.now().year}"

    tt_by_paper_code = {t["paper_code"]: t for t in tt_rows}

    ok_c, center_info = db.get_center_info(center_id)
    university_name = center_info.get("university_name", "").upper() if ok_c else ""
    center_name = center_info.get("center_name", "") if ok_c else ""
    center_code = center_info.get("center_code", "") if ok_c else ""

    ok, seats = db.select("assigned_seats", center_id, {"date": date_str, "shift": shift})
    if not ok or not seats:
        return b""

    for s in seats:
        tt = tt_by_paper_code.get(s["paper_code"], {})
        s["_class"] = s.get("class") or tt.get("class", "")
        s["_mode"] = s.get("mode") or tt.get("mode") or "REGULAR"
        s["_type"] = s.get("type") or tt.get("type") or "REGULAR"
        s["_paper_name"] = tt.get("paper_name") or s.get("paper_name") or ""

    by_room = defaultdict(list)
    for s in seats:
        by_room[s["room_number"]].append(s)

    styles = getSampleStyleSheet()
    center_style = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER)
    title_style = ParagraphStyle("title", parent=styles["Heading2"], alignment=TA_CENTER)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7, leading=8)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                             leftMargin=0.4 * inch, rightMargin=0.4 * inch)
    story = []

    room_keys = sorted(by_room.keys(), key=lambda r: (0, int(r)) if str(r).isdigit() else (1, str(r)))
    for idx, room_num in enumerate(room_keys):
        room_data = sorted(by_room[room_num], key=lambda s: _room_chart_seat_sort_key(s["seat_number"]))

        story.append(Paragraph(university_name, title_style))
        story.append(Paragraph(f"Examination Centre :- {center_name} Code :- {center_code}", center_style))
        story.append(Paragraph(class_summary_header, center_style))
        story.append(Paragraph(f"date :- {date_str}&nbsp;&nbsp;&nbsp; shift :- {shift}&nbsp;&nbsp;&nbsp; Time :- {exam_time}", center_style))
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"Room :- {room_num}", title_style))
        story.append(Spacer(1, 6))

        seen_papers = set()
        paper_rows = [["Name of Exam (Class - mode - Type)", "Paper", "Received", "Used", "Balance"]]
        for s in room_data:
            key = (s["_class"], s["paper_code"], s["_mode"], s["_type"], s["_paper_name"])
            if key in seen_papers:
                continue
            seen_papers.add(key)
            count_for_paper = sum(
                1 for x in room_data
                if x["paper_code"] == s["paper_code"] and x["_paper_name"] == s["_paper_name"]
                and x["_mode"] == s["_mode"] and x["_type"] == s["_type"]
            )
            paper_rows.append([
                f"{s['_class']} - {s['_mode']} - {s['_type']}",
                f"{s['paper_code']} - {s['_paper_name']}",
                str(count_for_paper), "", "",
            ])
        paper_table = Table(paper_rows, colWidths=[2.3 * inch, 3.0 * inch, 0.7 * inch, 0.6 * inch, 0.7 * inch])
        paper_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]))
        story.append(paper_table)
        story.append(Spacer(1, 6))

        roll_rows = []
        row_buf = []
        for s in room_data:
            truncated = (s["_paper_name"] or "")[:16]
            row_buf.append(f"{s['roll_number']}\n(R{room_num}-S{s['seat_number']})-{truncated}")
            if len(row_buf) == 5:
                roll_rows.append(row_buf)
                row_buf = []
        if row_buf:
            while len(row_buf) < 5:
                row_buf.append("")
            roll_rows.append(row_buf)

        if roll_rows:
            roll_table = Table(roll_rows, colWidths=[1.46 * inch] * 5)
            roll_table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(roll_table)
            story.append(Spacer(1, 6))

        absent_entries, ufm_entries, invigilators = _get_absent_ufm_data(center_id, room_num, date_str, shift, room_data)
        au_rows = [["Absent Roll Number", "UFM Roll Number"]]
        max_rows = max(len(absent_entries), len(ufm_entries), 1)
        for i in range(max_rows):
            a_roll = absent_entries[i][1] if i < len(absent_entries) else ""
            u_roll = ufm_entries[i][1] if i < len(ufm_entries) else ""
            au_rows.append([a_roll, u_roll])
        au_rows.append([f"Total: {len(absent_entries)}", f"Total: {len(ufm_entries)}"])
        au_table = Table(au_rows, colWidths=[3.65 * inch] * 2)
        au_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
        ]))
        story.append(au_table)
        story.append(Spacer(1, 6))

        inv_rows_pdf = []
        for i in range(3):
            name = invigilators[i] if i < len(invigilators) else ""
            inv_rows_pdf.append([f"{i + 1}. Name of Invigilator:", name, "Signature:", ""])
        inv_table = Table(inv_rows_pdf, colWidths=[1.6 * inch, 2.05 * inch, 1.0 * inch, 2.6 * inch])
        inv_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (1, 0), (1, -1), 0.5, colors.black),
            ("LINEBELOW", (3, 0), (3, -1), 0.5, colors.black),
        ]))
        story.append(inv_table)

        if idx < len(room_keys) - 1:
            from reportlab.platypus import PageBreak
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


def _get_absent_ufm_data(center_id: str, room_num: str, date_str: str, shift: str, room_data: list):
    """Returns (absent_entries, ufm_entries, invigilators) for a room's
    session — absent_entries/ufm_entries are [(paper_label, roll), ...],
    pulled from cs_reports and room_invigilator_assignments."""
    paper_codes_in_room = {s["paper_code"] for s in room_data}
    ok, reports = db.select("cs_reports", center_id, {"room_num": room_num, "date": date_str, "shift": shift})
    reports = [r for r in reports if r["paper_code"] in paper_codes_in_room] if ok else []

    absent_entries, ufm_entries = [], []
    for r in reports:
        paper_label = r.get("paper_name") or r.get("paper_code") or ""
        for roll in (r.get("absent_roll_numbers") or []):
            absent_entries.append((paper_label, roll))
        for roll in (r.get("ufm_roll_numbers") or []):
            ufm_entries.append((paper_label, roll))

    ok_i, inv_rows = db.select("room_invigilator_assignments", center_id, {"room_num": room_num, "date": date_str, "shift": shift})
    invigilators = inv_rows[0].get("invigilators", []) if ok_i and inv_rows else []

    return absent_entries, ufm_entries, invigilators


def _absent_ufm_block(center_id: str, room_num: str, date_str: str, shift: str, room_data: list) -> str:
    """Absent/UFM examinees table + invigilator signature lines, matching
    the official room-chart layout — CSV-text version."""
    absent_entries, ufm_entries, invigilators = _get_absent_ufm_data(center_id, room_num, date_str, shift, room_data)

    lines = [
        "Absent examinees,,,,,,UFM Examinees,,,\n",
        "paper name,,Absent roll number,,,Total,UFM roll number and extra answer sheets,,,Total\n",
    ]

    max_rows = max(len(absent_entries), len(ufm_entries), 1)
    for i in range(max_rows):
        a_paper, a_roll = absent_entries[i] if i < len(absent_entries) else ("", "")
        u_paper, u_roll = ufm_entries[i] if i < len(ufm_entries) else ("", "")
        paper_col = a_paper or u_paper if i == 0 else ""
        lines.append(f"{paper_col},,{a_roll},,,,{u_roll},,,\n")

    lines.append(f"Total,,,,,{len(absent_entries)},Total,,,{len(ufm_entries)}\n")

    for i in range(3):
        name = invigilators[i] if i < len(invigilators) else ""
        lines.append(f"{i + 1},Name of Invigilator,{name},,,,Signature,,,\n")

    return "".join(lines)


def render_room_chart(center_id: str):
    st.subheader("📄 Room Chart Report")

    ok, tt_rows = db.select("timetable", center_id)
    if not ok or not tt_rows:
        st.warning("No timetable data available.")
        return

    scheduled = [r for r in tt_rows if r.get("date") and r.get("shift")]
    dates = sorted({r["date"] for r in scheduled})
    if not dates:
        st.warning("No scheduled papers yet.")
        return

    sel_date = st.selectbox("Select date", dates, key="room_chart_date")
    shift_options = sorted({r["shift"] for r in scheduled if r["date"] == sel_date})
    sel_shift = st.selectbox("Select shift", shift_options, key="room_chart_shift")

    if st.button("Generate Room Chart"):
        output = generate_room_chart_report(center_id, sel_date, sel_shift)
        if output.startswith("Error:"):
            st.error(output)
        else:
            st.text_area("Generated Room Chart", output, height=600)
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "Download Room Chart as CSV", data=output.encode("utf-8"),
                    file_name=f"room_chart_{sel_date}_{sel_shift}.csv", mime="text/csv",
                )
            with col2:
                pdf_bytes = generate_room_chart_pdf(center_id, sel_date, sel_shift)
                if pdf_bytes:
                    st.download_button(
                        "Download Room Chart as PDF", data=pdf_bytes,
                        file_name=f"room_chart_{sel_date}_{sel_shift}.pdf", mime="application/pdf",
                    )


# ---------------------------------------------------------------------------
# Roll Number Notice List — flat, roll-number-ascending list for posting on
# the exam center's physical notice board, so students can find their own
# room/seat without needing the app.
# ---------------------------------------------------------------------------

def _roll_sort_key(roll: str):
    try:
        return (0, int(roll))
    except (ValueError, TypeError):
        return (1, str(roll))


def generate_roll_number_notice_list(center_id: str, date_str: str, shift: str) -> str:
    ok, tt_rows = db.select("timetable", center_id, {"date": date_str, "shift": shift})
    if not ok:
        return f"Error: {tt_rows}"
    if not tt_rows:
        return "No exams found for the selected date and shift."

    exam_time = tt_rows[0].get("time_slot") or ""
    unique_classes = sorted({t.get("class") for t in tt_rows if t.get("class")})
    if len(unique_classes) == 1:
        class_summary_header = f"{unique_classes[0]} Examination {datetime.datetime.now().year}"
    elif len(unique_classes) > 1:
        class_summary_header = f"Various Classes Examination {datetime.datetime.now().year}"
    else:
        class_summary_header = f"Examination {datetime.datetime.now().year}"

    tt_by_paper_code = {t["paper_code"]: t for t in tt_rows}

    ok, seats = db.select("assigned_seats", center_id, {"date": date_str, "shift": shift})
    if not ok:
        return f"Error: {seats}"
    if not seats:
        return "No students have been assigned seats for this date and shift."

    ok_c, center_info = db.get_center_info(center_id)
    university_name = center_info.get("university_name", "").upper() if ok_c else ""
    center_name = center_info.get("center_name", "") if ok_c else ""
    center_code = center_info.get("center_code", "") if ok_c else ""

    header_block = (
        f",,,,,,,,,\n{university_name} ,,,,,,,,,\n"
        f"\"Examination Centre :- {center_name} Code :- {center_code} \",,,,,,,,,\n"
        f"{class_summary_header},,,,,,,,,\n"
        f"date :- ,,{date_str},,shift :-,{shift},,Time :- ,{exam_time},\n"
    )

    for s in seats:
        tt = tt_by_paper_code.get(s["paper_code"], {})
        s["_paper_name"] = tt.get("paper_name") or s.get("paper_name") or ""

    seats_sorted = sorted(seats, key=lambda s: _roll_sort_key(s["roll_number"]))

    parts = [
        header_block,
        ",,,,,,,,,\n",
        "roll number - (room number-seat number) ,,,,,,,,,\n",
    ]

    line_students = []
    for s in seats_sorted:
        truncated = (s["_paper_name"] or "")[:20]
        line_students.append(f"{s['roll_number']}( Room -{s['room_number']}-Seat -{s['seat_number']})-{truncated}")
        if len(line_students) == 10:
            parts.append(",".join(line_students) + "\n")
            line_students = []
    if line_students:
        parts.append(",".join(line_students) + "\n")

    return "".join(parts)


def render_roll_number_notice_list(center_id: str):
    st.subheader("📋 Roll Number Notice List (for Notice Board)")
    st.info("A flat, roll-number-ascending list of every student's room and seat for a shift — meant for printing and posting at the center so students can look themselves up offline.")

    ok, tt_rows = db.select("timetable", center_id)
    if not ok or not tt_rows:
        st.warning("No timetable data available.")
        return

    scheduled = [r for r in tt_rows if r.get("date") and r.get("shift")]
    dates = sorted({r["date"] for r in scheduled})
    if not dates:
        st.warning("No scheduled papers yet.")
        return

    sel_date = st.selectbox("Select date", dates, key="notice_list_date")
    shift_options = sorted({r["shift"] for r in scheduled if r["date"] == sel_date})
    sel_shift = st.selectbox("Select shift", shift_options, key="notice_list_shift")

    if st.button("Generate Notice List"):
        output = generate_roll_number_notice_list(center_id, sel_date, sel_shift)
        if output.startswith("Error:"):
            st.error(output)
        else:
            st.text_area("Generated Notice List", output, height=600)
            st.download_button(
                "Download Notice List as CSV", data=output.encode("utf-8"),
                file_name=f"notice_list_{sel_date}_{sel_shift}.csv", mime="text/csv",
            )


# ---------------------------------------------------------------------------
# Session Statistics (Overall / Paper-wise / Class-wise)
# ---------------------------------------------------------------------------

def render_statistics(center_id: str):
    st.subheader("📊 Exam Session Reports")

    ok, reports = db.select("cs_reports", center_id)
    if not ok:
        st.error(reports)
        return
    if not reports:
        st.info("No Centre Superintendent reports available yet for statistics.")
        return

    ok, seats = db.select("assigned_seats", center_id)
    if not ok:
        st.error(seats)
        return
    if not seats:
        st.warning("Assigned seats data is required to calculate expected student counts.")
        return

    expected_by_session = defaultdict(int)  # (date, shift, room, paper_code, paper_name) -> count
    for s in seats:
        key = (s["date"], s["shift"], s["room_number"], s["paper_code"], s["paper_name"])
        expected_by_session[key] += 1

    total_reports = len(reports)
    unique_sessions = len({r["report_key"] for r in reports})
    total_expected, total_absent, total_ufm = 0, 0, 0

    paper_expected, paper_absent, paper_ufm = defaultdict(int), defaultdict(int), defaultdict(int)
    class_expected, class_absent, class_ufm = defaultdict(int), defaultdict(int), defaultdict(int)

    for r in reports:
        key = (r["date"], r["shift"], r["room_num"], r["paper_code"], r["paper_name"])
        expected = expected_by_session.get(key, 0)
        absent = len(r.get("absent_roll_numbers") or [])
        ufm = len(r.get("ufm_roll_numbers") or [])

        total_expected += expected
        total_absent += absent
        total_ufm += ufm

        p_key = (r["paper_name"], r["paper_code"])
        paper_expected[p_key] += expected
        paper_absent[p_key] += absent
        paper_ufm[p_key] += ufm

        c_key = r.get("class") or "Unknown"
        class_expected[c_key] += expected
        class_absent[c_key] += absent
        class_ufm[c_key] += ufm

    total_present = total_expected - total_absent
    total_sheets = total_present - total_ufm
    attendance_pct = (total_present / total_expected * 100) if total_expected > 0 else 0

    st.markdown("---")
    st.subheader("Overall Statistics")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Reports Submitted", total_reports)
    col2.metric("Unique Exam Sessions Reported", unique_sessions)
    col3.metric("Total Expected Students", total_expected)
    col4.metric("Total Absent Students", total_absent)
    col5.metric("Overall Attendance (%)", f"{attendance_pct:.2f}%")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Present Students", total_present)
    c2.metric("Total UFM Cases", total_ufm)
    c3.metric("Total Answer Sheets Collected", total_sheets)

    def _rows(expected_map, absent_map, ufm_map, label_key):
        rows = []
        for key, expected in expected_map.items():
            absent = absent_map[key]
            ufm = ufm_map[key]
            present = expected - absent
            sheets = present - ufm
            pct = f"{(present / expected * 100):.2f}%" if expected > 0 else "0.00%"
            row = {"Expected Students": expected, "Present Students": present,
                   "Absent Students": absent, "UFM Cases": ufm,
                   "Answer Sheets Collected": sheets, "Attendance (%)": pct}
            if isinstance(key, tuple):
                row["Paper Name"], row["Paper Code"] = key
            else:
                row[label_key] = key
            rows.append(row)
        return rows

    st.markdown("---")
    st.subheader("Paper-wise Statistics")
    paper_rows = _rows(paper_expected, paper_absent, paper_ufm, "Paper Name")
    if paper_rows:
        st.dataframe(paper_rows, use_container_width=True)

    st.markdown("---")
    st.subheader("Class-wise Statistics")
    class_rows = _rows(class_expected, class_absent, class_ufm, "Class")
    if class_rows:
        st.dataframe(class_rows, use_container_width=True)


# ---------------------------------------------------------------------------
# Shared entrypoint (used by both admin_panel.py and cs_panel.py)
# ---------------------------------------------------------------------------

def render(center_id: str):
    report_option = st.radio(
        "Select Report:",
        ["Room Occupancy Report", "Room Chart Report", "Roll Number Notice List", "Session Statistics"],
        key="reporting_option_radio",
    )
    st.markdown("---")

    if report_option == "Room Occupancy Report":
        render_room_occupancy(center_id)
    elif report_option == "Room Chart Report":
        render_room_chart(center_id)
    elif report_option == "Roll Number Notice List":
        render_roll_number_notice_list(center_id)
    elif report_option == "Session Statistics":
        render_statistics(center_id)
