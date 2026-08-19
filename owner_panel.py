"""
owner_panel.py — Super Admin / Owner panel. Not tenant-scoped: this is the
only module allowed to query exam_centers and other tables WITHOUT a
center_id filter, since the owner manages every center. Every other module
in this app goes through db.py's tenant-scoped functions — this file
intentionally does not, and that's the one exception to that rule.
"""

import streamlit as st

import auth
import db


# ---------------------------------------------------------------------------
# Data helpers (deliberately cross-tenant — owner only)
# ---------------------------------------------------------------------------

def _all_centers():
    client = db.get_client()
    response = client.table("exam_centers").select("*").order("center_name").execute()
    return response.data or []


def _center_stats(center_id: str) -> dict:
    client = db.get_client()
    team = client.table("exam_team_members").select("id", count="exact").eq("center_id", center_id).execute()
    papers = client.table("timetable").select("id", count="exact").eq("center_id", center_id).execute()
    seats = client.table("assigned_seats").select("roll_number").eq("center_id", center_id).execute()
    distinct_students = len({r["roll_number"] for r in (seats.data or [])})
    return {
        "Team Members": team.count or 0,
        "Papers Scheduled": papers.count or 0,
        "Students with Seats Assigned": distinct_students,
    }


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

def render_center_directory():
    st.subheader("🏫 All Exam Centers")
    centers = _all_centers()

    if not centers:
        st.info("No centers yet. Add one below.")
        return

    for center in centers:
        status = "🟢 Active" if center["is_active"] else "🔴 Inactive"
        with st.expander(f"{center['center_name']} — {center['university_name']} ({status})"):
            st.write(f"**Center Code:** {center['center_code']}")
            st.write(f"**Address:** {center.get('address') or '—'}")
            st.write(f"**Admin Username:** {center['admin_username']}")
            st.write(f"**CS Username:** {center['cs_username']}")

            stats = _center_stats(center["id"])
            cols = st.columns(len(stats))
            for col, (label, value) in zip(cols, stats.items()):
                col.metric(label, value)

            st.markdown("---")
            col1, col2 = st.columns(2)

            with col1:
                toggle_label = "Deactivate Center" if center["is_active"] else "Activate Center"
                if st.button(toggle_label, key=f"toggle_{center['id']}"):
                    client = db.get_client()
                    client.table("exam_centers").update({"is_active": not center["is_active"]}).eq("id", center["id"]).execute()
                    st.success(f"{center['center_name']} {'deactivated' if center['is_active'] else 'activated'}.")
                    st.rerun()

            with col2:
                if st.button("Reset Passwords", key=f"reset_btn_{center['id']}"):
                    st.session_state[f"show_reset_{center['id']}"] = True

            if st.session_state.get(f"show_reset_{center['id']}"):
                new_admin_pw = st.text_input("New Admin Password", type="password", key=f"new_admin_pw_{center['id']}")
                new_cs_pw = st.text_input("New CS Password", type="password", key=f"new_cs_pw_{center['id']}")
                if st.button("Confirm Reset", key=f"confirm_reset_{center['id']}"):
                    updates = {}
                    if new_admin_pw:
                        updates["admin_password_hash"] = auth.hash_password(new_admin_pw)
                    if new_cs_pw:
                        updates["cs_password_hash"] = auth.hash_password(new_cs_pw)
                    if updates:
                        client = db.get_client()
                        client.table("exam_centers").update(updates).eq("id", center["id"]).execute()
                        st.success("Password(s) updated.")
                        st.session_state[f"show_reset_{center['id']}"] = False
                        st.rerun()
                    else:
                        st.warning("Enter at least one new password.")


def render_add_center():
    st.subheader("➕ Add New Exam Center")

    with st.form("add_center_form"):
        university_name = st.text_input("University Name", value="Jiwaji University")
        center_name = st.text_input("Center Name")
        center_code = st.text_input("Center Code (short, unique)")
        address = st.text_input("Address")
        admin_username = st.text_input("Admin Username", value="admin")
        admin_password = st.text_input("Admin Password", type="password")
        cs_username = st.text_input("CS Username", value="cs_admin")
        cs_password = st.text_input("CS Password", type="password")

        submitted = st.form_submit_button("Create Center")

    if submitted:
        if not all([center_name, center_code, admin_password, cs_password]):
            st.error("Center Name, Center Code, Admin Password, and CS Password are required.")
            return

        row = {
            "university_name": university_name,
            "center_name": center_name,
            "center_code": center_code,
            "address": address,
            "admin_username": admin_username,
            "admin_password_hash": auth.hash_password(admin_password),
            "cs_username": cs_username,
            "cs_password_hash": auth.hash_password(cs_password),
            "is_active": True,
        }

        try:
            client = db.get_client()
            client.table("exam_centers").insert(row).execute()
            st.success(f"Center '{center_name}' created. Share the admin/CS credentials with them.")
            auth.list_active_centers.clear()  # bust the cached center picker list
        except Exception as e:
            st.error(f"Failed to create center: {e}")


def render_overview():
    st.subheader("📊 Cross-Center Overview")
    centers = _all_centers()
    active = [c for c in centers if c["is_active"]]

    col1, col2 = st.columns(2)
    col1.metric("Total Centers", len(centers))
    col2.metric("Active Centers", len(active))

    if centers:
        rows = []
        for c in centers:
            stats = _center_stats(c["id"])
            rows.append({
                "Center": c["center_name"], "University": c["university_name"],
                "Status": "Active" if c["is_active"] else "Inactive", **stats,
            })
        st.dataframe(rows, use_container_width=True)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def render():
    st.header("👑 Owner Panel — All Centers")

    option = st.radio("Select:", ["Overview", "Center Directory", "Add New Center"], key="owner_panel_radio", horizontal=True)
    st.markdown("---")

    if option == "Overview":
        render_overview()
    elif option == "Center Directory":
        render_center_directory()
    elif option == "Add New Center":
        render_add_center()
