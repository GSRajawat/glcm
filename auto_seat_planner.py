"""
auto_seat_planner.py — Automatic seat-assignment proposal engine.

Given a date/shift, looks at how many students actually need seating
across all scheduled papers, compares that against the center's uploaded
room capacities (room_capacities table, from data_ingestion.py's Room
Capacity Sheet upload), and picks the LEAST crowded tier that can fit
everyone:

  - "easy"   — 1 student per table everywhere (most spacious)
  - "normal" — 2 students per table allowed, but only the SAME subject
               (so a room used at this tier is dedicated to one paper)
  - "tight"  — 2 students per table allowed even with DIFFERENT subjects
               (mixing papers on shared desks — last resort)

The whole shift uses ONE tier uniformly (once doubling is needed
anywhere, it's simpler and more predictable to double wherever it helps
rather than mix comfort levels room-by-room). This is a genuinely
important design choice — say so if you'd rather it decide per-room.

Bin-packing is best-fit-decreasing: biggest papers placed first, each
into the smallest room that can still fully hold it (minimizes wasted
capacity), spilling into more rooms only if one room isn't enough. At
"normal" tier a room gets locked to the first paper placed in it (same
subject only); at "easy" and "tight" tiers rooms can freely mix papers
(harmless at "easy" since nobody shares a table; intentional at "tight"
since mixing is the whole point).

This only produces a PROPOSAL — a list of dicts the caller reviews/edits
before writing anything to assigned_seats. Nothing here touches the
database except reading room_capacities/timetable/sitting_plan/
assigned_seats.
"""

from collections import defaultdict

import streamlit as st

import db


TIERS = ["easy", "normal", "tight"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_rooms(center_id: str) -> list:
    ok, rows = db.select("room_capacities", center_id, order="room_no")
    return rows if ok else []


def _get_students_for_paper(center_id: str, paper_code: str) -> dict:
    """Returns {roll_number: {"class":..., "mode":..., "type":...}} — a
    paper_code can span several cohorts (Regular/EX/ATKT) ingested from
    separate sitting-plan PDFs, each carrying its own class/mode/type in
    raw_row. Mirrors seat_assignment.py's helper of the same purpose."""
    ok, rows = db.select("sitting_plan", center_id, {"paper_code": paper_code})
    if not ok:
        return {}
    students = {}
    for row in rows:
        raw = row.get("raw_row") or {}
        info = {"class": raw.get("class", ""), "mode": raw.get("mode", ""), "type": raw.get("type", "")}
        for roll in (row.get("roll_numbers") or []):
            students[roll] = info
    return students


def get_shift_demand(center_id: str, date_iso: str, shift: str, selected_classes: list | None = None) -> list:
    """Returns [{paper_code, paper_name, class, rolls: [roll_number, ...]}, ...]
    for students NOT YET in assigned_seats for this date/shift/paper — so
    this complements any manual assignments already made, rather than
    overwriting them."""
    ok, tt_rows = db.select("timetable", center_id, {"date": date_iso, "shift": shift})
    tt_rows = tt_rows if ok else []
    if selected_classes:
        tt_rows = [t for t in tt_rows if t.get("class") in selected_classes]

    papers = []
    for tt in tt_rows:
        ok, sp_rows = db.select("sitting_plan", center_id, {"paper_code": tt["paper_code"]})
        all_rolls = set()
        if ok:
            for row in sp_rows:
                all_rolls.update(row.get("roll_numbers") or [])

        ok2, existing = db.select("assigned_seats", center_id, {
            "paper_code": tt["paper_code"], "date": date_iso, "shift": shift,
        })
        already_assigned = {r["roll_number"] for r in existing} if ok2 else set()

        remaining_rolls = sorted(all_rolls - already_assigned)
        if remaining_rolls:
            papers.append({
                "paper_code": tt["paper_code"],
                "paper_name": tt.get("paper_name") or "",
                "class": tt.get("class") or "",
                "rolls": remaining_rolls,
            })

    return papers


# ---------------------------------------------------------------------------
# Seat-label generation
# ---------------------------------------------------------------------------

def generate_seats_for_tier(room: dict, tier: str, count: int) -> list:
    """Produces `count` seat labels for a room at a given tier, choosing
    plain numbering vs A/B pairing based on the room's own numbers rather
    than parsing its seat_type text — works for any room shape."""
    capacity_n = room["capacity_n"]
    tier_capacity = room[f"capacity_{tier}"]
    each_table_capacity = room.get("each_table_capacity", 1)

    seats = []
    if each_table_capacity >= 2:
        if tier_capacity <= capacity_n:
            seats = [f"{i}A" for i in range(1, capacity_n + 1)]
        else:
            for i in range(1, capacity_n + 1):
                seats.append(f"{i}A")
                seats.append(f"{i}B")
    else:
        if tier_capacity <= capacity_n:
            seats = [str(i) for i in range(1, capacity_n + 1)]
        else:
            for i in range(1, capacity_n + 1):
                seats.append(f"{i}A")
                seats.append(f"{i}B")

    return seats[:count]


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------

def choose_tier(total_demand: int, rooms: list) -> tuple:
    """Returns (tier_name, total_capacity_at_that_tier)."""
    for tier in TIERS:
        total = sum(r[f"capacity_{tier}"] for r in rooms)
        if total_demand <= total:
            return tier, total
    # Even "tight" isn't enough — return it anyway so the caller can show
    # a clear "N students could not be seated" warning.
    return "tight", sum(r["capacity_tight"] for r in rooms)


# ---------------------------------------------------------------------------
# Bin-packing
# ---------------------------------------------------------------------------

def _bin_pack_single_subject_tier(papers: list, rooms: list, tier: str) -> tuple:
    """Used for 'easy' and 'normal'. At 'normal', a room gets locked to the
    first paper placed in it (same-subject-only rule) — at 'easy' rooms
    may freely mix papers since no one shares a table there anyway.

    Returns (assignments, leftover_papers) — leftover_papers holds any
    rolls that couldn't be placed (ran out of room at this tier).
    """
    lock_rooms = (tier == "normal")
    room_state = [
        {**r, "remaining": r[f"capacity_{tier}"], "next_seat_idx": 0,
         "seats": generate_seats_for_tier(r, tier, r[f"capacity_{tier}"]),
         "locked_to": None}
        for r in rooms
    ]

    papers_sorted = sorted((dict(p, rolls=list(p["rolls"])) for p in papers),
                            key=lambda p: len(p["rolls"]), reverse=True)
    assignments = []

    for paper in papers_sorted:
        while paper["rolls"]:
            if lock_rooms:
                candidates = [r for r in room_state if r["remaining"] > 0
                              and (r["locked_to"] is None or r["locked_to"] == paper["paper_code"])]
            else:
                candidates = [r for r in room_state if r["remaining"] > 0]

            if not candidates:
                break

            remaining_count = len(paper["rolls"])
            fitting = [r for r in candidates if r["remaining"] >= remaining_count]
            room = min(fitting, key=lambda r: r["remaining"]) if fitting else max(candidates, key=lambda r: r["remaining"])

            if lock_rooms and room["locked_to"] is None:
                room["locked_to"] = paper["paper_code"]

            take = min(remaining_count, room["remaining"])
            for _ in range(take):
                roll = paper["rolls"].pop(0)
                seat = room["seats"][room["next_seat_idx"]]
                room["next_seat_idx"] += 1
                room["remaining"] -= 1
                assignments.append({
                    "roll_number": roll, "paper_code": paper["paper_code"],
                    "paper_name": paper["paper_name"], "room_number": room["room_no"],
                    "seat_number": seat,
                })

    leftover_papers = [p for p in papers_sorted if p["rolls"]]
    return assignments, leftover_papers


def _bin_pack_mixed_tier(papers: list, rooms: list) -> tuple:
    """Used for 'tight' — deliberately interleaves different papers so
    adjacent seats (same table) end up with different subjects."""
    queues = [list(p["rolls"]) for p in papers]
    meta = [(p["paper_code"], p["paper_name"]) for p in papers]

    merged = []
    while any(queues):
        for i, q in enumerate(queues):
            if q:
                merged.append((meta[i][0], meta[i][1], q.pop(0)))

    room_state = [
        {**r, "remaining": r["capacity_tight"], "next_seat_idx": 0,
         "seats": generate_seats_for_tier(r, "tight", r["capacity_tight"])}
        for r in rooms
    ]

    assignments = []
    idx = 0
    for room in room_state:
        while room["remaining"] > 0 and idx < len(merged):
            paper_code, paper_name, roll = merged[idx]
            seat = room["seats"][room["next_seat_idx"]]
            room["next_seat_idx"] += 1
            room["remaining"] -= 1
            assignments.append({
                "roll_number": roll, "paper_code": paper_code,
                "paper_name": paper_name, "room_number": room["room_no"],
                "seat_number": seat,
            })
            idx += 1

    unplaced = merged[idx:]
    return assignments, unplaced


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def propose_seating(center_id: str, date_iso: str, shift: str, selected_classes: list | None = None) -> dict:
    """
    Returns:
        {
            "tier_used": "easy" | "normal" | "tight",
            "assignments": [{roll_number, paper_code, paper_name, room_number, seat_number, date, shift}, ...],
            "unplaced_count": int,
            "unplaced_rolls": [roll_number, ...],
            "total_demand": int,
        }
    """
    rooms = load_rooms(center_id)
    papers = get_shift_demand(center_id, date_iso, shift, selected_classes)
    total_demand = sum(len(p["rolls"]) for p in papers)

    if not rooms:
        return {"tier_used": None, "assignments": [], "unplaced_count": total_demand,
                "unplaced_rolls": [r for p in papers for r in p["rolls"]], "total_demand": total_demand,
                "error": "No room capacity data uploaded for this center yet."}

    if total_demand == 0:
        return {"tier_used": None, "assignments": [], "unplaced_count": 0,
                "unplaced_rolls": [], "total_demand": 0}

    tier, _ = choose_tier(total_demand, rooms)

    if tier in ("easy", "normal"):
        assignments, leftover_papers = _bin_pack_single_subject_tier(papers, rooms, tier)
        leftover_rolls = [r for p in leftover_papers for r in p["rolls"]]

        if leftover_rolls:
            # Ran out of capacity at this tier for some papers even though
            # the shift-wide total should have fit — escalate just the
            # leftover into tight-tier mixing rather than leaving them unplaced.
            leftover_paper_map = defaultdict(list)
            for p in leftover_papers:
                leftover_paper_map[(p["paper_code"], p["paper_name"])].extend(p["rolls"])
            remaining_papers = [{"paper_code": k[0], "paper_name": k[1], "rolls": v} for k, v in leftover_paper_map.items()]

            used_room_nos = {a["room_number"] for a in assignments}
            fresh_rooms = [r for r in rooms if r["room_no"] not in used_room_nos]
            tight_assignments, unplaced = _bin_pack_mixed_tier(remaining_papers, fresh_rooms)
            assignments.extend(tight_assignments)
            leftover_rolls = [u[2] for u in unplaced]
    else:
        assignments, unplaced = _bin_pack_mixed_tier(papers, rooms)
        leftover_rolls = [u[2] for u in unplaced]

    for a in assignments:
        a["date"] = date_iso
        a["shift"] = shift

    # Enrich with per-student class/mode/type — these can legitimately
    # differ within the same paper_code (Regular/EX/ATKT cohorts), so this
    # must come from sitting_plan per-roll data, not a single shared value.
    info_cache = {}
    for a in assignments:
        paper_code = a["paper_code"]
        if paper_code not in info_cache:
            info_cache[paper_code] = _get_students_for_paper(center_id, paper_code)
        info = info_cache[paper_code].get(a["roll_number"], {})
        a["class"] = info.get("class", "")
        a["mode"] = info.get("mode", "")
        a["type"] = info.get("type", "")

    return {
        "tier_used": tier,
        "assignments": assignments,
        "unplaced_count": len(leftover_rolls),
        "unplaced_rolls": leftover_rolls,
        "total_demand": total_demand,
    }


# ---------------------------------------------------------------------------
# Streamlit UI — propose, review/edit, confirm
# ---------------------------------------------------------------------------

TIER_LABELS = {
    "easy": "Easy (1 student per table)",
    "normal": "Normal (2 per table, same subject only)",
    "tight": "Tight (2 per table, different subjects mixed)",
}


def render(center_id: str):
    st.subheader("🤖 Auto-Propose Seating")
    st.info(
        "Generates a seat proposal for an entire shift at once, using your uploaded "
        "Room Capacity Sheet (Admin \u2192 Upload Data Files). It's a draft — review and "
        "edit the table below before confirming. Nothing is saved to the real seating "
        "records until you click Confirm & Save."
    )

    rooms = load_rooms(center_id)
    if not rooms:
        st.warning("No room capacity data uploaded yet. Upload it under Admin \u2192 Upload Data Files first.")
        return

    ok, tt_rows = db.select("timetable", center_id)
    scheduled = [r for r in tt_rows if r.get("date") and r.get("shift")] if ok else []
    if not scheduled:
        st.warning("No papers have a date/shift assigned yet. Use Update Timetable Details first.")
        return

    dates = sorted({r["date"] for r in scheduled})
    sel_date = st.selectbox("Select date", dates, key="auto_seat_date")
    shift_options = sorted({r["shift"] for r in scheduled if r["date"] == sel_date})
    sel_shift = st.selectbox("Select shift", shift_options, key="auto_seat_shift")

    classes_in_session = sorted({r.get("class") for r in scheduled if r["date"] == sel_date and r["shift"] == sel_shift and r.get("class")})
    selected_classes = st.multiselect(
        "Restrict to class(es) (leave empty for all papers this shift)",
        classes_in_session, key="auto_seat_classes",
    )

    state_key = f"auto_seat_proposal_{sel_date}_{sel_shift}"

    if st.button("Generate Proposal"):
        with st.spinner("Working out the best room fit..."):
            result = propose_seating(center_id, sel_date, sel_shift, selected_classes or None)
        st.session_state[state_key] = result

    result = st.session_state.get(state_key)
    if not result:
        return

    if result.get("error"):
        st.error(result["error"])
        return

    if result["total_demand"] == 0:
        st.success("Every student for this shift already has a seat assigned — nothing to propose.")
        return

    st.markdown("---")
    tier = result["tier_used"]
    st.write(f"**Tier used:** {TIER_LABELS.get(tier, tier)}")
    st.write(f"**Total students needing seats:** {result['total_demand']}  |  **Proposed:** {len(result['assignments'])}  |  **Unplaced:** {result['unplaced_count']}")

    if result["unplaced_count"] > 0:
        st.error(
            f"{result['unplaced_count']} student(s) could not be seated even at the tightest "
            f"tier — total room capacity isn't enough for this shift. Unplaced roll numbers: "
            f"{', '.join(result['unplaced_rolls'][:20])}{'...' if result['unplaced_count'] > 20 else ''}"
        )

    st.markdown("**Review and edit the proposal below** (you can change Room Number or Seat Number in any row):")
    edited = st.data_editor(
        result["assignments"],
        column_config={
            "roll_number": st.column_config.TextColumn("Roll Number", disabled=True),
            "paper_code": st.column_config.TextColumn("Paper Code", disabled=True),
            "paper_name": st.column_config.TextColumn("Paper Name", disabled=True),
            "class": st.column_config.TextColumn("Class", disabled=True),
            "mode": st.column_config.TextColumn("Mode", disabled=True),
            "type": st.column_config.TextColumn("Type", disabled=True),
            "room_number": st.column_config.TextColumn("Room Number"),
            "seat_number": st.column_config.TextColumn("Seat Number"),
            "date": st.column_config.TextColumn("Date", disabled=True),
            "shift": st.column_config.TextColumn("Shift", disabled=True),
        },
        use_container_width=True, height=400, key=f"editor_{state_key}",
    )

    st.markdown("---")
    if st.button("✅ Confirm & Save to Assigned Seats", type="primary"):
        rows = [{
            "roll_number": r["roll_number"], "paper_code": r["paper_code"], "paper_name": r["paper_name"],
            "class": r.get("class", ""), "mode": r.get("mode", ""), "type": r.get("type", ""),
            "room_number": r["room_number"], "seat_number": r["seat_number"],
            "date": r["date"], "shift": r["shift"],
        } for r in edited]

        ok, save_result = db.upsert(
            "assigned_seats", center_id, rows,
            on_conflict="center_id,roll_number,date,shift,paper_code",
        )
        if ok:
            st.success(f"Saved {len(rows)} seat assignments. You can now generate Room Charts for this shift.")
            del st.session_state[state_key]
        else:
            st.error(save_result)
