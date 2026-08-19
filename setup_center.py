
import os
import bcrypt
from supabase import create_client

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # pip install tomli, for Python < 3.11

SECRETS_PATH = os.path.join(".streamlit", "secrets.toml")

with open(SECRETS_PATH, "rb") as f:
    _secrets = tomllib.load(f)

SUPABASE_URL = _secrets["supabase"]["url"]
SUPABASE_KEY = _secrets["supabase"]["key"]

# --- Edit these for each center you add ---
CENTER = {
    "university_name": "Jiwaji University Gwalior",
    "center_name": "Government Law College, Morena",
    "center_code": "0107",
    "address": "Near Ghirona Temple, Dhaulpur Road, Morena, Madhya Pradesh",
    "admin_username": "admin",
    "admin_password": "admin123",   # plaintext here only, hashed before insert
    "cs_username": "cs_admin",
    "cs_password": "cs_pass123",         # plaintext here only, hashed before insert
}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def main():
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    row = {
        "university_name": CENTER["university_name"],
        "center_name": CENTER["center_name"],
        "center_code": CENTER["center_code"],
        "address": CENTER["address"],
        "admin_username": CENTER["admin_username"],
        "admin_password_hash": hash_password(CENTER["admin_password"]),
        "cs_username": CENTER["cs_username"],
        "cs_password_hash": hash_password(CENTER["cs_password"]),
        "is_active": True,
    }

    response = client.table("exam_centers").insert(row).execute()
    print("Created center:", response.data)


if __name__ == "__main__":
    main()