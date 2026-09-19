"""
Make a password hash for .streamlit/secrets.toml.

    python3 make_password.py

The password is not echoed and never touches your shell history. Paste the
line it prints under the right [users.<name>] block.
"""

import base64
import getpass
import hashlib

try:
    import bcrypt
except ModuleNotFoundError:
    raise SystemExit("bcrypt is not installed. Run: pip install bcrypt")

first = getpass.getpass("Password: ")
again = getpass.getpass("Again: ")
if first != again:
    raise SystemExit("Those don't match. Nothing written.")
if len(first) < 10:
    print("Warning: short passwords are guessable even with a slow hash.\n")

prehashed = base64.b64encode(hashlib.sha256(first.encode()).digest())
print("\npassword_hash = \"" + bcrypt.hashpw(prehashed, bcrypt.gensalt()).decode() + "\"")
