"""
General Gathering Management Portal
-----------------------------------
For the gathering overseer and the assistant overseer.

Run:  streamlit run app.py
Needs: streamlit >= 1.49, pandas. psycopg[binary] for Neon, gspread for Sheets.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import db as store

APP_TITLE = "Gathering Portal"

OVERSEER_DEPTS = ["Accounts", "Attendant", "Cleaning", "First Aid", "Parking", "Rooming"]
ASSISTANT_DEPTS = ["Audio/Video", "Baptism", "Installation", "Lost & Found", "Checkroom"]
ALL_DEPTS = OVERSEER_DEPTS + ASSISTANT_DEPTS
DEPT_OWNER = {d: "Overseer" for d in OVERSEER_DEPTS}
DEPT_OWNER.update({d: "Assistant Overseer" for d in ASSISTANT_DEPTS})

HALVES = ["Morning", "Afternoon"]
SHIFT_COLUMNS = ["Shift", "Half", "Report", "Start", "End"]
DEFAULT_SHIFTS = [
    {"Shift": "Early setup", "Half": "Morning", "Report": "06:00", "Start": "06:15", "End": "08:30"},
    {"Shift": "Morning", "Half": "Morning", "Report": "07:45", "Start": "08:30", "End": "12:45"},
    {"Shift": "Afternoon", "Half": "Afternoon", "Report": "12:30", "Start": "13:15", "End": "17:15"},
]
TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"

CONGREGATIONS = [
    "Adabraka Ga",
    "Bubiashie South Ga",
    "Central Guggisberg Ga",
    "Chemu Road Ga",
    "Chorkor Ga",
    "Chorkor North Ga",
    "Dansoman Beach Ga",
    "Dansoman Estate Ga",
    "East Guggisberg Ga",
    "Kaneshie Ga",
    "Lartebiokoshie Ga",
    "Lartebiokoshie West Ga",
    "Manponse Ga",
    "Mataheko Ga",
    "Palace Street",
    "Ussher Town Ga",
]

GENDERS = ["Male", "Female"]
PRIVILEGES = ["Elder", "Servant", "Publisher"]
VOLUNTEER_STATUSES = ["Confirmed", "Invited", "Unavailable", "Moved out"]
VOLUNTEER_COLUMNS = [
    "Name",
    "Gender",
    "Privilege",
    "Congregation",
    "Department",
    "Shift",
    "Status",
    "Notes",
]
DB_FIELDS = {
    "Name": "name",
    "Gender": "gender",
    "Privilege": "privilege",
    "Congregation": "congregation",
    "Department": "dept",
    "Shift": "shift",
    "Status": "status",
    "Notes": "notes",
}
TARGET_COLUMNS = ["Department", "Shift", "Needed", "Min elders", "Min servants"]

CHECKLIST = {
    "Overseer": [
        ("setup_brief", "Department overseers told which setup work is allowed"),
        ("setup_plan", "Setup work planned and sequenced"),
        ("food", "Volunteers told to bring their own food"),
        ("ppe", "Volunteers told which PPE their task needs"),
        ("dress", "Dress and grooming reminder given"),
        ("hazard", "Job hazard forms collected from my departments"),
    ],
    "Assistant Overseer": [
        ("setup_brief_a", "My departments briefed on setup and testing"),
        ("food_ppe_a", "My departments briefed on food and PPE"),
        ("hazard_a", "Job hazard forms collected from my departments"),
        ("recruit_a", "Recruitment closed four weeks before the date"),
    ],
}

DEPT_FIELDS = {
    "overseer": "",
    "assistant": "",
    "keymen": "",
    "meeting_date": None,
    "attending": False,
    "training_set": False,
    "hazard_done": False,
    "notes": "",
}

DEMO_USERS = {"overseer": ("pass123", "Overseer"), "assistant": ("pass123", "Assistant Overseer")}

NEON_STEPS = """
1. Create a project at [console.neon.tech](https://console.neon.tech), region
   `eu-central-1` (Frankfurt) for the best latency from Accra.
2. Under **Roles**, create a role for the app rather than using the owner
   account. Copy its password — it is shown once.
3. Under **Connection Details**, pick that role and turn on **Pooled
   connection**.
4. Put it in `.streamlit/secrets.toml` (or App settings → Secrets on Streamlit
   Cloud):

   ```toml
   [postgres]
   dsn = "postgresql://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require"
   ```

5. Install the driver — the quotes are required, or zsh reads the brackets as a
   filename pattern:

   ```
   python3 -m pip install "psycopg[binary]"
   ```

6. Restart. This sidebar should read **Neon Postgres**. Tables are created on
   first run.

**Take a backup from the Documents tab first.** Switching storage moves nothing;
Neon starts empty and you restore into it with **Merge**.
"""

SHEETS_STEPS = """
1. At [console.cloud.google.com](https://console.cloud.google.com), create a
   project, then **APIs & Services → Library** and enable the **Google Sheets
   API**.
2. **IAM & Admin → Service Accounts → Create**. No roles or user access needed.
3. Open it, **Keys → Add key → Create new key → JSON**, and download.
4. Create the spreadsheet and **share it with the service account's
   `client_email` as an Editor**. This is the step that gets forgotten — without
   it every push fails on permissions.
5. Copy the JSON's fields into a `[gcp_service_account]` block in secrets. Keep
   `private_key` on one line with its `\n` sequences intact.
6. Add the spreadsheet id — the long string in its URL between `/d/` and
   `/edit`:

   ```toml
   [sheets]
   spreadsheet_id = "1AbC..."
   ```

7. `python3 -m pip install gspread`, then use **Push to Sheets** above.
"""


def sheets_problems() -> list[str]:
    """What is still missing before Push to Sheets can work."""
    missing = []
    creds = secret_block("gcp_service_account")
    sheets = secret_block("sheets")
    if not creds:
        missing.append("no [gcp_service_account] block in secrets")
    else:
        for field in ("client_email", "private_key", "project_id"):
            if not str(creds.get(field, "")).strip():
                missing.append(f"[gcp_service_account] has no {field}")
    if not str(sheets.get("spreadsheet_id", "")).strip():
        missing.append("no spreadsheet_id under [sheets]")
    return missing


def sheets_ready() -> bool:
    return not sheets_problems()


st.set_page_config(page_title=APP_TITLE, page_icon="🗓", layout="wide")

CSS = """
<style>
:root {
  --ink:      #16202b;
  --ink-soft: #5b6b7c;
  --paper:    #f7f5f0;
  --line:     #dcd8cf;
  --signal:   #b4531c;
  --done-ok:  #2f6b47;
}
.countdown {
  background: var(--ink); color: var(--paper); padding: 1.4rem 1.6rem;
  display: flex; align-items: baseline; gap: 2.4rem; flex-wrap: wrap; border-radius: 2px;
}
.countdown .days { font-size: 3.6rem; line-height: 1; font-weight: 700; letter-spacing: -0.03em; }
.countdown .days small { font-size: 1rem; font-weight: 400; opacity: .75; margin-left: .4rem; letter-spacing: 0; }
.countdown .meta { font-size: .95rem; opacity: .8; }
.countdown .meta b { display: block; font-size: 1.05rem; opacity: 1; font-weight: 600; }
.countdown.late { background: var(--signal); }
.row {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  padding: .55rem .2rem .55rem .7rem; border-bottom: 1px solid var(--line);
  border-left: 3px solid transparent;
}
.row:first-of-type { border-top: 1px solid var(--line); }
.row.ready  { border-left-color: var(--done-ok); }
.row.part   { border-left-color: #c9a227; }
.row.behind { border-left-color: var(--signal); }
.row .name  { font-weight: 600; }
.row .owner { color: var(--ink-soft); font-size: .85rem; font-weight: 400; margin-left: .5rem; }
.row .state { font-size: .85rem; color: var(--ink-soft); white-space: nowrap; }
.row .short { color: var(--signal); font-weight: 600; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin: .35rem 0 1rem; }
.bar > span { display: block; height: 100%; background: var(--ink); }
.note { color: var(--ink-soft); font-size: .9rem; }
.stTabs [data-baseweb="tab-list"] { gap: 1.6rem; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { padding: .4rem 0; font-weight: 600; background: transparent; }
section[data-testid="stSidebar"] { border-right: 1px solid var(--line); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def local_secrets_path() -> Path:
    return Path(__file__).parent / ".streamlit" / "secrets.toml"


@st.cache_resource
def all_secrets() -> dict:
    """Every secret, from either place Streamlit might not be looking.

    Streamlit only reads .streamlit/secrets.toml relative to the folder it was
    started from. Running `streamlit run ~/somewhere/app.py` from home means it
    sees nothing, and the app would quietly fall back to SQLite and report
    Sheets as unconfigured while the file sat right next to app.py.
    """
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


def secret_block(name: str) -> dict:
    block = all_secrets().get(name)
    return dict(block) if hasattr(block, "keys") else {}


@st.cache_resource
def get_db() -> store.Database:
    return store.Database(all_secrets())


try:
    DB = get_db()
except Exception as exc:  # noqa: BLE001
    st.error(
        "The database could not be opened. On Neon, check the [postgres] block in "
        f"secrets and that psycopg is installed.\n\n{exc}"
    )
    st.stop()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def configured_users() -> dict | None:
    users = secret_block("users")
    return {k: dict(v) if hasattr(v, "keys") else v for k, v in users.items()} or None


def secrets_report() -> list[str]:
    notes = [f"Storage in use: {DB.label}", f"Streamlit was started from: {Path.cwd()}"]
    cwd_file = Path.cwd() / ".streamlit" / "secrets.toml"
    app_file = Path(__file__).parent / ".streamlit" / "secrets.toml"
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
            h = str(rec.get("password_sha256", "")).strip()
            if name != name.strip().lower():
                notes.append(f"'{name}' has capitals or spaces; sign-in lowercases the name")
            if strong:
                if not strong.startswith("$2"):
                    notes.append(f"'{name}' password_hash is not a bcrypt hash")
            elif len(h) != 64:
                notes.append(f"'{name}' has no password_hash and its password_sha256 is "
                             f"{len(h)} characters, expected 64")
            if rec.get("role") not in CHECKLIST:
                notes.append(f"'{name}' role {rec.get('role')!r} is not one the app knows")
    else:
        notes.append("No accounts loaded, so only the demo sign-ins work.")
        notes.append("Section headers must read [users.overseer], not [overseer].")
    return notes


MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
IDLE_MINUTES = 240


def prehash(password: str) -> bytes:
    """bcrypt ignores anything past 72 bytes, so hash to a fixed length first."""
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def verify_password(password: str, rec: dict) -> bool:
    """Prefer the slow bcrypt hash; fall back to the older SHA-256 entries."""
    strong = str(rec.get("password_hash", "")).strip()
    if strong:
        try:
            import bcrypt
        except ModuleNotFoundError:
            st.error("This account uses password_hash, which needs the bcrypt package.")
            return False
        try:
            return bcrypt.checkpw(prehash(password), strong.encode())
        except ValueError:
            st.error("password_hash in secrets is not a valid bcrypt hash.")
            return False
    legacy = str(rec.get("password_sha256", "")).strip()
    return bool(legacy) and hashlib.sha256(password.encode()).hexdigest() == legacy


def check_login(username: str, password: str) -> str | None:
    users = configured_users()
    if users:
        rec = users.get(username)
        return rec.get("role") if rec and verify_password(password, rec) else None
    demo = DEMO_USERS.get(username)
    return demo[1] if demo and demo[0] == password else None


def login_screen() -> None:
    st.markdown(f"## {APP_TITLE}")
    st.markdown(
        '<p class="note">Sign in to plan departments, volunteers and shifts.</p>',
        unsafe_allow_html=True,
    )
    locked_until = st.session_state.get("locked_until", 0.0)
    waiting = int(locked_until - datetime.now().timestamp())
    if waiting > 0:
        st.error(f"Too many attempts. Try again in {waiting} seconds.")

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", disabled=waiting > 0):
            found = check_login(username.strip().lower(), password)
            if found:
                st.session_state.role = found
                st.session_state.username = username.strip().lower()
                st.session_state.last_seen = datetime.now().timestamp()
                st.session_state.attempts = 0
                st.rerun()
            else:
                st.session_state.attempts = st.session_state.get("attempts", 0) + 1
                left = MAX_ATTEMPTS - st.session_state.attempts
                if left <= 0:
                    st.session_state.locked_until = (
                        datetime.now().timestamp() + LOCKOUT_SECONDS
                    )
                    st.session_state.attempts = 0
                    st.rerun()
                st.error(f"That username and password don't match. {left} attempts left.")

    if not configured_users():
        st.info("No credentials in secrets, so demo logins are active (overseer / pass123).")
    with st.expander("Trouble signing in?"):
        for note in secrets_report():
            st.markdown(f'<p class="note">{note}</p>', unsafe_allow_html=True)


if "role" not in st.session_state:
    st.session_state.role = None

if st.session_state.role is not None:
    idle = datetime.now().timestamp() - st.session_state.get("last_seen", 0)
    if idle > IDLE_MINUTES * 60:
        st.session_state.role = None
        st.session_state.timed_out = True
    else:
        st.session_state.last_seen = datetime.now().timestamp()

if st.session_state.role is None:
    if st.session_state.pop("timed_out", False):
        st.info(f"Signed out after {IDLE_MINUTES // 60} hours without activity.")
    login_screen()
    st.stop()

role: str = st.session_state.role
actor: str = st.session_state.get("username", "")

# ---------------------------------------------------------------------------
# Choosing a gathering
# ---------------------------------------------------------------------------

events = store.list_events(DB)
labels = {e["event_key"]: f"{e['event_key']} — {e['event_date']}" for e in events}

with st.sidebar:
    st.markdown(f"**{APP_TITLE}**")
    st.caption(f"Signed in as {role}")

    keys = list(labels)
    if "event_key" not in st.session_state or st.session_state.event_key not in keys:
        st.session_state.event_key = keys[0] if keys else None

    if keys:
        chosen = st.selectbox(
            "Gathering",
            keys,
            index=keys.index(st.session_state.event_key),
            format_func=lambda k: labels[k],
            key="event_pick",
        )
        st.session_state.event_key = chosen

    with st.expander("Start a new gathering", expanded=not keys):
        new_part = st.selectbox("Part", ["Part 1", "Part 2"], key="new_part")
        new_year = st.number_input("Year", value=datetime.now().year, step=1, key="new_year")
        new_date = st.date_input(
            "Date", value=date.today() + timedelta(days=90), key="new_date"
        )
        source = st.selectbox(
            "Copy people and departments from",
            ["Start empty"] + keys,
            format_func=lambda k: labels.get(k, k),
            key="copy_src",
        )
        if st.button("Create", type="primary"):
            new_key = f"{new_part} {int(new_year)}"
            if store.event_exists(DB, new_key):
                st.error(f"{new_key} already exists.")
            else:
                store.create_event(DB, new_key, new_date, actor=actor)
                if source != "Start empty":
                    counts = store.copy_forward(DB, source, new_key, actor=actor)
                    st.success(
                        f"{new_key} created with {counts['volunteers']} people carried "
                        "forward, all set back to Invited."
                    )
                else:
                    st.success(f"{new_key} created.")
                st.session_state.event_key = new_key
                st.rerun()

if not st.session_state.event_key:
    st.markdown("### No gathering yet")
    st.markdown(
        '<p class="note">Create one in the sidebar. If a previous gathering exists, '
        'copy it forward instead of typing everyone in again.</p>',
        unsafe_allow_html=True,
    )
    st.stop()

event_key = st.session_state.event_key
event = store.load_event(DB, event_key)
dept_state = store.load_departments(DB, event_key)
shift_rows = store.load_shifts(DB, event_key) or [dict(s) for s in DEFAULT_SHIFTS]
target_rows = store.load_targets(DB, event_key)
vol_rows = store.load_volunteers(DB, event_key)

event_date = date.fromisoformat(event["event_date"])
part = event_key.rsplit(" ", 1)[0]
year = event_key.rsplit(" ", 1)[1]

with st.sidebar:
    new_date2 = st.date_input("Gathering date", value=event_date, key=f"date_{event_key}")
    new_venue = st.text_input("Venue", value=event.get("venue") or "", key=f"venue_{event_key}")
    if new_date2 != event_date or new_venue != (event.get("venue") or ""):
        store.save_event(DB, event_key, new_date2, new_venue, event["checklist"], actor=actor)
        st.rerun()
    st.divider()
    st.caption(DB.label)

    with st.expander("Check the setup"):
        st.markdown(
            '<p class="note">Runs the same checks as check_setup.py, against what '
            'this app can actually see.</p>',
            unsafe_allow_html=True,
        )
        if st.button("Run checks", key="run_checks"):
            all_secrets.clear()  # so an edit to secrets.toml is picked up now
            try:
                import check_setup

                results = check_setup.run_all(all_secrets())
            except ModuleNotFoundError:
                results = [("Setup", "fail", "check_setup.py is not next to app.py.")]
            except Exception as exc:  # noqa: BLE001
                results = [("Setup", "fail", f"The checks stopped: {exc}")]

            st.session_state["check_results"] = results
            st.session_state["check_where"] = (
                f"{len(all_secrets())} blocks visible: "
                + (", ".join(sorted(all_secrets())) or "none")
            )

        if st.session_state.get("check_results"):
            st.markdown(
                f'<p class="note">{st.session_state["check_where"]}</p>',
                unsafe_allow_html=True,
            )
            icon = {"ok": "✓", "fail": "✗", "warn": "!"}
            last = None
            for section, status, message in st.session_state["check_results"]:
                if section != last:
                    st.markdown(f"**{section}**")
                    last = section
                colour = {"ok": "var(--done-ok)", "fail": "var(--signal)"}.get(
                    status, "var(--ink-soft)"
                )
                st.markdown(
                    f'<p class="note" style="margin:.1rem 0"><span style="color:{colour};'
                    f'font-weight:700">{icon[status]}</span> {message}</p>',
                    unsafe_allow_html=True,
                )

    if DB.kind != "postgres":
        with st.expander("Move to Neon Postgres"):
            st.markdown(
                '<p class="note">A file-backed database is fine on one laptop. '
                'Deployed, it is wiped on every redeploy and two people editing it '
                'overwrite each other.</p>',
                unsafe_allow_html=True,
            )
            st.markdown(NEON_STEPS)
    if st.button("Sign out"):
        st.session_state.role = None
        st.rerun()

today = date.today()
deadline = event_date - timedelta(weeks=4)
days_out = (event_date - today).days
days_to_deadline = (deadline - today).days
late = days_to_deadline < 0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def volunteers_df() -> pd.DataFrame:
    if not vol_rows:
        return pd.DataFrame(columns=VOLUNTEER_COLUMNS)
    df = pd.DataFrame(
        [{k: r.get(v, "") or "" for k, v in DB_FIELDS.items()} for r in vol_rows],
        columns=VOLUNTEER_COLUMNS,
    )
    return df.astype(str)


def save_volunteers(df: pd.DataFrame) -> None:
    rows = [
        {DB_FIELDS[c]: str(r.get(c, "") or "") for c in VOLUNTEER_COLUMNS}
        for r in df.fillna("").to_dict("records")
    ]
    store.save_volunteers(DB, event_key, rows, actor=actor)


def congregation_options() -> list[str]:
    """The standing list, plus any spelling already in the data."""
    seen = {str(r.get("congregation") or "").strip() for r in vol_rows}
    extra = sorted(c for c in seen if c and c not in CONGREGATIONS)
    return CONGREGATIONS + extra


def shifts_df() -> pd.DataFrame:
    return pd.DataFrame(shift_rows, columns=SHIFT_COLUMNS).astype(str)


def shift_names() -> list[str]:
    names = [str(s.get("Shift", "")).strip() for s in shift_rows]
    return [n for n in names if n] or [s["Shift"] for s in DEFAULT_SHIFTS]


def shift_record(name: str) -> dict:
    for s in shift_rows:
        if str(s.get("Shift", "")).strip().lower() == str(name).strip().lower():
            return s
    return {}


def shift_half(name: str) -> str:
    half = shift_record(name).get("Half", "")
    if half in HALVES:
        return half
    return name if name in HALVES else "Morning"


def shift_hours(name: str) -> str:
    s = shift_record(name)
    return f"{s['Start']}–{s['End']}" if s.get("Start") and s.get("End") else ""


def dept_record(dept: str) -> dict:
    return dept_state.get(dept, dict(DEPT_FIELDS))


def targets_df() -> pd.DataFrame:
    saved = {(str(t["Department"]), str(t["Shift"])): t for t in target_rows}
    rows = []
    for dept in ALL_DEPTS:
        for shift in shift_names():
            t = saved.get((dept, shift), {})
            rows.append(
                {
                    "Department": dept,
                    "Shift": shift,
                    "Needed": int(t.get("Needed") or 0),
                    "Min elders": int(t.get("Min elders") or 0),
                    "Min servants": int(t.get("Min servants") or 0),
                }
            )
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def staffing_rows() -> list[dict]:
    df = volunteers_df()
    counted = df[(df["Name"].str.strip() != "") & (df["Status"] == "Confirmed")] if not df.empty else df
    out = []
    for t in target_rows:
        needed, min_e, min_s = t["Needed"], t["Min elders"], t["Min servants"]
        if not (needed or min_e or min_s):
            continue
        pool = (
            counted[(counted["Department"] == t["Department"]) & (counted["Shift"] == t["Shift"])]
            if not counted.empty
            else counted
        )
        have = len(pool)
        out.append(
            {
                "Department": t["Department"],
                "Shift": t["Shift"],
                "have": have,
                "needed": needed,
                "short": max(needed - have, 0),
                "elders": int((pool["Privilege"] == "Elder").sum()) if have else 0,
                "min_elders": min_e,
                "servants": int((pool["Privilege"] == "Servant").sum()) if have else 0,
                "min_servants": min_s,
            }
        )
    return out


def dept_progress(dept: str) -> tuple[int, int]:
    d = dept_record(dept)
    checks = [
        bool(d.get("overseer")),
        bool(d.get("meeting_date")),
        bool(d.get("training_set")),
        bool(d.get("hazard_done")),
    ]
    return sum(checks), len(checks)


def progress_bar(done: int, total: int) -> None:
    pct = 0 if not total else round(done / total * 100)
    st.markdown(f'<div class="bar"><span style="width:{pct}%"></span></div>', unsafe_allow_html=True)


def status_class(done: int, total: int) -> str:
    if done == total:
        return "ready"
    return "behind" if done == 0 else "part"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown(f"### General Gathering {part}, {year}")
st.markdown(
    f"""
<div class="countdown {'late' if late else ''}">
  <div class="days">{max(days_out, 0)}<small>days to the gathering</small></div>
  <div class="meta">Gathering date<b>{event_date:%A, %d %B %Y}</b></div>
  <div class="meta">Recruitment closes<b>{deadline:%d %B} &nbsp;·&nbsp; {'closed' if late else f'{days_to_deadline} days left'}</b></div>
</div>
""",
    unsafe_allow_html=True,
)

tab_overview, tab_depts, tab_vols, tab_follow, tab_docs = st.tabs(
    ["Readiness", "Departments", "Volunteers", "Follow-up", "Documents"]
)

# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

with tab_overview:
    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("#### Your checklist")
        items = CHECKLIST[role]
        saved = dict(event["checklist"])
        done = sum(1 for k, _ in items if saved.get(k))
        progress_bar(done, len(items))
        st.markdown(f'<p class="note">{done} of {len(items)} confirmed</p>', unsafe_allow_html=True)

        changed = False
        for key, label in items:
            value = st.checkbox(label, value=bool(saved.get(key)), key=f"chk_{event_key}_{key}")
            if value != bool(saved.get(key)):
                saved[key] = value
                changed = True
        if changed:
            store.save_event(DB, event_key, event_date, event.get("venue") or "", saved, actor=actor)
            st.rerun()

    with right:
        st.markdown("#### Departments")
        staffing = staffing_rows()
        by_dept: dict[str, dict[str, int]] = {}
        for s in staffing:
            agg = by_dept.setdefault(s["Department"], {"have": 0, "needed": 0, "short": 0})
            agg["have"] += s["have"]
            agg["needed"] += s["needed"]
            agg["short"] += s["short"]

        ready_total = 0
        for dept in ALL_DEPTS:
            d_done, d_total = dept_progress(dept)
            ready_total += d_done == d_total
            agg = by_dept.get(dept)
            staff = (
                f' &nbsp;·&nbsp; <span class="{"short" if agg["short"] else ""}">'
                f'{agg["have"]}/{agg["needed"]} volunteers</span>'
                if agg and agg["needed"]
                else ""
            )
            st.markdown(
                f'<div class="row {status_class(d_done, d_total)}">'
                f'<span><span class="name">{dept}</span>'
                f'<span class="owner">{DEPT_OWNER[dept]}</span></span>'
                f'<span class="state">{d_done}/{d_total} steps{staff}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<p class="note" style="margin-top:.8rem">{ready_total} of {len(ALL_DEPTS)} '
            f'departments fully prepared</p>',
            unsafe_allow_html=True,
        )

        gaps = []
        for s in staffing:
            bits = []
            if s["short"]:
                bits.append(f"{s['short']} more")
            if s["elders"] < s["min_elders"]:
                bits.append(f"{s['min_elders'] - s['elders']} more elder(s)")
            if s["servants"] < s["min_servants"]:
                bits.append(f"{s['min_servants'] - s['servants']} more servant(s)")
            if bits:
                gaps.append(f"{s['Department']}, {s['Shift']}: {', '.join(bits)}")

        st.markdown("#### Still short")
        if not staffing:
            st.markdown(
                '<p class="note">No headcount set yet. Enter how many each department '
                'needs per shift on the Departments tab.</p>',
                unsafe_allow_html=True,
            )
        elif gaps:
            st.markdown(
                f'<p class="note">{sum(s["short"] for s in staffing)} confirmed volunteers '
                f'short across {len(gaps)} shifts.</p>',
                unsafe_allow_html=True,
            )
            for g in gaps:
                st.markdown(f'<div class="row behind"><span>{g}</span></div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<p class="note">Every shift with a target set is fully staffed.</p>',
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

with tab_depts:
    mine = [d for d in ALL_DEPTS if DEPT_OWNER[d] == role]
    scope = st.radio("Show", ["My departments", "All departments"], horizontal=True, key="scope")
    options = mine if scope == "My departments" else ALL_DEPTS
    dept = st.selectbox("Department", options, key="dept_pick")
    d = dept_record(dept)

    with st.form(f"dept_form_{dept}"):
        c1, c2 = st.columns(2, gap="large")
        with c1:
            overseer = st.text_input("Department overseer", value=d.get("overseer", ""))
            assistant = st.text_input("Assistant", value=d.get("assistant", ""))
            keymen = st.text_input("Keymen", value=d.get("keymen", ""), help="Separate with commas")
        with c2:
            meet = st.date_input(
                "Pre-gathering meeting",
                value=date.fromisoformat(d["meeting_date"])
                if d.get("meeting_date")
                else event_date - timedelta(days=21),
            )
            attending = st.checkbox("I plan to attend this meeting", value=d.get("attending", False))
            training = st.checkbox("Volunteer training date agreed", value=d.get("training_set", False))
            hazard = st.checkbox("Job hazard form received", value=d.get("hazard_done", False))
        notes = st.text_area("Notes", value=d.get("notes", ""), height=90)

        if st.form_submit_button("Save department", type="primary"):
            store.save_department(
                DB,
                event_key,
                dept,
                {
                    "overseer": overseer,
                    "assistant": assistant,
                    "keymen": keymen,
                    "meeting_date": meet.isoformat(),
                    "attending": attending,
                    "training_set": training,
                    "hazard_done": hazard,
                    "notes": notes,
                },
                actor=actor,
            )
            st.rerun()

    st.markdown("#### How many are needed")
    st.markdown(
        '<p class="note">Headcount per shift. Leave a row at zero if this department '
        'does not work that shift. Only confirmed volunteers count towards it.</p>',
        unsafe_allow_html=True,
    )
    dept_targets = targets_df()
    dept_targets = dept_targets[dept_targets["Department"] == dept].set_index("Shift")
    edited_targets = st.data_editor(
        dept_targets[["Needed", "Min elders", "Min servants"]],
        num_rows="fixed",
        width="stretch",
        key=f"targets_{event_key}_{dept}",
        column_config={
            c: st.column_config.NumberColumn(min_value=0, step=1, width="small")
            for c in ("Needed", "Min elders", "Min servants")
        },
    )
    new_targets = [
        {"Shift": str(s), **{k: int(r.get(k) or 0) for k in ("Needed", "Min elders", "Min servants")}}
        for s, r in edited_targets.iterrows()
    ]
    current = [
        {
            "Shift": t["Shift"],
            "Needed": t["Needed"],
            "Min elders": t["Min elders"],
            "Min servants": t["Min servants"],
        }
        for t in target_rows
        if t["Department"] == dept
    ]
    if sorted([t for t in new_targets if any(v for k, v in t.items() if k != "Shift")], key=str) != sorted(current, key=str):
        store.save_dept_targets(DB, event_key, dept, new_targets, actor=actor)
        st.rerun()

    st.markdown("#### Volunteers in this department")
    df = volunteers_df()
    assigned = df[df["Department"] == dept] if not df.empty else df
    if assigned.empty:
        st.markdown(
            '<p class="note">No one is assigned yet. Add names on the Volunteers tab.</p>',
            unsafe_allow_html=True,
        )
    else:
        counts = assigned.groupby("Shift").size().to_dict()
        line = " &nbsp;·&nbsp; ".join(
            f"{n} {counts.get(n, 0)}" for n in shift_names() if counts.get(n, 0)
        )
        st.markdown(f'<p class="note">{line or "No shift assigned yet"}</p>', unsafe_allow_html=True)
        st.dataframe(assigned, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Volunteers
# ---------------------------------------------------------------------------

with tab_vols:
    with st.expander("Shift times"):
        st.markdown(
            '<p class="note">Half decides who counts as working the morning or the '
            'afternoon, so a setup shift that ends before the programme still belongs '
            'to the morning.</p>',
            unsafe_allow_html=True,
        )
        shift_edit = st.data_editor(
            shifts_df(),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=f"shift_editor_{event_key}",
            column_config={
                "Shift": st.column_config.TextColumn(required=True, width="medium"),
                "Half": st.column_config.SelectboxColumn(options=HALVES, required=True, width="small"),
                "Report": st.column_config.TextColumn("Report at", validate=TIME_PATTERN, width="small"),
                "Start": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
                "End": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
            },
        )
        new_shifts = shift_edit.fillna("").to_dict("records")
        if new_shifts != shift_rows:
            old_names = [str(s["Shift"]).strip() for s in shift_rows]
            new_names = [str(s.get("Shift", "")).strip() for s in new_shifts]
            notes = []
            for i in range(min(len(old_names), len(new_names))):
                old, new = old_names[i], new_names[i]
                if old and new and old != new and old not in new_names and new not in old_names:
                    moved = store.rename_shift(DB, event_key, old, new, actor=actor)
                    notes.append(f"{old} became {new}, {moved} volunteers moved with it")
            store.save_shifts(DB, event_key, new_shifts, actor=actor)
            for n in notes:
                st.session_state.setdefault("flash", []).append(n)
            st.rerun()

    for note in st.session_state.pop("flash", []):
        st.success(note)

    stranded = store.stranded_shifts(DB, event_key)
    if stranded:
        st.warning(
            "On a shift that no longer exists: "
            + ", ".join(f"{s['n']} in {s['shift']}" for s in stranded)
            + ". Add that shift back, or reassign them below."
        )

    with st.expander("Paste a list of names"):
        st.markdown(
            '<p class="note">One name per line. They go in as Invited, and anyone '
            'already on the list is skipped.</p>',
            unsafe_allow_html=True,
        )
        p1, p2, p3 = st.columns(3)
        paste_dept = p1.selectbox("Into department", ALL_DEPTS, key="paste_dept")
        paste_shift = p2.selectbox("On shift", shift_names(), key="paste_shift")
        paste_cong = p3.selectbox(
            "Congregation",
            [""] + congregation_options(),
            help="Used for any line that does not name one after a comma",
            key="paste_cong",
        )
        pasted = st.text_area("Names", height=120, key="paste_names")
        if st.button("Add these names") and pasted.strip():
            n = store.add_names(
                DB,
                event_key,
                pasted.splitlines(),
                paste_dept,
                paste_shift,
                paste_cong.strip(),
                actor=actor,
            )
            st.session_state.setdefault("flash", []).append(
                f"{n} added to {paste_dept}, {paste_shift}."
            )
            st.rerun()

    previous = store.served_before(DB, event_key)
    if previous:
        with st.expander(f"Served before but not on this list ({len(previous)})"):
            st.markdown(
                '<p class="note">People from earlier gatherings. Adding them here '
                'keeps their history rather than creating a second record.</p>',
                unsafe_allow_html=True,
            )
            look = {
                f"{p['name']}{' — ' + p['congregation'] if p['congregation'] else ''}"
                f" ({p['times']}×)": p["id"]
                for p in previous
            }
            picked = st.multiselect("Who to add", list(look), key="prev_pick")
            b1, b2 = st.columns(2)
            back_dept = b1.selectbox("Department", ALL_DEPTS, key="prev_dept")
            back_shift = b2.selectbox("Shift", shift_names(), key="prev_shift")
            if st.button("Add to this gathering", disabled=not picked):
                for label in picked:
                    store.add_person_to_event(
                        DB, event_key, look[label], back_dept, back_shift, actor=actor
                    )
                store.log(
                    DB,
                    actor,
                    event_key,
                    "volunteers",
                    f"{len(picked)} returning volunteers added to {back_dept}",
                )
                st.rerun()

    removed_rows = store.load_removed(DB, event_key)
    if removed_rows:
        with st.expander(f"Recently removed ({len(removed_rows)})"):
            st.markdown(
                '<p class="note">Nothing is deleted outright. Put anyone back if a '
                'row went by mistake.</p>',
                unsafe_allow_html=True,
            )
            for r in removed_rows[:15]:
                c1, c2 = st.columns([4, 1])
                c1.markdown(
                    f'<div class="row"><span><span class="name">{r["name"]}</span>'
                    f'<span class="owner">{r["dept"] or "no department"}'
                    f'{", " + r["shift"] if r["shift"] else ""}</span></span>'
                    f'<span class="state">{(r["removed_at"] or "")[:16].replace("T", " ")}'
                    "</span></div>",
                    unsafe_allow_html=True,
                )
                if c2.button("Restore", key=f"restore_{r['id']}"):
                    store.restore_assignment(DB, r["id"], event_key, actor=actor)
                    st.rerun()

    st.markdown("#### Master volunteer list")
    base = volunteers_df()
    f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1.5, 1.5])
    search = f1.text_input("Find by name", key=f"f_name_{event_key}")
    f_cong = f2.multiselect("Congregation", congregation_options(), key=f"f_cong_{event_key}")
    f_dept = f3.multiselect("Department", ALL_DEPTS, key=f"f_dept_{event_key}")
    f_shift = f4.multiselect("Shift", shift_names(), key=f"f_shift_{event_key}")
    f_status = f5.multiselect("Status", VOLUNTEER_STATUSES, key=f"f_status_{event_key}")

    filtering = bool(search.strip() or f_cong or f_dept or f_shift or f_status)
    view = base
    if filtering and not base.empty:
        mask = pd.Series(True, index=base.index)
        if search.strip():
            term = search.strip()
            mask &= base["Name"].str.contains(term, case=False, na=False) | base[
                "Congregation"
            ].str.contains(term, case=False, na=False)
        if f_cong:
            mask &= base["Congregation"].isin(f_cong)
        if f_dept:
            mask &= base["Department"].isin(f_dept)
        if f_shift:
            mask &= base["Shift"].isin(f_shift)
        if f_status:
            mask &= base["Status"].isin(f_status)
        view = base[mask]
        st.caption(f"{len(view)} of {len(base)}. Clear the filters to add or delete rows.")

    edited = st.data_editor(
        view,
        num_rows="fixed" if filtering else "dynamic",
        hide_index=True,
        width="stretch",
        key=f"vol_editor_{event_key}",
        column_config={
            "Name": st.column_config.TextColumn(required=True, width="medium"),
            "Gender": st.column_config.SelectboxColumn(options=GENDERS, width="small"),
            "Privilege": st.column_config.SelectboxColumn(options=PRIVILEGES, width="small"),
            "Congregation": st.column_config.SelectboxColumn(
                options=congregation_options(), width="medium"
            ),
            "Department": st.column_config.SelectboxColumn(options=ALL_DEPTS, width="medium"),
            "Shift": st.column_config.SelectboxColumn(options=shift_names(), width="small"),
            "Status": st.column_config.SelectboxColumn(options=VOLUNTEER_STATUSES, width="small"),
            "Notes": st.column_config.TextColumn(width="large"),
        },
    )

    if filtering:
        merged = base.copy()
        merged.loc[edited.index] = edited
        result = merged
    else:
        result = edited

    if result.fillna("").to_dict("records") != base.to_dict("records"):
        save_volunteers(result)
        st.rerun()

    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df

    if named.empty:
        st.markdown(
            '<p class="note">Add a row above to start the list.</p>', unsafe_allow_html=True
        )
    else:
        halves = named["Shift"].map(shift_half)
        keys_l = named["Name"].str.strip().str.lower()
        both = halves.groupby(keys_l).nunique().pipe(lambda s: s[s > 1])
        if len(both):
            names = named[keys_l.isin(both.index)]["Name"].unique()
            st.warning("Assigned to both halves of the day: " + ", ".join(sorted(names)))

        empty_depts = [d for d in ALL_DEPTS if d not in set(named["Department"])]
        if empty_depts:
            st.info("No volunteers yet: " + ", ".join(empty_depts))

        summary = (
            named.pivot_table(index="Department", columns="Shift", values="Name", aggfunc="count")
            .reindex(ALL_DEPTS)
            .fillna(0)
            .astype(int)
        )
        for n in shift_names():
            if n not in summary.columns:
                summary[n] = 0
        summary = summary[[n for n in shift_names() if n in summary.columns]]
        st.markdown("#### Coverage by shift")
        st.caption(" · ".join(f"{n} {shift_hours(n)}".strip() for n in shift_names() if shift_hours(n)))
        st.dataframe(summary, width="stretch")

        mix = pd.DataFrame(index=ALL_DEPTS)
        for p in PRIVILEGES:
            mix[p] = named[named["Privilege"] == p].groupby("Department").size()
        for g in GENDERS:
            mix[g] = named[named["Gender"] == g].groupby("Department").size()
        st.markdown("#### Make-up of each department")
        st.dataframe(mix.fillna(0).astype(int), width="stretch")

        with_cong = named[named["Congregation"].str.strip() != ""]
        if not with_cong.empty:
            spread = (
                with_cong.pivot_table(
                    index="Congregation", columns="Status", values="Name", aggfunc="count"
                )
                .fillna(0)
                .astype(int)
            )
            spread["Total"] = spread.sum(axis=1)
            st.markdown("#### Where they come from")
            st.caption("Useful when one congregation is carrying more than its share")
            st.dataframe(spread.sort_values("Total", ascending=False), width="stretch")

            missing = len(named) - len(with_cong)
            if missing:
                st.markdown(
                    f'<p class="note">{missing} volunteers have no congregation set.</p>',
                    unsafe_allow_html=True,
                )

# ---------------------------------------------------------------------------
# Follow-up
# ---------------------------------------------------------------------------

with tab_follow:
    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df
    pending = named[named["Status"] == "Invited"] if not named.empty else named

    st.markdown("#### Who still owes you an answer")
    st.markdown(
        f'<p class="note">{f"Recruitment closed {-days_to_deadline} days ago" if late else f"{days_to_deadline} days until recruitment closes"}.</p>',
        unsafe_allow_html=True,
    )

    if pending.empty:
        st.markdown(
            '<p class="note">Nobody is sitting at Invited.</p>', unsafe_allow_html=True
        )
    else:
        counts = pending.groupby("Department").size().sort_values(ascending=False)
        for dept_name, n in counts.items():
            d = dept_record(dept_name)
            who = d.get("overseer") or "no overseer named"
            st.markdown(
                f'<div class="row part"><span><span class="name">{dept_name}</span>'
                f'<span class="owner">ask {who}</span></span>'
                f'<span class="state">{n} awaiting reply</span></div>',
                unsafe_allow_html=True,
            )

        chase_dept = st.selectbox("Department", ["All"] + sorted(counts.index), key="chase_dept")
        rows = pending if chase_dept == "All" else pending[pending["Department"] == chase_dept]
        st.dataframe(
            rows[["Name", "Department", "Shift", "Notes"]], hide_index=True, width="stretch"
        )

        lines = [f"General Gathering {part}, {year} — {event_date:%d %B}"]
        for dept_name, group in rows.groupby("Department"):
            lines.append(f"\n{dept_name}:")
            for _, v in group.iterrows():
                lines.append(f"  {v['Name']} ({v['Shift']})")
        st.text_area("Copy this into a message", "\n".join(lines), height=200, key="chase_text")

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

PRINT_CSS = """
 @page { size: A4; margin: 15mm; }
 body { font-family: Helvetica, Arial, sans-serif; color:#16202b; margin:0; font-size:10pt; }
 h1 { font-size:17pt; margin:0 0 2mm; }
 p.sub { color:#5b6b7c; margin:0 0 8mm; font-size:9pt; }
 section { break-inside:avoid; page-break-inside:avoid; margin:0 0 7mm; }
 h2 { font-size:12pt; margin:0 0 1mm; border-bottom:2px solid #16202b; padding-bottom:1mm; }
 p.lead { color:#5b6b7c; font-size:8.5pt; margin:1.5mm 0 3mm; }
 table { border-collapse:collapse; width:100%; font-size:9pt; }
 th, td { border-bottom:1px solid #dcd8cf; text-align:left; padding:1.6mm 2mm; vertical-align:top; }
 th { background:#f7f5f0; font-size:8.5pt; letter-spacing:.02em; }
 td.num { color:#5b6b7c; width:8mm; }
"""


def dept_lead(dept: str) -> str:
    d = dept_record(dept)
    bits = []
    if d.get("overseer"):
        bits.append(f"Overseer: {d['overseer']}")
    if d.get("assistant"):
        bits.append(f"Assistant: {d['assistant']}")
    if d.get("keymen"):
        bits.append(f"Keymen: {d['keymen']}")
    return " &nbsp;·&nbsp; ".join(bits) or "No oversight recorded"


def master_list_html(frame: pd.DataFrame, include_empty: bool = False) -> str:
    sections = []
    for dept in ALL_DEPTS:
        rows = frame[frame["Department"] == dept].sort_values(["Shift", "Name"])
        if rows.empty and not include_empty:
            continue
        body = "".join(
            f"<tr><td class='num'>{i}</td><td>{r['Name']}</td><td>{r['Congregation']}</td>"
            f"<td>{r['Gender']}</td><td>{r['Privilege']}</td>"
            f"<td>{r['Shift']}</td><td>{r['Status']}</td></tr>"
            for i, (_, r) in enumerate(rows.iterrows(), start=1)
        ) or "<tr><td colspan='7'>No volunteers recorded.</td></tr>"
        sections.append(
            f"<section><h2>{dept}</h2><p class='lead'>{dept_lead(dept)}</p>"
            "<table><tr><th></th><th>Name</th><th>Congregation</th><th>Gender</th>"
            f"<th>Privilege</th><th>Shift</th><th>Status</th></tr>{body}</table>"
            f"<p class='lead'>{len(rows)} volunteers</p></section>"
        )
    return f"""<!doctype html><meta charset="utf-8">
<title>Master volunteer list — {part} {year}</title><style>{PRINT_CSS}</style>
<h1>Master volunteer list</h1>
<p class="sub">General Gathering {part}, {year} &middot; {event_date:%A, %d %B %Y}
{' &middot; ' + (event.get('venue') or '') if event.get('venue') else ''}
&middot; {len(frame)} volunteers &middot; printed {today:%d %B %Y}</p>
{''.join(sections)}"""


def rotation_frame(frame: pd.DataFrame, dept: str) -> pd.DataFrame:
    cols = {}
    for name in shift_names():
        people = sorted(
            frame[(frame["Department"] == dept) & (frame["Shift"] == name)]["Name"].tolist()
        )
        cols[name] = people
    depth = max((len(v) for v in cols.values()), default=0)
    return pd.DataFrame({k: v + [""] * (depth - len(v)) for k, v in cols.items()})


def rotation_html(frame: pd.DataFrame) -> str:
    sections = []
    for dept in ALL_DEPTS:
        table = rotation_frame(frame, dept)
        if table.empty:
            continue
        heads = "".join(
            f"<th>{n}<br><span style='font-weight:400'>{shift_hours(n) or '—'}</span></th>"
            for n in table.columns
        )
        body = "".join(
            "<tr><td class='num'>" + str(i + 1) + "</td>"
            + "".join(f"<td>{v}</td>" for v in row)
            + "</tr>"
            for i, row in enumerate(table.itertuples(index=False))
        )
        counts = " &nbsp;·&nbsp; ".join(
            f"{c}: {(table[c] != '').sum()}" for c in table.columns
        )
        sections.append(
            f"<section><h2>{dept}</h2><p class='lead'>{dept_lead(dept)}</p>"
            f"<table><tr><th></th>{heads}</tr>{body}</table>"
            f"<p class='lead'>{counts}</p></section>"
        )
    return f"""<!doctype html><meta charset="utf-8">
<title>Rotation list — {part} {year}</title><style>{PRINT_CSS}</style>
<h1>Department rotation list</h1>
<p class="sub">General Gathering {part}, {year} &middot; {event_date:%A, %d %B %Y}
{' &middot; ' + (event.get('venue') or '') if event.get('venue') else ''}
&middot; printed {today:%d %B %Y}</p>
{''.join(sections) or '<p>No volunteers recorded.</p>'}"""


def master_csv(frame: pd.DataFrame) -> str:
    out = []
    for dept in ALL_DEPTS:
        d = dept_record(dept)
        for _, r in frame[frame["Department"] == dept].sort_values(["Shift", "Name"]).iterrows():
            out.append(
                {
                    "Department": dept,
                    "Dept overseer": d.get("overseer", ""),
                    "Dept assistant": d.get("assistant", ""),
                    "Keymen": d.get("keymen", ""),
                    "Name": r["Name"],
                    "Congregation": r["Congregation"],
                    "Gender": r["Gender"],
                    "Privilege": r["Privilege"],
                    "Shift": r["Shift"],
                    "Status": r["Status"],
                }
            )
    return pd.DataFrame(out).to_csv(index=False)


def rotation_csv(frame: pd.DataFrame) -> str:
    out = []
    for dept in ALL_DEPTS:
        table = rotation_frame(frame, dept)
        for _, row in table.iterrows():
            out.append({"Department": dept, **row.to_dict()})
    return pd.DataFrame(out).to_csv(index=False)


with tab_docs:
    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df
    stem = f"{part.replace(' ', '')}_{year}"

    c_a, c_b = st.columns(2)
    only_confirmed = c_a.checkbox("Confirmed volunteers only", value=True, key="doc_conf")
    include_empty = c_b.checkbox("Include departments with nobody yet", value=False, key="doc_empty")
    frame = named[named["Status"] == "Confirmed"] if only_confirmed and not named.empty else named

    st.markdown("#### Master list")
    st.markdown(
        '<p class="note">Every department in turn, each with its overseer, assistant '
        'and keymen above the names.</p>',
        unsafe_allow_html=True,
    )
    m1, m2 = st.columns(2)
    m1.download_button(
        "Master list (print)",
        master_list_html(frame, include_empty),
        file_name=f"{stem}_master_list.html",
        mime="text/html",
        type="primary",
        width="stretch",
    )
    m2.download_button(
        "Master list (CSV)",
        master_csv(frame),
        file_name=f"{stem}_master_list.csv",
        mime="text/csv",
        width="stretch",
    )

    st.markdown("#### Rotation list")
    st.markdown(
        '<p class="note">One column per shift with its hours, so each department can '
        'see who is on in the morning and who takes over in the afternoon.</p>',
        unsafe_allow_html=True,
    )
    r1, r2 = st.columns(2)
    r1.download_button(
        "Rotation list (print)",
        rotation_html(frame),
        file_name=f"{stem}_rotation.html",
        mime="text/html",
        type="primary",
        width="stretch",
    )
    r2.download_button(
        "Rotation list (CSV)",
        rotation_csv(frame),
        file_name=f"{stem}_rotation.csv",
        mime="text/csv",
        width="stretch",
    )

    st.divider()
    st.markdown("#### Mirror to Google Sheets")
    st.markdown(
        '<p class="note">Writes both documents plus a copy of every table, so the '
        'data can be read — and recovered — without the app. One way only: the app '
        'never reads back from Sheets.</p>',
        unsafe_allow_html=True,
    )

    gaps = sheets_problems()
    if gaps:
        st.markdown(
            '<p class="note">Not ready yet: ' + "; ".join(gaps) + ".</p>",
            unsafe_allow_html=True,
        )
        if not local_secrets_path().exists():
            st.markdown(
                f'<p class="note">No secrets file at {local_secrets_path()} either. '
                "If yours lives elsewhere, Streamlit only reads the one in the folder "
                "you launched it from.</p>",
                unsafe_allow_html=True,
            )
        with st.expander("How to set Sheets up"):
            st.markdown(SHEETS_STEPS)

    last_push = store.get_meta(DB, "last_sheets_push")
    if last_push:
        st.markdown(
            f'<p class="note">Last pushed {last_push["at"].replace("T", " ")[:16]}.</p>',
            unsafe_allow_html=True,
        )
        stale = (datetime.now() - datetime.fromisoformat(last_push["at"])).days
        if stale >= 7:
            st.warning(f"The Sheets copy is {stale} days old.")
    else:
        st.markdown(
            '<p class="note">Never pushed.</p>', unsafe_allow_html=True
        )

    if st.button("Push to Sheets", disabled=not sheets_ready()):
        try:
            import gspread

            creds = secret_block("gcp_service_account")
            sheet_id = str(secret_block("sheets").get("spreadsheet_id", ""))
            client = gspread.service_account_from_dict(creds)
            book = client.open_by_key(sheet_id)

            master_rows = pd.read_csv(io.StringIO(master_csv(frame))).fillna("")
            rot_rows = pd.read_csv(io.StringIO(rotation_csv(frame))).fillna("")
            pages = [
                (f"{event_key} master", master_rows),
                (f"{event_key} rotation", rot_rows),
            ]
            dump = store.export_all(DB)
            for table, rows in dump["tables"].items():
                pages.append((f"data {table}", pd.DataFrame(rows).fillna("")))

            for title, table in pages:
                try:
                    sheet = book.worksheet(title)
                    sheet.clear()
                except Exception:
                    sheet = book.add_worksheet(title, rows=400, cols=26)
                if table.empty:
                    continue
                sheet.update([table.columns.tolist()] + table.astype(str).values.tolist())

            store.set_meta(DB, "last_sheets_push", str(len(pages)))
            store.log(DB, actor, event_key, "backup", f"{len(pages)} sheets pushed")
            st.success(f"{len(pages)} sheets updated.")
        except KeyError as exc:
            st.error(f"Sheets configuration is incomplete: {exc}")
        except ModuleNotFoundError:
            st.error("gspread is not installed. Add gspread to requirements.txt.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Sheets rejected the update: {exc}")

    st.divider()
    st.markdown("#### Backup file")
    last_dl = store.get_meta(DB, "last_backup")
    st.markdown(
        '<p class="note">Everything in the database — every gathering, not just this '
        'one. The file name carries the date and time, so downloads pile up as a '
        'history instead of overwriting each other.'
        + (f' Last taken {last_dl["at"].replace("T", " ")[:16]}.' if last_dl else "")
        + "</p>",
        unsafe_allow_html=True,
    )

    dump = store.export_all(DB)
    facts = store.describe_backup(dump)
    st.markdown(
        f'<p class="note">{facts["people"]} people across '
        f'{len(facts["gatherings"])} '
        f'{"gathering" if len(facts["gatherings"]) == 1 else "gatherings"}.</p>',
        unsafe_allow_html=True,
    )
    if st.download_button(
        "Download backup (JSON)",
        json.dumps(dump, indent=1),
        file_name=f"gathering_backup_{datetime.now():%Y%m%d_%H%M}.json",
        mime="application/json",
    ):
        store.set_meta(DB, "last_backup", facts["exported_at"])

    with st.expander("Restore from a backup file"):
        uploaded = st.file_uploader("Backup file", type="json", key="restore_file")
        if uploaded is not None:
            try:
                incoming = json.load(uploaded)
                summary = store.describe_backup(incoming)
            except Exception as exc:  # noqa: BLE001
                st.error(f"That file could not be read as a backup: {exc}")
                summary = None

            if summary:
                st.markdown(
                    f'<p class="note">Taken {summary["exported_at"].replace("T", " ")[:16]}, '
                    f'holding {summary["people"]} people.</p>',
                    unsafe_allow_html=True,
                )
                if summary["gatherings"]:
                    st.dataframe(
                        pd.DataFrame(summary["gatherings"]), hide_index=True, width="stretch"
                    )
                mode = st.radio(
                    "How to apply it",
                    ["Merge", "Replace everything"],
                    horizontal=True,
                    key="restore_mode",
                    help=(
                        "Merge adds what is missing and leaves anything already here "
                        "alone. Replace empties the database first, so it ends up "
                        "matching the file exactly."
                    ),
                )
                danger = mode == "Replace everything"
                agreed = st.checkbox(
                    "Yes, wipe the current data first" if danger else "Go ahead and merge",
                    key="restore_ok",
                )
                if st.button("Restore", disabled=not agreed, type="primary"):
                    try:
                        counts = store.restore_all(
                            DB,
                            incoming,
                            "replace" if danger else "merge",
                            actor=actor,
                        )
                        written = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
                        st.success(f"Restored: {written or 'nothing new to add'}.")
                        st.session_state.event_key = None
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Restore stopped, nothing was written: {exc}")

    st.divider()
    with st.expander("Who changed what"):
        entries = store.recent_changes(DB, event_key, 50)
        if not entries:
            st.markdown('<p class="note">Nothing recorded yet.</p>', unsafe_allow_html=True)
        else:
            st.dataframe(
                pd.DataFrame(entries).rename(
                    columns={"ts": "When", "actor": "Who", "area": "Area", "detail": "What"}
                ),
                hide_index=True,
                width="stretch",
            )

    st.divider()
    with st.expander("Delete this gathering"):
        st.markdown(
            f'<p class="note">This gathering holds {len(named)} names. Records are '
            'normally kept so next year can be copied forward, so delete only if the '
            'gathering was entered by mistake.</p>',
            unsafe_allow_html=True,
        )
        sure = st.checkbox(f"Yes, delete {event_key} and everyone in it", key="del_sure")
        if st.button("Delete now", disabled=not sure):
            store.delete_event(DB, event_key, actor=actor)
            st.session_state.event_key = None
            st.rerun()
