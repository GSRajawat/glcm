"""
data_ingestion.py — Sitting plan & attestation PDF import.

Ported from the original process_sitting_plan_pdfs() / process_attestation_pdfs()
(which wrote wide CSVs). Same extraction logic (PyMuPDF + regex), but rows go
straight into Supabase, scoped to center_id, instead of CSV files.

Two-phase timetable design (matches the original app's behaviour): a sitting
plan PDF tells you a paper's code/name/class but NOT its date/shift — that
gets assigned afterwards via the admin "Update Timetable" screen. So papers
extracted here are inserted into `timetable` with date/shift left NULL, and
are matched against existing NULL-date rows to avoid duplicate stubs if you
reprocess the same PDFs.
"""

import hashlib
import os
import re
import tempfile
import zipfile

import fitz  # PyMuPDF
import streamlit as st

import db


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _format_paper_code(code) -> str:
    return str(code).strip().upper() if code else ""


def _dedupe_key(roll_numbers: list, room_number: str, class_val: str,
                 mode: str, type_: str) -> str:
    raw = "|".join(sorted(roll_numbers)) + f"|{room_number}|{class_val}|{mode}|{type_}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def extract_metadata_from_pdf_text(text: str) -> dict:
    """Pulls class/mode/type/paper_code/paper_name out of a sitting-plan PDF's text."""
    pattern_match = re.search(
        r'([A-Z]+)\s*/\s*(\d+(?:SEM|YEAR))\s*/\s*([A-Z]+)\s*/\s*([A-Z]+)\s*/\s*([A-Z]{3}-20\d{2})',
        text,
    )

    if pattern_match:
        class_part = pattern_match.group(1)
        year_part = pattern_match.group(2)
        session_part = pattern_match.group(5)
        session_formatted = session_part.replace("-20", " ")
        class_val = f"{class_part} {year_part} - {session_formatted}"
        mode_type = pattern_match.group(3)
        type_type = pattern_match.group(4)
    else:
        class_match = re.search(r'([A-Z]+)\s*/?\s*(\d+(?:SEM|YEAR))', text)
        session_match = re.search(r'([A-Z]{3}-20\d{2})', text)

        if class_match and session_match:
            class_part = class_match.group(1)
            year_part = class_match.group(2)
            session_formatted = session_match.group(1).replace("-20", " ")
            class_val = f"{class_part} {year_part} - {session_formatted}"
        elif class_match:
            class_val = f"{class_match.group(1)} {class_match.group(2)}"
        else:
            class_val = "UNSPECIFIED_CLASS"

        mode_type = "UNSPECIFIED_MODE"
        for keyword_mode in ["PRIVATE", "REGULAR"]:
            if keyword_mode in text.upper():
                mode_type = keyword_mode
                break

        type_type = "UNSPECIFIED_TYPE"
        for keyword_type in ["ATKT", "SUPP", "EXR", "REGULAR", "PRIVATE"]:
            if keyword_type in text.upper():
                type_type = keyword_type
                break

    paper_code = re.search(r'Paper Code[:\s]*([A-Z0-9]+)', text, re.IGNORECASE)
    paper_code_val = _format_paper_code(paper_code.group(1)) if paper_code else "UNSPECIFIED_PAPER_CODE"

    paper_name = re.search(r'Paper Name[:\s]*(.+?)(?:\n|$)', text)
    paper_name_val = paper_name.group(1).strip() if paper_name else "UNSPECIFIED_PAPER_NAME"

    return {
        "class": class_val,
        "mode": mode_type,
        "type": type_type,
        "room_number": "",
        "paper_code": paper_code_val,
        "paper_name": paper_name_val,
    }


def _extract_roll_numbers(text: str) -> list:
    return sorted(set(re.findall(r'\b\d{9}\b', text)))


def _resolve_zip_base_dir(tmpdir: str, expected_subfolder: str | None = None) -> str:
    """Handles zips that wrap contents in a single subfolder."""
    contents = os.listdir(tmpdir)
    if expected_subfolder and expected_subfolder in contents and os.path.isdir(
        os.path.join(tmpdir, expected_subfolder)
    ):
        return os.path.join(tmpdir, expected_subfolder)
    if len(contents) == 1 and os.path.isdir(os.path.join(tmpdir, contents[0])):
        return os.path.join(tmpdir, contents[0])
    return tmpdir


# ---------------------------------------------------------------------------
# Sitting plan PDF import
# ---------------------------------------------------------------------------

def _timetable_stub_exists(center_id: str, paper_code: str, class_val: str) -> bool:
    """Checks for an existing un-scheduled (date/shift still NULL) timetable
    row for this paper, so reprocessing the same PDFs doesn't create dupes."""
    client = db.get_client()
    response = (
        client.table("timetable")
        .select("id")
        .eq("center_id", center_id)
        .eq("paper_code", paper_code)
        .eq("class", class_val)
        .is_("date", "null")
        .is_("shift", "null")
        .limit(1)
        .execute()
    )
    return bool(response.data)


def process_sitting_plan_pdfs(center_id: str, zip_file_buffer) -> tuple[bool, str]:
    """
    Expects a ZIP where each subfolder is named after a paper (e.g. its
    folder name becomes the 'paper_short' / Paper column) and contains one
    or more PDFs of that paper's sitting plan.
    """
    sitting_plan_rows = []
    timetable_stubs = []  # deduped by (paper_code, class) within this run
    seen_stub_keys = set()
    processed_files = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_file_buffer, "r") as zip_ref:
            zip_ref.extractall(tmpdir)

        base_dir = _resolve_zip_base_dir(tmpdir, expected_subfolder="pdf_folder")

        for folder_name in os.listdir(base_dir):
            folder_path = os.path.join(base_dir, folder_name)
            if not os.path.isdir(folder_path):
                continue

            for file in os.listdir(folder_path):
                if not file.lower().endswith(".pdf"):
                    continue

                pdf_path = os.path.join(folder_path, file)
                try:
                    doc = fitz.open(pdf_path)
                    full_text = "\n".join(page.get_text() for page in doc)
                    doc.close()

                    meta = extract_metadata_from_pdf_text(full_text)
                    if meta["paper_code"] == "UNSPECIFIED_PAPER_CODE":
                        meta["paper_code"] = folder_name
                    if meta["paper_name"] == "UNSPECIFIED_PAPER_NAME":
                        meta["paper_name"] = folder_name

                    rolls = _extract_roll_numbers(full_text)

                    # One sitting_plan row per chunk of 10 roll numbers, matching
                    # the original layout (Roll Number 1..10 per room-ish block).
                    for i in range(0, len(rolls), 10):
                        chunk = rolls[i:i + 10]
                        sitting_plan_rows.append({
                            "room_number": meta["room_number"],
                            "paper_code": meta["paper_code"],
                            "roll_numbers": chunk,
                            "dedupe_key": _dedupe_key(
                                chunk, meta["room_number"], meta["class"],
                                meta["mode"], meta["type"]
                            ),
                            "raw_row": {
                                "class": meta["class"],
                                "mode": meta["mode"],
                                "type": meta["type"],
                                "paper": folder_name,
                                "paper_code": meta["paper_code"],
                                "paper_name": meta["paper_name"],
                            },
                        })

                    stub_key = (meta["paper_code"], meta["class"])
                    if stub_key not in seen_stub_keys:
                        seen_stub_keys.add(stub_key)
                        timetable_stubs.append({
                            "paper_code": meta["paper_code"],
                            "paper_name": meta["paper_name"],
                            "paper_short": folder_name,
                            "class": meta["class"],
                            "mode": meta["mode"],
                            "type": meta["type"],
                        })

                    processed_files += 1
                    st.info(f"✔ Processed: {file} ({len(rolls)} unique roll numbers)")

                except Exception as e:
                    st.error(f"❌ Failed to process {file}: {e}")

    if not sitting_plan_rows:
        return False, "No roll numbers extracted from PDFs."

    ok, result = db.upsert(
        "sitting_plan", center_id, sitting_plan_rows,
        on_conflict="center_id,paper_code,dedupe_key",
    )
    if not ok:
        return False, result

    new_stub_count = 0
    for stub in timetable_stubs:
        if not _timetable_stub_exists(center_id, stub["paper_code"], stub["class"]):
            ok, result = db.insert("timetable", center_id, stub)
            if not ok:
                st.warning(f"Could not stage timetable entry for {stub['paper_code']}: {result}")
            else:
                new_stub_count += 1

    return True, (
        f"Processed {processed_files} PDFs, saved {len(sitting_plan_rows)} sitting "
        f"plan rows, staged {new_stub_count} new paper(s) in the timetable "
        f"(assign date/shift to them in Admin \u2192 Update Timetable)."
    )


# ---------------------------------------------------------------------------
# Attestation PDF import
# ---------------------------------------------------------------------------

def _parse_attestation_pdf_text(text: str) -> list:
    students = re.split(r"\n?RollNo\.\:\s*", text)
    students = [s.strip() for s in students if s.strip()]

    student_records = []

    for s in students:
        lines = [line.strip() for line in s.splitlines() if line.strip()]

        def extract_after(label):
            for i, line in enumerate(lines):
                if line.startswith(label):
                    value = line.replace(label, "", 1).strip()
                    if value:
                        return value
                    elif i + 1 < len(lines):
                        return lines[i + 1].strip()
                if label == "Regular/ Backlog:" and line.startswith("Regular/Backlog"):
                    value = line.replace("Regular/Backlog", "", 1).strip()
                    if value:
                        return value
                    elif i + 1 < len(lines):
                        return lines[i + 1].strip()
            return ""

        roll_match = re.match(r"(\d{9})", lines[0]) if lines else None
        roll_no = roll_match.group(1) if roll_match else ""
        if not roll_no:
            continue

        raw_row = {
            "roll_number": roll_no,
            "enrollment_number": extract_after("Enrollment No.:"),
            "session": extract_after("Session:"),
            "regular_backlog": extract_after("Regular/ Backlog:"),
            "name": extract_after("Name:"),
            "fathers_name": extract_after("Father's Name:"),
            "mothers_name": extract_after("Mother's Name:"),
            "gender": extract_after("Gender:"),
            "exam_name": extract_after("Exam Name:"),
            "exam_centre": extract_after("Exam Centre:"),
            "college_name": extract_after("College Nmae:"),
            "address": extract_after("Address:"),
        }

        papers = re.findall(r"([^\n]+?\[\d{5}\][^\n]*)", s)
        raw_row["papers"] = [p.strip() for p in papers[:10]]

        student_records.append({
            "roll_number": roll_no,
            "papers": raw_row["papers"],
            "raw_row": raw_row,
        })

    return student_records


def process_attestation_pdfs(center_id: str, zip_file_buffer) -> tuple[bool, str]:
    all_records = []
    processed_files = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_file_buffer, "r") as zip_ref:
            zip_ref.extractall(tmpdir)

        base_dir = _resolve_zip_base_dir(tmpdir, expected_subfolder="rasa_pdf")

        for filename in os.listdir(base_dir):
            if not filename.lower().endswith(".pdf"):
                continue

            pdf_path = os.path.join(base_dir, filename)
            try:
                doc = fitz.open(pdf_path)
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
                st.info(f"📄 Extracting: {filename}")
                all_records.extend(_parse_attestation_pdf_text(text))
                processed_files += 1
            except Exception as e:
                st.error(f"❌ Failed to process {filename}: {e}")

    if not all_records:
        return False, "No data extracted from attestation PDFs."

    ok, result = db.upsert(
        "attestation_data", center_id, all_records,
        on_conflict="center_id,roll_number",
    )
    if not ok:
        return False, result

    return True, f"Processed {processed_files} attestation PDFs, saved {len(all_records)} student records."


# ---------------------------------------------------------------------------
# Streamlit UI block — drop this into admin_panel.py's "Upload Data Files" section
# ---------------------------------------------------------------------------

def render_upload_ui(center_id: str):
    st.subheader("📤 Upload Data Files")

    st.markdown("**Sitting Plan PDFs**")
    sitting_zip = st.file_uploader(
        "Upload Sitting Plan PDFs (ZIP, one subfolder per paper)",
        type=["zip"], key="upload_sitting_plan_zip",
    )
    if sitting_zip and st.button("Process Sitting Plan ZIP"):
        with st.spinner("Processing sitting plan PDFs..."):
            ok, message = process_sitting_plan_pdfs(center_id, sitting_zip)
        (st.success if ok else st.error)(message)

    st.markdown("---")
    st.markdown("**Attestation PDFs**")
    attestation_zip = st.file_uploader(
        "Upload Attestation PDFs (ZIP)",
        type=["zip"], key="upload_attestation_zip",
    )
    if attestation_zip and st.button("Process Attestation ZIP"):
        with st.spinner("Processing attestation PDFs..."):
            ok, message = process_attestation_pdfs(center_id, attestation_zip)
        (st.success if ok else st.error)(message)

    st.markdown("---")
    st.markdown("**Room Capacity Sheet** (used by Auto-Propose Seating)")
    st.caption(
        "Columns expected: room no, Each table capacity, capacity (N), Type of "
        "seats, can accommodate 2 students of same subjects, can accommodate 2 "
        "students of different subjects, capacity in easy condition, capacity "
        "in normal condition, capacity in tight condition."
    )
    capacity_file = st.file_uploader(
        "Upload Room Capacity (CSV or Excel)",
        type=["csv", "xlsx", "xls"], key="upload_room_capacity_file",
    )
    if capacity_file and st.button("Process Room Capacity File"):
        with st.spinner("Processing room capacity sheet..."):
            ok, message = process_room_capacity_file(center_id, capacity_file)
        (st.success if ok else st.error)(message)


# ---------------------------------------------------------------------------
# Room capacity sheet import
# ---------------------------------------------------------------------------

_CAPACITY_COLUMN_ALIASES = {
    "room_no": ["room no", "room number", "room"],
    "each_table_capacity": ["each table capacity", "table capacity"],
    "capacity_n": ["capacity (n)", "capacity(n)", "capacity n"],
    "seat_type": ["type of seats", "seat type"],
    "accommodate_2_same": ["can accommodate 2 students of same subjects", "accommodate 2 same", "same subject"],
    "accommodate_2_diff": ["can accommodate 2 students of different subjects", "accommodate 2 different", "different subject"],
    "capacity_easy": ["capacity in easy condition", "easy condition", "easy"],
    "capacity_normal": ["capacity in normal condition", "normal condition", "normal"],
    "capacity_tight": ["capacity in tight condition (with different subjects only)", "capacity in tight condition", "tight condition", "tight"],
}


def _match_capacity_columns(columns: list) -> dict:
    """Maps a DataFrame's actual column names to our canonical field names,
    tolerant of case/whitespace/minor wording differences."""
    normalized = {c: re.sub(r"\s+", " ", str(c).strip().lower()) for c in columns}
    mapping = {}
    for field, aliases in _CAPACITY_COLUMN_ALIASES.items():
        for col, norm in normalized.items():
            if any(norm == a or a in norm for a in aliases):
                mapping[field] = col
                break
    return mapping


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("yes", "y", "true", "1")


def process_room_capacity_file(center_id: str, uploaded_file) -> tuple[bool, str]:
    import pandas as pd

    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        return False, f"Could not read the file: {e}"

    col_map = _match_capacity_columns(list(df.columns))
    required = ["room_no", "each_table_capacity", "capacity_n", "seat_type",
                "capacity_easy", "capacity_normal", "capacity_tight"]
    missing = [f for f in required if f not in col_map]
    if missing:
        return False, f"Could not find columns for: {', '.join(missing)}. Check the column headers match what's expected."

    rows = []
    for _, row in df.iterrows():
        try:
            rows.append({
                "room_no": str(row[col_map["room_no"]]).strip(),
                "each_table_capacity": int(row[col_map["each_table_capacity"]]),
                "capacity_n": int(row[col_map["capacity_n"]]),
                "seat_type": str(row[col_map["seat_type"]]).strip(),
                "accommodate_2_same": _to_bool(row[col_map["accommodate_2_same"]]) if "accommodate_2_same" in col_map else False,
                "accommodate_2_diff": _to_bool(row[col_map["accommodate_2_diff"]]) if "accommodate_2_diff" in col_map else False,
                "capacity_easy": int(row[col_map["capacity_easy"]]),
                "capacity_normal": int(row[col_map["capacity_normal"]]),
                "capacity_tight": int(row[col_map["capacity_tight"]]),
            })
        except (ValueError, TypeError):
            continue

    if not rows:
        return False, "No valid room rows found in the file."

    ok, result = db.upsert("room_capacities", center_id, rows, on_conflict="center_id,room_no")
    if not ok:
        return False, result

    return True, f"Imported capacity data for {len(rows)} room(s)."
