"""
remuneration.py — Remuneration bill generation.

Core model: every paid duty — a real exam shift, a prep day, or a closing
day — becomes an "event" (name, role_key, date, shift_label, rate,
conveyance). Individual bills AND the role-summary matrix are both just
different groupings of the same event list. This fixes a bug in the
previous version: prep/closing-only people (no real shift assignment)
were silently skipped entirely, so their prep/closing pay and holiday
conveyance never appeared anywhere.

Business rules ported from the original calculate_remuneration():
  1. A person gets exam-day conveyance only if they worked BOTH shifts of
     that date (selected or not).
  2. If eligible, conveyance is paid in the Evening shift only.
  3. Each duty role's rate can be set to "per shift" or "per day" in
     Rates config. A per-day role assigned both Morning and Evening the
     same date is paid once — the later shift's event shows the person
     worked but pays 0, so they're never double-paid for one day.
  4. Preparation/closing day remuneration and holiday conveyance apply only
     to the role a person is specifically assigned prep/closing duty under,
     and only to roles eligible for prep/closing duty (not invigilators).
  5. Class 3 / Class 4 workers are paid per-student (center-wide student
     count for the selected classes), split evenly among however many
     workers were assigned that date range.

Dates (prep/closing days, holiday dates) accept either DD-MM-YYYY or
YYYY-MM-DD on input and are normalized to ISO internally, so a typed
DD-MM-YYYY date still correctly matches an ISO exam date and a holiday
date typed in either format.
"""

import datetime
import io
from collections import defaultdict

import pandas as pd
import streamlit as st

import db


def _to_iso(date_str: str) -> str:
    """Best-effort normalization of a date string to YYYY-MM-DD, so dates
    typed as DD-MM-YYYY (or with slashes) still match ISO dates coming from
    date-picker fields elsewhere in the app. Falls back to the original
    string, stripped, if nothing parses."""
    if not date_str:
        return date_str
    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _to_display(iso_date: str) -> str:
    """YYYY-MM-DD -> DD-MM-YYYY for display. Falls back to input unchanged."""
    try:
        return datetime.date.fromisoformat(iso_date).strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return iso_date


ROLE_RULES = {
    "senior_center_superintendent": {"label": "Senior Center Superintendent", "short": "SCS", "eligible_prep_close": True, "conveyance": True},
    "center_superintendent": {"label": "Center Superintendent", "short": "CS", "eligible_prep_close": True, "conveyance": True},
    "assistant_center_superintendent": {"label": "Assistant Center Superintendent", "short": "ACS", "eligible_prep_close": True, "conveyance": True},
    "permanent_invigilator": {"label": "Permanent Invigilator", "short": "PI/API", "eligible_prep_close": True, "conveyance": True},
    "assistant_permanent_invigilator": {"label": "Assistant Permanent Invigilator", "short": "PI/API", "eligible_prep_close": False, "conveyance": True},
    "invigilator": {"label": "Invigilator (room-assigned)", "short": "Invigilators", "eligible_prep_close": False, "conveyance": True},
}
PREP_CLOSE_ELIGIBLE_ROLES = [k for k, v in ROLE_RULES.items() if v["eligible_prep_close"]]
DUTY_ROLE_KEYS = list(ROLE_RULES.keys())  # roles priced per-shift or per-day
MATRIX_ROLE_COLUMNS = ["SCS", "CS", "ACS", "PI/API", "Invigilators"]  # display order

CLASS_WORKER_RULES = {
    "class_3_worker": {"label": "Class 3 Worker"},
    "class_4_worker": {"label": "Class 4 Worker"},
}
RATE_KEYS = [
    "senior_center_superintendent_rate", "center_superintendent_rate",
    "assistant_center_superintendent_rate", "permanent_invigilator_rate",
    "assistant_permanent_invigilator_rate", "invigilator_rate",
    "class_3_worker_rate_per_student", "class_4_worker_rate_per_student",
    "conveyance_rate", "holiday_conveyance_allowance_rate",
]
RATE_LABELS = {
    "senior_center_superintendent_rate": "Senior Center Superintendent — Rate",
    "center_superintendent_rate": "Center Superintendent — Rate",
    "assistant_center_superintendent_rate": "Assistant Center Superintendent — Rate",
    "permanent_invigilator_rate": "Permanent Invigilator — Rate",
    "assistant_permanent_invigilator_rate": "Assistant Permanent Invigilator — Rate",
    "invigilator_rate": "Invigilator — Rate",
    "class_3_worker_rate_per_student": "Class 3 Worker (per student, center-wide)",
    "class_4_worker_rate_per_student": "Class 4 Worker (per student, center-wide)",
    "conveyance_rate": "Exam-day Conveyance (evening shift, if worked both shifts)",
    "holiday_conveyance_allowance_rate": "Holiday Conveyance Allowance (per assigned holiday)",
}


# ---------------------------------------------------------------------------
# Rates config
# ---------------------------------------------------------------------------

def _load_rates(center_id: str) -> dict:
    ok, rows = db.select("remuneration_rates", center_id)
    rates = {k: 0.0 for k in RATE_KEYS}
    if ok:
        for r in rows:
            if r["role_key"] in rates and r.get("rate") is not None:
                rates[r["role_key"]] = float(r["rate"])
    return rates


def _load_rate_units(center_id: str) -> dict:
    """role_key -> 'shift' or 'day', for the six duty roles only."""
    ok, rows = db.select("remuneration_rates", center_id)
    units = {k: "shift" for k in DUTY_ROLE_KEYS}
    if ok:
        for r in rows:
            role_key = r["role_key"].replace("_rate", "")
            if role_key in units and r.get("unit") in ("shift", "day"):
                units[role_key] = r["unit"]
    return units


def render_rates_config(center_id: str):
    st.subheader("💰 Remuneration Rates")
    rates = _load_rates(center_id)
    units = _load_rate_units(center_id)

    st.markdown("**Duty roles** — choose whether each role is paid per shift or per day (a per-day role is paid once even if assigned both Morning and Evening the same date).")
    new_units = {}
    for role_key in DUTY_ROLE_KEYS:
        rate_key = f"{role_key}_rate"
        c1, c2 = st.columns([2, 1])
        with c1:
            rates[rate_key] = st.number_input(
                RATE_LABELS[rate_key], min_value=0.0, value=rates[rate_key], step=10.0, key=f"rate_{rate_key}",
            )
        with c2:
            new_units[role_key] = st.selectbox(
                "Unit", ["shift", "day"], index=["shift", "day"].index(units[role_key]),
                key=f"unit_{role_key}", label_visibility="visible" if role_key == DUTY_ROLE_KEYS[0] else "collapsed",
            )

    st.markdown("**Other rates**")
    other_keys = [k for k in RATE_KEYS if k not in {f"{r}_rate" for r in DUTY_ROLE_KEYS}]
    cols = st.columns(2)
    for i, key in enumerate(other_keys):
        with cols[i % 2]:
            rates[key] = st.number_input(RATE_LABELS[key], min_value=0.0, value=rates[key], step=10.0, key=f"rate_{key}")

    if st.button("Save Rates"):
        rows = []
        for key, val in rates.items():
            unit = new_units.get(key.replace("_rate", ""), "per_student" if "rate_per" in key else "shift")
            rows.append({"role_key": key, "rate": val, "unit": unit})
        ok, result = db.upsert("remuneration_rates", center_id, rows, on_conflict="center_id,role_key")
        (st.success("Rates saved.") if ok else st.error(result))


# ---------------------------------------------------------------------------
# Prep/closing assignments & holiday dates
# ---------------------------------------------------------------------------

def render_prep_closing_config(center_id: str):
    st.subheader("🗓️ Preparation / Closing Day Assignments")

    ok, members_rows = db.select("exam_team_members", center_id, order="name")
    members = [m["name"] for m in members_rows] if ok else []
    if not members:
        st.info("Add exam team members first (Centre Superintendent Panel \u2192 Manage Exam Team & Shift Assignments).")
        return

    ok, existing_rows = db.select("prep_closing_assignments", center_id)
    existing = {r["name"]: r for r in existing_rows} if ok else {}

    name = st.selectbox("Team Member", members, key="prep_closing_member_select")
    current = existing.get(name, {})

    current_role_idx = PREP_CLOSE_ELIGIBLE_ROLES.index(current["role"]) if current.get("role") in PREP_CLOSE_ELIGIBLE_ROLES else 0
    role = st.selectbox(
        "Role for prep/closing duty", PREP_CLOSE_ELIGIBLE_ROLES, index=current_role_idx,
        format_func=lambda r: ROLE_RULES[r]["label"], key="prep_closing_role_select",
        help="Only roles eligible for prep/closing pay are listed.",
    )

    prep_days_str = st.text_input(
        "Preparation Days (comma-separated, DD-MM-YYYY or YYYY-MM-DD)",
        value=", ".join(_to_display(d) for d in current.get("prep_days", [])), key="prep_days_input",
    )
    closing_days_str = st.text_input(
        "Closing Days (comma-separated, DD-MM-YYYY or YYYY-MM-DD)",
        value=", ".join(_to_display(d) for d in current.get("closing_days", [])), key="closing_days_input",
    )
    prep_days = [_to_iso(d.strip()) for d in prep_days_str.split(",") if d.strip()]
    closing_days = [_to_iso(d.strip()) for d in closing_days_str.split(",") if d.strip()]

    if st.button("Save Prep/Closing Assignment"):
        data = {"name": name, "role": role, "prep_days": prep_days, "closing_days": closing_days, "selected_classes": []}
        if name in existing:
            ok, result = db.update("prep_closing_assignments", center_id, {"name": name}, data)
        else:
            ok, result = db.insert("prep_closing_assignments", center_id, data)
        (st.success("Saved.") if ok else st.error(result))
        if ok:
            st.rerun()

    if existing:
        st.write("Current assignments:")
        st.dataframe(list(existing.values()), use_container_width=True)


def render_holiday_dates_config(center_id: str) -> list:
    st.subheader("🎌 Holiday Dates")
    ok, rows = db.select("global_settings", center_id, {"setting_key": "holiday_dates"})
    current = rows[0]["setting_value"] if ok and rows else []
    if not isinstance(current, list):
        current = []

    holiday_dates_str = st.text_input(
        "Holiday dates, comma-separated (DD-MM-YYYY or YYYY-MM-DD)",
        value=", ".join(_to_display(d) for d in current), key="holiday_dates_input",
    )
    holiday_dates = [_to_iso(d.strip()) for d in holiday_dates_str.split(",") if d.strip()]

    if st.button("Save Holiday Dates"):
        ok, result = db.upsert(
            "global_settings", center_id, {"setting_key": "holiday_dates", "setting_value": holiday_dates},
            on_conflict="center_id,setting_key",
        )
        (st.success("Holiday dates saved.") if ok else st.error(result))
    return holiday_dates


# ---------------------------------------------------------------------------
# Event model — the single source of truth for both bills and the matrix
# ---------------------------------------------------------------------------

def _build_events(center_id: str, rates: dict, rate_units: dict, prep_closing: dict, holiday_dates: list, selected_classes: list) -> list:
    holiday_dates_iso = {_to_iso(d) for d in holiday_dates}

    ok, timetable_rows = db.select("timetable", center_id)
    timetable_rows = timetable_rows if ok else []
    scheduled = [t for t in timetable_rows if t.get("date") and t.get("shift")]

    session_classes_map = defaultdict(set)
    for t in scheduled:
        session_classes_map[(t["date"], t["shift"])].add(t.get("class"))

    ok, shift_rows = db.select("shift_assignments", center_id)
    shift_rows = shift_rows if ok else []
    ok, room_inv_rows = db.select("room_invigilator_assignments", center_id)
    room_inv_rows = room_inv_rows if ok else []

    raw_exam_assignments = []
    for row in shift_rows:
        for role_key in ROLE_RULES.keys():
            if role_key == "invigilator":
                continue
            for person in (row.get(role_key) or []):
                raw_exam_assignments.append({"name": person, "role_key": role_key, "date": row["date"], "shift": row["shift"]})
    for row in room_inv_rows:
        for person in (row.get("invigilators") or []):
            dup = any(a["name"] == person and a["date"] == row["date"] and a["shift"] == row["shift"] for a in raw_exam_assignments)
            if not dup:
                raw_exam_assignments.append({"name": person, "role_key": "invigilator", "date": row["date"], "shift": row["shift"]})

    both_shifts = set()
    by_person_date = defaultdict(set)
    for a in raw_exam_assignments:
        by_person_date[(a["name"], a["date"])].add(a["shift"])
    for (name, date), shifts in by_person_date.items():
        if {"Morning", "Evening"}.issubset(shifts):
            both_shifts.add((name, date))

    paid_day = set()  # (name, role_key, date) already paid once for a "day"-unit role
    events = []

    for a in sorted(raw_exam_assignments, key=lambda a: (a["date"], 0 if a["shift"] == "Morning" else 1)):
        name, role_key, date, shift = a["name"], a["role_key"], a["date"], a["shift"]
        rule = ROLE_RULES[role_key]
        rate = rates.get(f"{role_key}_rate", 0.0)
        is_day_unit = rate_units.get(role_key, "shift") == "day"

        classes_in_session = session_classes_map.get((date, shift), set())
        is_selected = bool(set(selected_classes) & classes_in_session) if selected_classes else True

        rate_paid = rate
        if is_day_unit:
            key = (name, role_key, date)
            if key in paid_day:
                rate_paid = 0.0
            else:
                paid_day.add(key)

        conveyance = 0.0
        if rule["conveyance"] and (name, date) in both_shifts and shift == "Evening" and is_selected:
            conveyance = rates.get("conveyance_rate", 0.0)

        events.append({
            "name": name, "role_key": role_key, "date": date, "shift": shift,
            "row_label": shift, "event_type": "exam",
            "rate": rate, "rate_paid": rate_paid, "conveyance": conveyance,
            "is_selected": is_selected,
        })

    for name, cfg in prep_closing.items():
        role_key = cfg.get("role")
        if role_key not in PREP_CLOSE_ELIGIBLE_ROLES:
            continue
        rate = rates.get(f"{role_key}_rate", 0.0)
        holiday_rate = rates.get("holiday_conveyance_allowance_rate", 0.0)

        for raw_date in (cfg.get("prep_days") or []):
            date = _to_iso(raw_date)
            conveyance = holiday_rate if date in holiday_dates_iso else 0.0
            events.append({
                "name": name, "role_key": role_key, "date": date, "shift": "PrepClosing",
                "row_label": "Pre Exam Preparation", "event_type": "prep",
                "rate": rate, "rate_paid": rate, "conveyance": conveyance, "is_selected": True,
            })
        for raw_date in (cfg.get("closing_days") or []):
            date = _to_iso(raw_date)
            conveyance = holiday_rate if date in holiday_dates_iso else 0.0
            events.append({
                "name": name, "role_key": role_key, "date": date, "shift": "PrepClosing",
                "row_label": "Post Exam Closing", "event_type": "closing",
                "rate": rate, "rate_paid": rate, "conveyance": conveyance, "is_selected": True,
            })

    return events


# ---------------------------------------------------------------------------
# Individual bills
# ---------------------------------------------------------------------------

def calculate_individual_bills(events: list) -> list:
    people = sorted({(e["name"], e["role_key"]) for e in events}, key=lambda p: list(ROLE_RULES.keys()).index(p[1]))

    bills = []
    for name, role_key in people:
        rule = ROLE_RULES[role_key]
        rows = [e for e in events if e["name"] == name and e["role_key"] == role_key and e["is_selected"]]
        if not rows:
            continue

        exam_rows = [r for r in rows if r["event_type"] == "exam"]
        prep_rows = [r for r in rows if r["event_type"] == "prep"]
        closing_rows = [r for r in rows if r["event_type"] == "closing"]

        morning = sorted({r["date"] for r in exam_rows if r["shift"] == "Morning"})
        evening = sorted({r["date"] for r in exam_rows if r["shift"] == "Evening"})

        base_remuneration = sum(r["rate_paid"] for r in exam_rows)
        total_conveyance = sum(r["conveyance"] for r in exam_rows)
        prep_rem = sum(r["rate_paid"] for r in prep_rows)
        closing_rem = sum(r["rate_paid"] for r in closing_rows)
        holiday_conv = sum(r["conveyance"] for r in prep_rows) + sum(r["conveyance"] for r in closing_rows)

        total_amount = base_remuneration + total_conveyance + prep_rem + closing_rem + holiday_conv
        if total_amount <= 0:
            continue

        bills.append({
            "SN": len(bills) + 1,
            "Name (with role)": f"{name} ({rule['label']})",
            "Duty dates (Morning)": ", ".join(morning),
            "Duty dates (Evening)": ", ".join(evening),
            "Total Shifts": len(exam_rows),
            "Base Remuneration (Rs)": base_remuneration,
            "Conveyance (Rs)": total_conveyance,
            "Preparation Day Remuneration (Rs)": prep_rem,
            "Closing Day Remuneration (Rs)": closing_rem,
            "Holiday Conveyance (Rs)": holiday_conv,
            "Total Amount (Rs)": total_amount,
        })

    if bills:
        totals = {"SN": "TOTAL", "Name (with role)": "", "Duty dates (Morning)": "", "Duty dates (Evening)": ""}
        for col in ["Total Shifts", "Base Remuneration (Rs)", "Conveyance (Rs)", "Preparation Day Remuneration (Rs)",
                    "Closing Day Remuneration (Rs)", "Holiday Conveyance (Rs)", "Total Amount (Rs)"]:
            totals[col] = sum(b[col] for b in bills)
        bills.append(totals)

    return bills


# ---------------------------------------------------------------------------
# Class 3 / Class 4 worker bills (unchanged logic, still separate from events)
# ---------------------------------------------------------------------------

def calculate_class_worker_bills(center_id: str, rates: dict, selected_classes: list) -> list:
    ok, timetable_rows = db.select("timetable", center_id)
    timetable_rows = timetable_rows if ok else []
    ok, seats = db.select("assigned_seats", center_id)
    seats = seats if ok else []

    if selected_classes:
        selected_paper_keys = {(t["paper_code"], t["date"], t["shift"]) for t in timetable_rows if t.get("class") in selected_classes}
        total_students = len({s["roll_number"] for s in seats if (s["paper_code"], s["date"], s["shift"]) in selected_paper_keys})
    else:
        total_students = len({s["roll_number"] for s in seats})

    ok, shift_rows = db.select("shift_assignments", center_id)
    shift_rows = shift_rows if ok else []
    selected_dates = {t["date"] for t in timetable_rows if not selected_classes or t.get("class") in selected_classes}

    workers = {"class_3_worker": set(), "class_4_worker": set()}
    for row in shift_rows:
        if row["date"] not in selected_dates:
            continue
        for role_key in workers:
            workers[role_key].update(row.get(role_key) or [])

    rows = []
    for role_key, label_key in [("class_3_worker", "class_3_worker_rate_per_student"), ("class_4_worker", "class_4_worker_rate_per_student")]:
        names = sorted(workers[role_key])
        if not names:
            continue
        rate = rates.get(label_key, 0.0)
        category_total = total_students * rate
        per_worker = category_total / len(names) if names else 0

        rows.append({
            "S.N.": CLASS_WORKER_RULES[role_key]["label"], "Name": "", "Total Students (Center-wide)": total_students,
            "Rate per Student (Rs)": rate, "Category Total (Rs)": category_total,
            "Number of Workers": len(names), "Per-Worker Amount (Rs)": "",
        })
        for i, name in enumerate(names):
            rows.append({
                "S.N.": i + 1, "Name": name, "Total Students (Center-wide)": "",
                "Rate per Student (Rs)": "", "Category Total (Rs)": "",
                "Number of Workers": "", "Per-Worker Amount (Rs)": round(per_worker, 2),
            })

    return rows


# ---------------------------------------------------------------------------
# Role Summary Matrix (date/shift rows x role columns)
# ---------------------------------------------------------------------------

def _session_papers_display(center_id: str, date: str, shift: str, selected_classes: list) -> tuple:
    """Returns (paper_display_str, expected_student_count) for a real exam row."""
    ok, tt_rows = db.select("timetable", center_id, {"date": date, "shift": shift})
    tt_rows = tt_rows if ok else []
    if selected_classes:
        tt_rows = [t for t in tt_rows if t.get("class") in selected_classes]

    seen, labels = set(), []
    for t in tt_rows:
        key = t["paper_code"]
        if key in seen:
            continue
        seen.add(key)
        labels.append(f"{t.get('paper_name') or t.get('paper_short') or ''} ({t['paper_code']})")

    ok, seats = db.select("assigned_seats", center_id, {"date": date, "shift": shift})
    seats = seats if ok else []
    if selected_classes:
        paper_codes = {t["paper_code"] for t in tt_rows}
        seats = [s for s in seats if s["paper_code"] in paper_codes]
    student_count = len({s["roll_number"] for s in seats})

    return ", ".join(labels), student_count


def calculate_role_summary_matrix(center_id: str, events: list, selected_classes: list) -> list:
    ok, seats = db.select("assigned_seats", center_id)
    seats = seats if ok else []
    if selected_classes:
        ok, timetable_rows = db.select("timetable", center_id)
        timetable_rows = timetable_rows if ok else []
        selected_paper_keys = {(t["paper_code"], t["date"], t["shift"]) for t in timetable_rows if t.get("class") in selected_classes}
        total_students_all = len({s["roll_number"] for s in seats if (s["paper_code"], s["date"], s["shift"]) in selected_paper_keys})
    else:
        total_students_all = len({s["roll_number"] for s in seats})

    rows_by_key = defaultdict(list)  # (date, row_label) -> events
    for e in events:
        if not e["is_selected"]:
            continue
        rows_by_key[(e["date"], e["row_label"])].append(e)

    row_type_order = {"Pre Exam Preparation": 0, "Morning": 1, "Evening": 1, "Post Exam Closing": 2}
    shift_order = {"Morning": 0, "Evening": 1, "Pre Exam Preparation": 0, "Post Exam Closing": 0}

    def sort_key(item):
        (date, row_label), row_events = item
        return (row_type_order.get(row_label, 1), date, shift_order.get(row_label, 0))

    matrix = []
    for (date, row_label), row_events in sorted(rows_by_key.items(), key=sort_key):
        is_exam_row = row_events[0]["event_type"] == "exam"

        if is_exam_row:
            paper_display, num_students = _session_papers_display(center_id, date, row_label, selected_classes)
        else:
            paper_display, num_students = row_label, total_students_all

        role_cells = {col: {"count": 0, "amount": 0.0} for col in MATRIX_ROLE_COLUMNS}
        for e in row_events:
            short = ROLE_RULES[e["role_key"]]["short"]
            role_cells[short]["count"] += 1
            role_cells[short]["amount"] += e["rate_paid"]

        conveyance_total = sum(e["conveyance"] for e in row_events)
        role_total = sum(c["amount"] for c in role_cells.values())
        daily_total = role_total + conveyance_total

        row = {
            "date & shift": f"{_to_display(date)} ({row_label})",
            "Paper": paper_display,
            "Number of students": num_students,
        }
        for col in MATRIX_ROLE_COLUMNS:
            row[col] = f"{role_cells[col]['count']} ({role_cells[col]['amount']:.0f})"
        row["Conveyance"] = conveyance_total
        row["Daily Total"] = daily_total
        matrix.append(row)

    if matrix:
        totals = {"date & shift": "Total", "Paper": "", "Number of students": sum(
            m["Number of students"] for m in matrix if isinstance(m["Number of students"], (int, float))
        )}
        for col in MATRIX_ROLE_COLUMNS:
            total_count = sum(int(m[col].split(" (")[0]) for m in matrix)
            total_amount = sum(float(m[col].split("(")[1].rstrip(")")) for m in matrix)
            totals[col] = f"{total_count} ({total_amount:.0f})"
        totals["Conveyance"] = sum(m["Conveyance"] for m in matrix)
        totals["Daily Total"] = sum(m["Daily Total"] for m in matrix)
        matrix.append(totals)

    return matrix


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def _to_excel(individual_bills: list, matrix: list, class_worker_bills: list) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        if individual_bills:
            pd.DataFrame(individual_bills).to_excel(writer, sheet_name="Individual Bills", index=False)
        if matrix:
            pd.DataFrame(matrix).to_excel(writer, sheet_name="Role Summary Matrix", index=False)
        if class_worker_bills:
            pd.DataFrame(class_worker_bills).to_excel(writer, sheet_name="Class 3-4 Workers", index=False)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def render(center_id: str):
    st.header("💵 Remuneration Bill Generation")

    with st.expander("Configure Rates", expanded=False):
        render_rates_config(center_id)
    rates = _load_rates(center_id)
    rate_units = _load_rate_units(center_id)

    with st.expander("Configure Preparation/Closing Assignments", expanded=False):
        render_prep_closing_config(center_id)

    with st.expander("Configure Holiday Dates", expanded=False):
        holiday_dates = render_holiday_dates_config(center_id)

    ok, prep_closing_rows = db.select("prep_closing_assignments", center_id)
    prep_closing = {r["name"]: r for r in prep_closing_rows} if ok else {}

    st.markdown("---")
    st.subheader("Generate Bill")

    ok, timetable_rows = db.select("timetable", center_id)
    all_classes = sorted({t["class"] for t in (timetable_rows if ok else []) if t.get("class")})
    selected_classes = st.multiselect(
        "Restrict to Class(es) (leave empty for all)", all_classes, key="remuneration_selected_classes",
    )

    if st.button("Generate Remuneration Bills"):
        events = _build_events(center_id, rates, rate_units, prep_closing, holiday_dates, selected_classes)

        if not events:
            st.warning("No shift assignments, room invigilator assignments, or prep/closing assignments found yet.")
            return

        individual_bills = calculate_individual_bills(events)
        class_worker_bills = calculate_class_worker_bills(center_id, rates, selected_classes)
        matrix = calculate_role_summary_matrix(center_id, events, selected_classes)

        st.subheader("Individual Bills")
        st.dataframe(individual_bills, use_container_width=True)

        st.subheader("Role Summary Matrix (date & shift \u00d7 role)")
        st.dataframe(matrix, use_container_width=True)

        st.subheader("Class 3 / Class 4 Worker Bills")
        st.dataframe(class_worker_bills, use_container_width=True)

        excel_bytes = _to_excel(individual_bills, matrix, class_worker_bills)
        st.download_button(
            "📥 Download Bills as Excel", data=excel_bytes,
            file_name=f"remuneration_bills_{datetime.date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
