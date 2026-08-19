# Exam Management System — Deployment Guide

Multi-tenant exam management system. One shared Supabase project holds
every exam center's data, isolated by `center_id`. You (the Owner) can
add/manage centers from the in-app Owner Panel; each center's own Admin
and Centre Superintendent log in with credentials scoped to just their
center.

## First-time setup (do this once)

1. Run the schema: `exam-management-schema.sql` in your Supabase SQL editor.
2. Generate your Owner login: `python generate_owner_credentials.py`
3. Create `.streamlit/secrets.toml` (copy `secrets.toml.example`, fill in
   your real Supabase URL/key and the Owner credentials from step 2).
4. Add your first center either via `setup_center.py`, or just log into
   the app as Owner and use "Add New Center."

---

## Path A — Online, free (GitHub + Streamlit Community Cloud)

This is the easiest way to give every center a single shared URL.

1. Push this whole folder to a GitHub repo. **Do not commit
   `.streamlit/secrets.toml`** — it's already in `.gitignore`.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, click "New app," point it at your repo and `app.py`.
3. In the app's Settings → Secrets, paste the contents of your real
   `secrets.toml` (Streamlit Cloud has its own secrets manager — you
   don't need a file for this).
4. Deploy. You get a URL like `https://yourapp.streamlit.app` — send this
   to every college. They pick their center on the login screen; nothing
   else to install.

Free tier limits (as of writing): the app sleeps after inactivity and
wakes on the next visit (a few seconds' delay), and there's a cap on
concurrent apps per account — fine for this use case.

---

## Path B — Offline `.exe` (Windows)

**Important:** this still needs internet to reach Supabase — "offline"
here means "no Python install required, just double-click," not "works
without internet." What it buys you: colleges don't see your source code
sitting in `.py` files, and there's nothing for them to configure.

### Build steps

1. Install build tools:
   ```
   pip install pyinstaller
   ```

2. From this folder, build the executable:
   ```
   pyinstaller --onefile --name ExamManagementSystem ^
     --add-data "app.py;." --add-data "auth.py;." --add-data "db.py;." ^
     --add-data "admin_panel.py;." --add-data "cs_panel.py;." ^
     --add-data "student_portal.py;." --add-data "owner_panel.py;." ^
     --add-data "data_ingestion.py;." --add-data "seat_assignment.py;." ^
     --add-data "reporting.py;." --add-data "remuneration.py;." ^
     --add-data "assets;assets" ^
     --add-data ".streamlit;.streamlit" ^
     run_app.py
   ```
   The output lands in `dist/ExamManagementSystem.exe`. The `assets` folder
   holds `UFM_Form.pdf` (the official form template used by the UFM print
   feature) — it must be bundled or that feature will error looking for it.

3. **Your Supabase credentials will be bundled inside that .exe** (via the
   `.streamlit` folder). Anyone who runs it can reach your Supabase
   project with whatever key you embedded. Use a key scoped appropriately
   — do not ship your service-role key this way if you're not comfortable
   with that exposure. (See "Protecting the embedded key" below.)

4. Test the .exe on a clean machine (or a VM) before distributing —
   PyInstaller sometimes misses a dependency's data files, and it's
   easier to catch that before a college calls you.

5. Optional — wrap it in a proper installer with Inno Setup (same tool
   your ShopER-P distribution uses) so it installs to Program Files with
   a Start Menu shortcut instead of being a loose .exe.

### Protecting the embedded key

Since every copy of the .exe carries the same Supabase key, consider:
- A **restricted Postgres role** in Supabase (not the full service-role
  key) that can only touch the exam-system tables, if you want to limit
  blast radius from a leaked key.
- Basic obfuscation of the bundled Python bytecode with `pyarmor` if you
  want to raise the bar against casual extraction — not unbreakable, but
  matches the level of protection typical for this kind of distribution.

### Code obfuscation (optional, mirrors ShopER-P's approach)

```
pip install pyarmor
pyarmor gen -O dist_protected run_app.py app.py auth.py db.py admin_panel.py cs_panel.py student_portal.py owner_panel.py data_ingestion.py seat_assignment.py reporting.py remuneration.py
```
Then PyInstaller-package the `dist_protected` output instead of the raw
source. This is the OWNER_KIT (full source, only for you) vs. CLIENT_DIST
(protected build, for colleges) split from ShopER-P.

---

## Adding a new center later

Either:
- Log in as Owner → "Add New Center," or
- Run `setup_center.py` again with the new center's details.

Either way, give the college their center's Admin and CS username/password
— they never need your Owner credentials.
