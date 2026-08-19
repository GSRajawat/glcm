"""
db.py — Tenant-scoped Supabase CRUD layer.

Every table in the schema (see exam-management-schema.sql) carries a
center_id column. This module is the ONLY place that talks to Supabase —
every other module goes through these functions instead of calling
supabase.table(...) directly, so tenant scoping can never be forgotten
in a feature module.

The app connects with the SERVICE ROLE key (RLS is a backstop only, per
our earlier decision), so scoping by center_id is enforced here in code,
not by the database.
"""

import streamlit as st
from supabase import create_client, Client


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@st.cache_resource
def get_client() -> Client:
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except KeyError:
        st.error(
            "Supabase secrets not found. Please configure `supabase.url` "
            "and `supabase.key` in your secrets.toml file."
        )
        st.stop()
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Generic tenant-scoped CRUD
# ---------------------------------------------------------------------------

def select(table: str, center_id: str, filters: dict | None = None,
           columns: str = "*", order: str | None = None,
           ascending: bool = True, limit: int | None = None):
    """
    Select rows from `table` scoped to `center_id`, with optional equality
    filters, e.g. select("timetable", cid, {"date": "2026-08-06"}).

    Returns (success: bool, data: list[dict] | error_message: str)
    """
    try:
        client = get_client()
        query = client.table(table).select(columns).eq("center_id", center_id)

        if filters:
            for col, val in filters.items():
                query = query.eq(col, val)

        if order:
            query = query.order(order, desc=not ascending)

        if limit:
            query = query.limit(limit)

        response = query.execute()
        return True, response.data or []

    except Exception as e:
        return False, f"Error reading from {table}: {e}"


def get_center_info(center_id: str):
    """
    Special case: exam_centers is the tenant table itself (identified by
    its own id, not a center_id column), so it can't go through the
    tenant-scoped select() above. Used for report headers that need the
    center's own name/code/university.

    Returns (success: bool, data: dict | error_message: str)
    """
    try:
        client = get_client()
        response = (
            client.table("exam_centers")
            .select("university_name, center_name, center_code")
            .eq("id", center_id)
            .single()
            .execute()
        )
        return True, response.data or {}
    except Exception as e:
        return False, f"Error reading center info: {e}"


def insert(table: str, center_id: str, rows: dict | list[dict]):
    """
    Insert one row (dict) or many rows (list of dicts) into `table`.
    center_id is injected automatically into every row — callers never
    need to (and should not) set it themselves.

    Returns (success: bool, data: list[dict] | error_message: str)
    """
    try:
        client = get_client()

        if isinstance(rows, dict):
            rows = [rows]

        rows_with_tenant = [{**row, "center_id": center_id} for row in rows]

        response = client.table(table).insert(rows_with_tenant).execute()
        return True, response.data or []

    except Exception as e:
        return False, f"Error inserting into {table}: {e}"


def upsert(table: str, center_id: str, rows: dict | list[dict],
           on_conflict: str):
    """
    Insert or update rows, keyed on `on_conflict` (a comma-separated list
    of column names matching a unique constraint, e.g.
    "center_id,date,shift,paper_code"). center_id is injected automatically.

    Returns (success: bool, data: list[dict] | error_message: str)
    """
    try:
        client = get_client()

        if isinstance(rows, dict):
            rows = [rows]

        rows_with_tenant = [{**row, "center_id": center_id} for row in rows]

        response = (
            client.table(table)
            .upsert(rows_with_tenant, on_conflict=on_conflict)
            .execute()
        )
        return True, response.data or []

    except Exception as e:
        return False, f"Error upserting into {table}: {e}"


def update(table: str, center_id: str, match: dict, data: dict):
    """
    Update rows in `table` matching `match` (equality filters), scoped to
    center_id, with the given `data`. `match` should be specific enough to
    hit the intended row(s) — e.g. {"date": "2026-08-06", "shift": "Morning"}.

    Returns (success: bool, data: list[dict] | error_message: str)
    """
    try:
        client = get_client()
        query = client.table(table).update(data).eq("center_id", center_id)

        for col, val in match.items():
            query = query.eq(col, val)

        response = query.execute()
        return True, response.data or []

    except Exception as e:
        return False, f"Error updating {table}: {e}"


def delete(table: str, center_id: str, match: dict):
    """
    Delete rows in `table` matching `match` (equality filters), scoped to
    center_id. Refuses to run if `match` is empty, to prevent an accidental
    full-tenant wipe.

    Returns (success: bool, data: list[dict] | error_message: str)
    """
    if not match:
        return False, "Refusing to delete without at least one match filter."

    try:
        client = get_client()
        query = client.table(table).delete().eq("center_id", center_id)

        for col, val in match.items():
            query = query.eq(col, val)

        response = query.execute()
        return True, response.data or []

    except Exception as e:
        return False, f"Error deleting from {table}: {e}"
