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


def check_users(data: dict) -> None:
    head("Sign-in accounts")
    users = data.get("users") or {}
    if not users:
        print(f"{CROSS}No [users] block. Headers must read [users.overseer].")
        return
    for name, rec in users.items():
        bits = []
        if name != name.lower():
            bits.append("name must be lowercase")
        if not (rec.get("password_hash") or rec.get("password_sha256")):
            bits.append("no password_hash")
        if rec.get("role") not in ("Overseer", "Assistant Overseer"):
            bits.append(f"role {rec.get('role')!r} is not one the app knows")
        print(f"{CROSS if bits else TICK}{name}: " + ("; ".join(bits) or rec.get("role", "")))


def check_neon(data: dict) -> None:
    head("Neon Postgres")
    block = data.get("postgres") or {}
    if not block:
        print(f"{CROSS}No [postgres] block, so the app uses SQLite in data/portal.db.")
        return

    dsn = block.get("dsn") or ""
    if not dsn:
        print(f"{CROSS}[postgres] has no dsn.")
        return
    if not dsn.startswith(("postgresql://", "postgres://")):
        print(f"{CROSS}dsn should start with postgresql://")
        return
    print(f"{TICK}dsn present")
    if "-pooler." not in dsn:
        print(f"{WARN}Not the pooled host. Turn on 'Pooled connection' in Neon.")
    if "sslmode=" not in dsn:
        print(f"{WARN}No sslmode=require at the end. Neon needs it.")
    if "<" in dsn or "PASSWORD" in dsn or "USER:" in dsn:
        print(f"{CROSS}The dsn still has placeholder text in it.")
        return

    try:
        import psycopg
    except ModuleNotFoundError:
        print(f'{CROSS}psycopg is not installed. Run: python3 -m pip install "psycopg[binary]"')
        print("        The quotes matter in zsh, or the brackets are read as a pattern.")
        return
    print(f"{TICK}psycopg {psycopg.__version__} installed")

    try:
        with psycopg.connect(dsn, connect_timeout=15) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_database(), current_user")
            dbname, user = cur.fetchone()
            print(f"{TICK}Connected to {dbname} as {user}")
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            tables = [r[0] for r in cur.fetchall()]
            print(f"{TICK}Tables: " + (", ".join(tables) if tables else "none yet (normal)"))
            if "assignments" in tables:
                cur.execute("SELECT COUNT(*) FROM assignments")
                print(f"{TICK}{cur.fetchone()[0]} assignments stored")
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        print(f"{CROSS}{exc}")
        if "password" in text or "authentication" in text:
            print("        Wrong password. Neon shows it once — reset the role's password.")
        elif "does not exist" in text:
            print("        The database name at the end of the dsn is wrong.")
        elif "timeout" in text or "could not translate" in text or "name or service" in text:
            print("        Network or hostname problem — check the host and your connection.")


def check_sheets(data: dict) -> None:
    head("Google Sheets")
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
    for p in problems:
        print(f"{CROSS}{p}")
    if problems:
        return

    key = str(creds.get("private_key", ""))
    if "BEGIN PRIVATE KEY" not in key:
        print(f"{CROSS}private_key does not look like a key.")
        return
    if "\n" not in key:
        print(f"{CROSS}private_key has no newlines in it. Keep the \\n sequences.")
        return
    print(f"{TICK}Service account: {creds['client_email']}")

    try:
        import gspread
    except ModuleNotFoundError:
        print(f"{CROSS}gspread is not installed. Run: python3 -m pip install gspread")
        return
    print(f"{TICK}gspread installed")

    try:
        client = gspread.service_account_from_dict(dict(creds))
        book = client.open_by_key(str(sheets["spreadsheet_id"]))
        print(f"{TICK}Opened '{book.title}'")
        print(f"{TICK}Tabs: " + ", ".join(w.title for w in book.worksheets()))
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        print(f"{CROSS}{exc}")
        if "permission" in text or "403" in text:
            print(f"        Share the spreadsheet with {creds['client_email']} as Editor.")
        elif "404" in text or "not found" in text:
            print("        The spreadsheet_id is wrong — it is the part of the URL")
            print("        between /d/ and /edit.")
        elif "api" in text and "disabled" in text:
            print("        Enable the Google Sheets API for that project in the console.")
        elif "invalid_grant" in text or "jwt" in text:
            print("        The key is malformed, or this machine's clock is off.")


def main() -> None:
    head("Where the secrets file is")
    path = find_secrets()
    if path is None:
        sys.exit(1)
    print(f"\nUsing {path}")
    data = load(path)
    check_users(data)
    check_neon(data)
    check_sheets(data)
    print("\nRun the app from the folder holding .streamlit, or the file may be ignored:")
    print(f"    cd {path.parent.parent}  &&  streamlit run app.py")


if __name__ == "__main__":
    main()
