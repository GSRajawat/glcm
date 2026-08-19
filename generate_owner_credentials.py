"""
generate_owner_credentials.py — Run once, locally, to generate your Owner
login hash. Paste the output into secrets.toml (both local and Streamlit
Cloud). Run with: python generate_owner_credentials.py
"""

import getpass

import bcrypt


def main():
    username = input("Choose your owner username: ").strip()
    password = getpass.getpass("Choose your owner password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        raise SystemExit("Passwords did not match.")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    print("\nAdd this to secrets.toml:\n")
    print("[owner]")
    print(f'username = "{username}"')
    print(f'password_hash = "{password_hash}"')


if __name__ == "__main__":
    main()
