"""
Check the Neon and Sheets setup without starting Streamlit.

    python3 check_setup.py

It finds your secrets file, checks each block, then actually tries to connect to
Neon and to open the spreadsheet, reporting where it stops.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
TICK, CROSS, WARN = "  ok   ", "  FAIL ", "  ...  "


def head(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def find_secrets() -> Path | None:
    candidates = [
        Path.cwd() / ".streamlit" / "secrets.toml",
        HERE / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    print(f"Working directory: {Path.cwd()}")
    print(f"This script lives in: {HERE}")
    found = None
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        mark = TICK if path.exists() else WARN
        print(f"{mark}{path}")
        if path.exists() and found is None:
            found = path
    if found is None:
        strays = sorted(
            p
            for d in {c.parent for c in candidates}
            if d.exists()
            for p in d.glob("secrets.toml*")
        )
        if strays:
            print(f"{CROSS}Found but wrongly named: " + ", ".join(str(s) for s in strays))
            print("        Rename it to exactly secrets.toml")
        else:
            print(f"{CROSS}No secrets file anywhere. Copy .streamlit/secrets.toml.sample")
    return found


def load(path: Path) -> dict:
    import tomllib

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        print(f"{CROSS}That file is not valid TOML: {exc}")
        print("        A private_key pasted across several lines is the usual cause.")
        print("        It must be one line, in double quotes, with \\n left as text.")
        sys.exit(1)
    print(f"{TICK}Parsed. Blocks found: " + ", ".join(sorted(data)) or "none")
    return data


def check_users(data: dict) -> list:
    out = []
    users = data.get("users") or {}
    if not users:
        return [("fail", "No [users] block. Headers must read [users.overseer].")]
    for name, rec in users.items():
        bits = []
        if name != name.lower():
            bits.append("name must be lowercase")
        if not (rec.get("password_hash") or rec.get("password_sha256")):
            bits.append("no password_hash")
        if rec.get("role") not in ("Overseer", "Assistant Overseer"):
            bits.append(f"role {rec.get('role')!r} is not one the app knows")
        out.append(
            ("fail" if bits else "ok", f"{name}: " + ("; ".join(bits) or rec.get("role", "")))
        )
    return out


def check_neon(data: dict) -> list:
    out = []
    block = data.get("postgres") or {}
    if not block:
        return [("fail", "No [postgres] block, so the app uses SQLite in data/portal.db.")]

    dsn = str(block.get("dsn") or "")
    if not dsn:
        return [("fail", "[postgres] has no dsn.")]
    if not dsn.startswith(("postgresql://", "postgres://")):
        return [("fail", "dsn should start with postgresql://")]
    out.append(("ok", "dsn present"))
    if "-pooler." not in dsn:
        out.append(("warn", "Not the pooled host. Turn on 'Pooled connection' in Neon."))
    if "sslmode=" not in dsn:
        out.append(("warn", "No sslmode=require at the end. Neon needs it."))
    if "<" in dsn or "PASSWORD" in dsn or "USER:" in dsn:
        out.append(("fail", "The dsn still has placeholder text in it."))
        return out

    try:
        import psycopg
    except ModuleNotFoundError:
        out.append(
            ("fail", 'psycopg is not installed. Run: python3 -m pip install "psycopg[binary]" '
                     "(the quotes matter in zsh).")
        )
        return out
    out.append(("ok", f"psycopg {psycopg.__version__} installed"))

    try:
        with psycopg.connect(dsn, connect_timeout=15) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_database(), current_user")
            dbname, user = cur.fetchone()
            out.append(("ok", f"Connected to {dbname} as {user}"))
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            tables = [r[0] for r in cur.fetchall()]
            out.append(("ok", "Tables: " + (", ".join(tables) if tables else "none yet (normal)")))
            if "assignments" in tables:
                cur.execute("SELECT COUNT(*) FROM assignments")
                out.append(("ok", f"{cur.fetchone()[0]} assignments stored"))
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        out.append(("fail", str(exc)))
        if "password" in text or "authentication" in text:
            out.append(("warn", "Wrong password. Neon shows it once — reset the role's password."))
        elif "does not exist" in text:
            out.append(("warn", "The database name at the end of the dsn is wrong."))
        elif "timeout" in text or "translate" in text or "name or service" in text:
            out.append(("warn", "Network or hostname problem — check the host."))
    return out


def check_sheets(data: dict) -> list:
    out = []
    creds = data.get("gcp_service_account") or {}
    sheets = data.get("sheets") or {}
    problems = []
    if not creds:
        problems.append("no [gcp_service_account] block")
    else:
        for field in ("project_id", "private_key", "client_email"):
            if not str(creds.get(field, "")).strip():
                problems.append(f"[gcp_service_account] has no {field}")
    if not str(sheets.get("spreadsheet_id", "")).strip():
        problems.append("no spreadsheet_id under [sheets]")
    if problems:
        return [("fail", p) for p in problems]

    key = str(creds.get("private_key", ""))
    if "BEGIN PRIVATE KEY" not in key:
        return [("fail", "private_key does not look like a key.")]
    if "\n" not in key:
        return [("fail", "private_key has no newlines in it. Keep the \\n sequences.")]
    out.append(("ok", f"Service account: {creds['client_email']}"))

    try:
        import gspread
    except ModuleNotFoundError:
        out.append(("fail", "gspread is not installed. Run: python3 -m pip install gspread"))
        return out
    out.append(("ok", "gspread installed"))

    try:
        client = gspread.service_account_from_dict(dict(creds))
        book = client.open_by_key(str(sheets["spreadsheet_id"]))
        out.append(("ok", f"Opened '{book.title}'"))
        out.append(("ok", "Tabs: " + ", ".join(w.title for w in book.worksheets())))
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        out.append(("fail", str(exc)[:300]))
        if "permission" in text or "403" in text:
            out.append(("warn", f"Share the spreadsheet with {creds['client_email']} as Editor."))
        elif "404" in text or "not found" in text:
            out.append(("warn", "The spreadsheet_id is wrong — it is the part of the URL "
                                "between /d/ and /edit."))
        elif "disabled" in text:
            out.append(("warn", "Enable the Google Sheets API for that project."))
        elif "invalid_grant" in text or "jwt" in text:
            out.append(("warn", "The key is malformed, or this machine's clock is off."))
    return out


def run_all(data: dict) -> list:
    """Every check, as (section, status, message) for the app to render."""
    rows = []
    for section, results in (
        ("Sign-in accounts", check_users(data)),
        ("Neon Postgres", check_neon(data)),
        ("Google Sheets", check_sheets(data)),
    ):
        for status, message in results:
            rows.append((section, status, message))
    return rows


def main() -> None:
    head("Where the secrets file is")
    path = find_secrets()
    if path is None:
        sys.exit(1)
    print(f"\nUsing {path}")
    data = load(path)
    marks = {"ok": TICK, "fail": CROSS, "warn": WARN}
    last = None
    for section, status, message in run_all(data):
        if section != last:
            head(section)
            last = section
        print(f"{marks[status]}{message}")
    print("\nRun the app from the folder holding .streamlit, or the file may be ignored:")
    print(f"    cd {path.parent.parent}  &&  streamlit run app.py")


if __name__ == "__main__":
    main()
