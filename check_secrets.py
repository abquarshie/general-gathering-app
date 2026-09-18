"""
Login diagnostic. Run from the same folder as app.py:

    python3 check_secrets.py

It reads .streamlit/secrets.toml the way the app does and tells you whether a
password you type would be accepted, without starting Streamlit.
"""

import getpass
import hashlib
import tomllib
from pathlib import Path

SECRETS = Path(".streamlit/secrets.toml")

print(f"Looking for {SECRETS.resolve()}")
if not SECRETS.exists():
    print("\nNot found. The app is running in demo mode, so only the demo")
    print("passwords work. Check that the file is named exactly secrets.toml")
    print("(not secrets.toml.sample) and sits in .streamlit/ inside the folder")
    print("you run `streamlit run app.py` from.")
    raise SystemExit(1)

data = tomllib.loads(SECRETS.read_text())
users = data.get("users")
if not users:
    print("\nFile found, but it has no [users] block. Section headers must look")
    print("like [users.overseer], not [overseer].")
    raise SystemExit(1)

print("\nAccounts in the file:")
for name, rec in users.items():
    h = str(rec.get("password_sha256", ""))
    flags = []
    if name != name.strip().lower():
        flags.append("NAME must be lowercase with no spaces")
    if len(h) != 64:
        flags.append(f"HASH is {len(h)} characters, expected 64")
    if h != h.strip():
        flags.append("HASH has stray whitespace")
    if rec.get("role") not in ("Overseer", "Assistant Overseer"):
        flags.append(f"ROLE {rec.get('role')!r} is not one the app knows")
    status = "; ".join(flags) if flags else "looks fine"
    print(f"  {name:<12} role={rec.get('role','?'):<20} {status}")

username = input("\nUsername to test: ").strip().lower()
password = getpass.getpass("Password (not shown): ")

rec = users.get(username)
if not rec:
    print(f"\nNo account named {username!r}. The names above are what the app accepts.")
    raise SystemExit(1)

typed = hashlib.sha256(password.encode()).hexdigest()
stored = str(rec.get("password_sha256", "")).strip()
if typed == stored:
    print(f"\nMatch. Signing in as {username} gives the {rec.get('role')} view.")
else:
    print("\nNo match.")
    print(f"  hash of what you typed: {typed}")
    print(f"  hash in the file:       {stored}")
    print("\nPaste the first line into the file as password_sha256 to make this")
    print("password work, or check the password itself for a stray space.")
