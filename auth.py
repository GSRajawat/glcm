"""
auth.py — Center selection + admin/CS login.

Replaces the old hardcoded admin/admin123 and cs_admin/cs_pass123 checks.
Each exam_centers row carries its own bcrypt-hashed admin and CS passwords.
A successful login sets center_id (and role) in st.session_state, which
every other module then passes into db.py calls.
"""

import bcrypt
import streamlit as st

import db


# ---------------------------------------------------------------------------
# Password hashing helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Center directory
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def list_active_centers():
    """Returns [{"id":..., "center_name":..., "center_code":..., "university_name":...}]"""
    client = db.get_client()
    response = (
        client.table("exam_centers")
        .select("id, center_name, center_code, university_name")
        .eq("is_active", True)
        .order("center_name")
        .execute()
    )
    return response.data or []


def _get_center_row(center_id: str):
    client = db.get_client()
    response = (
        client.table("exam_centers")
        .select("*")
        .eq("id", center_id)
        .eq("is_active", True)
        .single()
        .execute()
    )
    return response.data


# ---------------------------------------------------------------------------
# Login flows
# ---------------------------------------------------------------------------

def center_picker(key_prefix: str = "center"):
    """
    Renders a center dropdown. Returns the selected center's id, or None
    if no centers exist yet.
    """
    centers = list_active_centers()
    if not centers:
        st.warning("No exam centers are set up yet. Ask your administrator to add one.")
        return None

    labels = [f"{c['center_name']} ({c['university_name']})" for c in centers]
    idx = st.selectbox(
        "Select Exam Center", range(len(centers)),
        format_func=lambda i: labels[i], key=f"{key_prefix}_select"
    )
    return centers[idx]["id"]


def admin_login() -> bool:
    """
    Renders center + admin credential inputs. On success, sets
    st.session_state['center_id'], ['center_name'], ['role'] = 'admin'
    and returns True.
    """
    center_id = center_picker(key_prefix="admin")
    if not center_id:
        return False

    username = st.text_input("Admin Username", key="admin_username_input")
    password = st.text_input("Admin Password", type="password", key="admin_password_input")

    if not st.button("Login as Admin"):
        return False

    center = _get_center_row(center_id)
    if not center:
        st.error("Center not found or inactive.")
        return False

    if username == center["admin_username"] and verify_password(password, center["admin_password_hash"]):
        st.session_state["center_id"] = center["id"]
        st.session_state["center_name"] = center["center_name"]
        st.session_state["role"] = "admin"
        return True

    st.error("Invalid admin credentials for this center.")
    return False


def cs_login() -> bool:
    """
    Renders center + CS credential inputs. On success, sets
    st.session_state['center_id'], ['center_name'], ['role'] = 'cs'
    and returns True.
    """
    center_id = center_picker(key_prefix="cs")
    if not center_id:
        return False

    username = st.text_input("CS Username", key="cs_username_input")
    password = st.text_input("CS Password", type="password", key="cs_password_input")

    if not st.button("Login as Centre Superintendent"):
        return False

    center = _get_center_row(center_id)
    if not center:
        st.error("Center not found or inactive.")
        return False

    if username == center["cs_username"] and verify_password(password, center["cs_password_hash"]):
        st.session_state["center_id"] = center["id"]
        st.session_state["center_name"] = center["center_name"]
        st.session_state["role"] = "cs"
        return True

    st.error("Invalid Centre Superintendent credentials for this center.")
    return False


def owner_login() -> bool:
    """
    Super Admin / Owner login — you, across all centers. Credentials come
    from st.secrets (see generate_owner_credentials.py), NOT from any
    Supabase table, since this role isn't tenant-scoped at all.
    """
    username = st.text_input("Owner Username", key="owner_username_input")
    password = st.text_input("Owner Password", type="password", key="owner_password_input")

    if not st.button("Login as Owner"):
        return False

    try:
        owner_username = st.secrets["owner"]["username"]
        owner_password_hash = st.secrets["owner"]["password_hash"]
    except KeyError:
        st.error(
            "Owner credentials not configured. Add an [owner] section with "
            "username and password_hash to secrets.toml (see generate_owner_credentials.py)."
        )
        return False

    if username == owner_username and verify_password(password, owner_password_hash):
        st.session_state["role"] = "owner"
        st.session_state["center_id"] = None
        st.session_state["center_name"] = "All Centers"
        return True

    st.error("Invalid owner credentials.")
    return False


def logout():
    for key in ("center_id", "center_name", "role"):
        st.session_state.pop(key, None)


def current_center_id() -> str | None:
    return st.session_state.get("center_id")


def is_logged_in(role: str | None = None) -> bool:
    if "role" not in st.session_state:
        return False
    if role:
        return st.session_state.get("role") == role
    return True
