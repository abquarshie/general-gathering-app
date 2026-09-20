"""
Secrets and sign-in.

Streamlit only reads .streamlit/secrets.toml relative to the folder it was
started from, so everything here reads through one loader that also looks next
to the code. Without that, launching from elsewhere makes Neon and Sheets look
unconfigured while the file sits right there.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from pathlib import Path

import streamlit as st

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
IDLE_MINUTES = 240

DEMO_USERS = {"overseer": ("pass123", "Overseer"), "assistant": ("pass123", "Assistant Overseer")}


def local_secrets_path() -> Path:
    return Path(__file__).parent / ".streamlit" / "secrets.toml"


@st.cache_resource
def all_secrets() -> dict:
    merged: dict = {}
    local = local_secrets_path()
    if local.exists():
        try:
            import tomllib

            merged.update(tomllib.loads(local.read_text()))
        except Exception:
            pass
    try:
        for key in st.secrets.keys():
            value = st.secrets[key]
            merged[key] = dict(value) if hasattr(value, "keys") else value
    except Exception:
        pass
    return merged


def forget_secrets() -> None:
    """Re-read secrets.toml, so an edit shows up without a restart."""
    all_secrets.clear()


def secret_block(name: str) -> dict:
    block = all_secrets().get(name)
    return dict(block) if hasattr(block, "keys") else {}


def configured_users() -> dict | None:
    users = secret_block("users")
    return {k: dict(v) if hasattr(v, "keys") else v for k, v in users.items()} or None


def prehash(password: str) -> bytes:
    """bcrypt ignores anything past 72 bytes, so hash to a fixed length first."""
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def verify_password(password: str, rec: dict) -> tuple[bool, str]:
    strong = str(rec.get("password_hash", "")).strip()
    if strong:
        try:
            import bcrypt
        except ModuleNotFoundError:
            return False, "This account uses password_hash, which needs the bcrypt package."
        try:
            return bcrypt.checkpw(prehash(password), strong.encode()), ""
        except ValueError:
            return False, "password_hash in secrets is not a valid bcrypt hash."
    legacy = str(rec.get("password_sha256", "")).strip()
    return bool(legacy) and hashlib.sha256(password.encode()).hexdigest() == legacy, ""


def check_login(username: str, password: str, roles) -> tuple[str | None, str]:
    users = configured_users()
    if users:
        rec = users.get(username)
        if not rec:
            return None, ""
        ok, problem = verify_password(password, rec)
        if problem:
            return None, problem
        role = rec.get("role")
        if ok and role not in roles:
            return None, f"That account's role, {role!r}, is not one the app knows."
        return (role if ok else None), ""
    demo = DEMO_USERS.get(username)
    return (demo[1] if demo and demo[0] == password else None), ""


def session_expired() -> bool:
    since = st.session_state.get("signed_in_at")
    if not since:
        return False
    return (datetime.now() - since).total_seconds() > IDLE_MINUTES * 60


def report(storage_label: str, roles) -> list[str]:
    """Plain-language notes for the sign-in screen's help panel."""
    notes = [f"Storage in use: {storage_label}", f"Streamlit was started from: {Path.cwd()}"]
    cwd_file = Path.cwd() / ".streamlit" / "secrets.toml"
    app_file = local_secrets_path()
    for label, p in (("Launch folder", cwd_file), ("Next to app.py", app_file)):
        notes.append(f"{label}: {p} — {'found' if p.exists() else 'not found'}")
    stray = sorted(
        p.name
        for d in {cwd_file.parent, app_file.parent}
        if d.exists()
        for p in d.glob("secrets.toml.*")
    )
    if stray:
        notes.append("Files that need renaming to secrets.toml: " + ", ".join(stray))

    users = configured_users()
    if users:
        notes.append("Accounts loaded: " + ", ".join(sorted(users)))
        for name, rec in users.items():
            strong = str(rec.get("password_hash", "")).strip()
            legacy = str(rec.get("password_sha256", "")).strip()
            if name != name.strip().lower():
                notes.append(f"'{name}' has capitals or spaces; sign-in lowercases the name")
            if strong and not strong.startswith("$2"):
                notes.append(f"'{name}' password_hash is not a bcrypt hash")
            elif not strong and len(legacy) != 64:
                notes.append(f"'{name}' has no usable password hash")
            if rec.get("role") not in roles:
                notes.append(f"'{name}' role {rec.get('role')!r} is not one the app knows")
    else:
        notes.append("No accounts loaded, so only the demo sign-ins work.")
        notes.append("Section headers must read [users.overseer], not [overseer].")
    return notes


def login_screen(app_title: str, storage_label: str, roles) -> None:
    st.markdown(f"## {app_title}")
    st.markdown(
        '<p class="note">Sign in to plan departments, volunteers and shifts.</p>',
        unsafe_allow_html=True,
    )

    locked_until = st.session_state.get("locked_until")
    if locked_until and datetime.now() < locked_until:
        wait = int((locked_until - datetime.now()).total_seconds())
        st.error(f"Too many attempts. Try again in {wait} seconds.")
        return

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            role, problem = check_login(username.strip().lower(), password, roles)
            if problem:
                st.error(problem)
            elif role:
                st.session_state.role = role
                st.session_state.username = username.strip().lower()
                st.session_state.signed_in_at = datetime.now()
                st.session_state.attempts = 0
                st.rerun()
            else:
                st.session_state.attempts = st.session_state.get("attempts", 0) + 1
                left = MAX_ATTEMPTS - st.session_state.attempts
                if left <= 0:
                    st.session_state.locked_until = datetime.now().replace(
                        microsecond=0
                    ) + __import__("datetime").timedelta(seconds=LOCKOUT_SECONDS)
                    st.session_state.attempts = 0
                    st.error("Too many attempts. Wait a minute and try again.")
                else:
                    st.error(f"That username and password don't match. {left} tries left.")

    if not configured_users():
        st.info("No credentials in secrets, so demo logins are active (overseer / pass123).")
    with st.expander("Trouble signing in?"):
        for note in report(storage_label, roles):
            st.markdown(f'<p class="note">{note}</p>', unsafe_allow_html=True)
