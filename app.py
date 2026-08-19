"""
app.py — Entrypoint. Run with: streamlit run app.py

Top-level flow mirrors the original: pick a module (Student View / Admin
Panel / Centre Superintendent Panel). Student View just needs a center
picked (no login). Admin/CS need to log in via auth.py, after which
center_id is scoped for the rest of the session via st.session_state.
"""

import streamlit as st

import auth
import admin_panel
import cs_panel
import student_portal
import owner_panel


st.set_page_config(page_title="Exam Management System", page_icon="🎓", layout="wide")


def render_sidebar_session_info():
    with st.sidebar:
        st.write(f"**Center:** {st.session_state.get('center_name', '')}")
        st.write(f"**Role:** {st.session_state.get('role', '').replace('_', ' ').title()}")
        if st.button("Logout"):
            auth.logout()
            st.rerun()


def main():
    st.title("🎓 Exam Management System")

    menu = st.radio(
        "Select Module",
        ["Student View", "Admin Panel", "Centre Superintendent Panel", "Owner Panel"],
        key="main_menu_radio", horizontal=True,
    )
    st.markdown("---")

    if menu == "Student View":
        # Public — no login, just needs a center selected for this session.
        center_id = auth.center_picker(key_prefix="student_view")
        if center_id:
            student_portal.render(center_id)

    elif menu == "Admin Panel":
        if auth.is_logged_in(role="admin"):
            render_sidebar_session_info()
            admin_panel.render(auth.current_center_id())
        else:
            st.subheader("🔐 Admin Login")
            if auth.admin_login():
                st.rerun()

    elif menu == "Centre Superintendent Panel":
        if auth.is_logged_in(role="cs"):
            render_sidebar_session_info()
            cs_panel.render(auth.current_center_id())
        else:
            st.subheader("🔐 Centre Superintendent Login")
            if auth.cs_login():
                st.rerun()

    elif menu == "Owner Panel":
        if auth.is_logged_in(role="owner"):
            render_sidebar_session_info()
            owner_panel.render()
        else:
            st.subheader("🔐 Owner Login")
            if auth.owner_login():
                st.rerun()


if __name__ == "__main__":
    main()
