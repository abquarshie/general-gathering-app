"""
General Gathering Management Portal
-----------------------------------
Streamlit app for the gathering overseer and assistant overseer to track
departments, volunteers and pre-gathering readiness.

Run:  streamlit run app.py
Needs: streamlit >= 1.49, pandas
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_TITLE = "Gathering Portal"
DATA_DIR = Path("data")
STATE_FILE = DATA_DIR / "portal_state.json"

OVERSEER_DEPTS = [
    "Accounts",
    "Attendant",
    "Cleaning",
    "First Aid",
    "Parking",
    "Rooming",
]
ASSISTANT_DEPTS = [
    "Audio/Video",
    "Baptism",
    "Installation",
    "Lost & Found",
    "Checkroom",
]
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

VOLUNTEER_STATUSES = ["Confirmed", "Invited", "Unavailable", "Moved out"]
GENDERS = ["Male", "Female"]
PRIVILEGES = ["Elder", "Servant", "Publisher"]
VOLUNTEER_COLUMNS = [
    "Name",
    "Gender",
    "Privilege",
    "Department",
    "Shift",
    "Status",
    "Phone",
    "Notes",
]
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

# Demo credentials, used only when no [users] block exists in secrets.
DEMO_USERS = {
    "overseer": ("pass123", "Overseer"),
    "assistant": ("pass123", "Assistant Overseer"),
}

st.set_page_config(page_title=APP_TITLE, page_icon="🗓", layout="wide")

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

CSS = """
<style>
:root {
  --ink:      #16202b;
  --ink-soft: #5b6b7c;
  --paper:    #f7f5f0;
  --line:     #dcd8cf;
  --signal:   #b4531c;   /* deadlines, attention */
  --done-ok:  #2f6b47;
}
html, body, [class*="css"] { font-feature-settings: "tnum" 1; }

/* ---- countdown strip: the one loud element ---- */
.countdown {
  background: var(--ink);
  color: var(--paper);
  padding: 1.4rem 1.6rem;
  display: flex;
  align-items: baseline;
  gap: 2.4rem;
  flex-wrap: wrap;
  border-radius: 2px;
}
.countdown .days {
  font-size: 3.6rem;
  line-height: 1;
  font-weight: 700;
  letter-spacing: -0.03em;
}
.countdown .days small {
  font-size: 1rem;
  font-weight: 400;
  opacity: .75;
  margin-left: .4rem;
  letter-spacing: 0;
}
.countdown .meta { font-size: .95rem; opacity: .8; }
.countdown .meta b { display: block; font-size: 1.05rem; opacity: 1; font-weight: 600; }
.countdown.late { background: var(--signal); }

/* ---- quiet rows instead of a grid of identical cards ---- */
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: .55rem .2rem .55rem .7rem;
  border-bottom: 1px solid var(--line);
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

/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] { gap: 1.6rem; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] {
  padding: .4rem 0; font-weight: 600; background: transparent;
}
section[data-testid="stSidebar"] { border-right: 1px solid var(--line); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Storage  (swap these two functions for Postgres/Sheets when you deploy)
# ---------------------------------------------------------------------------


def load_store() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            st.warning("Saved data could not be read. Starting a fresh copy.")
    return {}


def save_store(store: dict) -> None:
    """Write the whole store atomically, so a crash can't leave a half file."""
    DATA_DIR.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, default=str))
    tmp.replace(STATE_FILE)


def blank_event(event_date: date) -> dict:
    return {
        "event_date": event_date.isoformat(),
        "venue": "",
        "checklist": {},
        "departments": {d: dict(DEPT_FIELDS) for d in ALL_DEPTS},
        "shifts": [dict(s) for s in DEFAULT_SHIFTS],
        "targets": [],
        "volunteers": [],
    }


def migrate(ev: dict) -> dict:
    """Fill in fields added after a saved record was first written."""
    ev.setdefault("venue", "")
    ev.setdefault("checklist", {})
    ev.setdefault("volunteers", [])
    ev.setdefault("departments", {})
    ev.setdefault("targets", [])
    if not ev.get("shifts"):
        ev["shifts"] = [dict(s) for s in DEFAULT_SHIFTS]
    return ev


def persist() -> None:
    """Save the current gathering, keeping other gatherings as they are on disk.

    Re-reading first means the overseer and the assistant working at the same
    time can't wipe each other's other gatherings. Within one gathering the
    last save still wins — move to Postgres if that matters.
    """
    on_disk = load_store()
    on_disk[st.session_state.event_key] = st.session_state.event
    st.session_state.store = on_disk
    save_store(on_disk)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def configured_users() -> dict | None:
    """Users from secrets, or None when no usable [users] block is found.

    Streamlit only finds .streamlit/secrets.toml relative to the folder you
    launched it from, so we also look next to this file.
    """
    try:
        users = dict(st.secrets["users"])
        if users:
            return users
    except Exception:
        pass

    local = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if local.exists():
        try:
            import tomllib

            return dict(tomllib.loads(local.read_text()).get("users") or {}) or None
        except Exception:
            return None
    return None


def secrets_report() -> list[str]:
    """Plain-language notes about where credentials are coming from."""
    notes = [f"Streamlit was started from: {Path.cwd()}"]
    cwd_file = Path.cwd() / ".streamlit" / "secrets.toml"
    app_file = Path(__file__).parent / ".streamlit" / "secrets.toml"
    for label, p in (("Launch folder", cwd_file), ("Next to app.py", app_file)):
        notes.append(f"{label}: {p} — {'found' if p.exists() else 'not found'}")

    stray = sorted(
        str(p.name)
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
            h = str(rec.get("password_sha256", "")).strip()
            if name != name.strip().lower():
                notes.append(f"'{name}' has capitals or spaces; sign-in lowercases the name")
            if len(h) != 64:
                notes.append(f"'{name}' hash is {len(h)} characters, expected 64")
            if rec.get("role") not in ("Overseer", "Assistant Overseer"):
                notes.append(f"'{name}' role {rec.get('role')!r} is not one the app knows")
    else:
        notes.append("No accounts loaded, so only the demo sign-ins work.")
        notes.append("Section headers must read [users.overseer], not [overseer].")
    return notes


def check_login(username: str, password: str) -> str | None:
    """Return the role name on success, else None."""
    users = configured_users()
    if users:
        record = users.get(username)
        if record and hashlib.sha256(password.encode()).hexdigest() == record["password_sha256"]:
            return record["role"]
        return None
    demo = DEMO_USERS.get(username)
    return demo[1] if demo and demo[0] == password else None


def login_screen() -> None:
    st.markdown(f"## {APP_TITLE}")
    st.markdown(
        '<p class="note">Sign in to plan departments, volunteers and shifts for the gathering.</p>',
        unsafe_allow_html=True,
    )
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            role = check_login(username.strip().lower(), password)
            if role:
                st.session_state.role = role
                st.rerun()
            else:
                st.error("That username and password don't match. Check for stray spaces and try again.")
    if not configured_users():
        st.info(
            "No credentials found in secrets, so demo logins are active "
            "(overseer / pass123). Add a `[users]` block to "
            "`.streamlit/secrets.toml` before sharing this app."
        )
    with st.expander("Trouble signing in?"):
        for note in secrets_report():
            st.markdown(f'<p class="note">{note}</p>', unsafe_allow_html=True)


if "role" not in st.session_state:
    st.session_state.role = None
if st.session_state.role is None:
    login_screen()
    st.stop()

role: str = st.session_state.role

# ---------------------------------------------------------------------------
# Event selection
# ---------------------------------------------------------------------------

if "store" not in st.session_state:
    st.session_state.store = load_store()

with st.sidebar:
    st.markdown(f"**{APP_TITLE}**")
    st.caption(f"Signed in as {role}")

    part = st.selectbox("Gathering", ["Part 1", "Part 2"], key="part")
    year = st.number_input("Year", value=datetime.now().year, step=1, key="year")
    event_key = f"{part} {int(year)}"

    if event_key not in st.session_state.store:
        st.session_state.store[event_key] = blank_event(date.today() + timedelta(days=60))
        save_store(st.session_state.store)

    st.session_state.event_key = event_key
    st.session_state.event = migrate(st.session_state.store[event_key])
    event = st.session_state.event

    new_date = st.date_input(
        "Gathering date",
        value=date.fromisoformat(event["event_date"]),
        key=f"date_{event_key}",
    )
    if new_date.isoformat() != event["event_date"]:
        event["event_date"] = new_date.isoformat()
        persist()

    new_venue = st.text_input("Venue", value=event.get("venue", ""), key=f"venue_{event_key}")
    if new_venue != event.get("venue", ""):
        event["venue"] = new_venue
        persist()

    st.divider()
    if Path("/mount/src").exists():
        st.warning(
            "This is Streamlit Cloud, where saved data is wiped on every redeploy. "
            "Download a backup from the Export tab before you finish."
        )
    if st.button("Sign out"):
        st.session_state.role = None
        st.rerun()

event = st.session_state.event
event_date = date.fromisoformat(event["event_date"])
today = date.today()
deadline = event_date - timedelta(weeks=4)
days_out = (event_date - today).days
days_to_deadline = (deadline - today).days

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def volunteers_df() -> pd.DataFrame:
    rows = event.get("volunteers", [])
    df = pd.DataFrame(rows, columns=VOLUNTEER_COLUMNS) if rows else pd.DataFrame(columns=VOLUNTEER_COLUMNS)
    return df.astype(str).replace("nan", "")


def shifts_df() -> pd.DataFrame:
    rows = event.get("shifts") or [dict(s) for s in DEFAULT_SHIFTS]
    return pd.DataFrame(rows, columns=SHIFT_COLUMNS).astype(str).replace("nan", "")


def shift_names() -> list[str]:
    names = [s.get("Shift", "").strip() for s in event.get("shifts", [])]
    return [n for n in names if n] or [s["Shift"] for s in DEFAULT_SHIFTS]


def shift_record(name: str) -> dict:
    for s in event.get("shifts", []):
        if s.get("Shift", "").strip().lower() == str(name).strip().lower():
            return s
    return {}


def shift_half(name: str) -> str:
    half = shift_record(name).get("Half", "")
    if half in HALVES:
        return half
    return name if name in HALVES else "Morning"


def shift_hours(name: str) -> str:
    s = shift_record(name)
    if s.get("Start") and s.get("End"):
        return f"{s['Start']}–{s['End']}"
    return ""


def targets_df() -> pd.DataFrame:
    """One row per department and shift, carrying over anything already set."""
    saved = {
        (str(t.get("Department")), str(t.get("Shift"))): t for t in event.get("targets", [])
    }
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


def target_for(dept: str, shift: str) -> dict:
    for t in event.get("targets", []):
        if str(t.get("Department")) == dept and str(t.get("Shift")) == shift:
            return t
    return {"Needed": 0, "Min elders": 0, "Min servants": 0}


def staffing_rows() -> list[dict]:
    """Have-versus-need for every department and shift with a target set."""
    df = volunteers_df()
    counted = df[(df["Name"].str.strip() != "") & (df["Status"] == "Confirmed")] if not df.empty else df
    out = []
    for t in event.get("targets", []):
        needed = int(t.get("Needed") or 0)
        min_e = int(t.get("Min elders") or 0)
        min_s = int(t.get("Min servants") or 0)
        if not (needed or min_e or min_s):
            continue
        dept, shift = str(t.get("Department")), str(t.get("Shift"))
        pool = (
            counted[(counted["Department"] == dept) & (counted["Shift"] == shift)]
            if not counted.empty
            else counted
        )
        have = len(pool)
        elders = int((pool["Privilege"] == "Elder").sum()) if have else 0
        servants = int((pool["Privilege"] == "Servant").sum()) if have else 0
        out.append(
            {
                "Department": dept,
                "Shift": shift,
                "have": have,
                "needed": needed,
                "short": max(needed - have, 0),
                "elders": elders,
                "min_elders": min_e,
                "servants": servants,
                "min_servants": min_s,
            }
        )
    return out


def dept_progress(dept: str) -> tuple[int, int]:
    d = event["departments"].setdefault(dept, dict(DEPT_FIELDS))
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

st.markdown(f"### General Gathering {part}, {int(year)}")

late = days_to_deadline < 0
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

if late:
    st.markdown(
        f'<p class="note">Recruitment should be closed. Confirm the volunteer list for all '
        f'{len(ALL_DEPTS)} departments and mark anyone still missing as unavailable.</p>',
        unsafe_allow_html=True,
    )

tab_overview, tab_depts, tab_vols, tab_follow, tab_export = st.tabs(
    ["Readiness", "Departments", "Volunteers", "Follow-up", "Export"]
)

# ---------------------------------------------------------------------------
# Tab 1 — Readiness
# ---------------------------------------------------------------------------

with tab_overview:
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.markdown("#### Your checklist")
        items = CHECKLIST[role]
        saved = event["checklist"]
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
            persist()
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
            if agg and agg["needed"]:
                staff = f' &nbsp;·&nbsp; <span class="{"short" if agg["short"] else ""}">{agg["have"]}/{agg["needed"]} volunteers</span>'
            else:
                staff = ""
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
                'needs per shift on the Departments tab, and shortfalls appear here.</p>',
                unsafe_allow_html=True,
            )
        elif gaps:
            total_short = sum(s["short"] for s in staffing)
            st.markdown(
                f'<p class="note">{total_short} confirmed volunteers short across '
                f'{len(gaps)} shifts.</p>',
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
# Tab 2 — Departments
# ---------------------------------------------------------------------------

with tab_depts:
    mine = [d for d in ALL_DEPTS if DEPT_OWNER[d] == role]
    scope = st.radio(
        "Show", ["My departments", "All departments"], horizontal=True, key="dept_scope"
    )
    options = mine if scope == "My departments" else ALL_DEPTS
    dept = st.selectbox("Department", options, key="dept_pick")
    d = event["departments"].setdefault(dept, dict(DEPT_FIELDS))

    with st.form(f"dept_form_{dept}"):
        c1, c2 = st.columns(2, gap="large")
        with c1:
            overseer = st.text_input("Department overseer", value=d.get("overseer", ""))
            assistant = st.text_input("Assistant", value=d.get("assistant", ""))
            keymen = st.text_input("Keymen", value=d.get("keymen", ""), help="Separate names with commas")
        with c2:
            meet = st.date_input(
                "Pre-gathering meeting",
                value=date.fromisoformat(d["meeting_date"]) if d.get("meeting_date") else event_date - timedelta(days=21),
            )
            attending = st.checkbox("I plan to attend this meeting", value=d.get("attending", False))
            training = st.checkbox("Volunteer training date agreed", value=d.get("training_set", False))
            hazard = st.checkbox("Job hazard form received", value=d.get("hazard_done", False))

        notes = st.text_area("Notes", value=d.get("notes", ""), height=90)

        if st.form_submit_button("Save department", type="primary"):
            d.update(
                overseer=overseer,
                assistant=assistant,
                keymen=keymen,
                meeting_date=meet.isoformat(),
                attending=attending,
                training_set=training,
                hazard_done=hazard,
                notes=notes,
            )
            persist()
            st.success(f"{dept} saved.")

    st.markdown("#### How many are needed")
    st.markdown(
        '<p class="note">Set the headcount per shift. Leave a row at zero if that '
        'department does not work that shift. Only confirmed volunteers count towards it.</p>',
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
            "Needed": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "Min elders": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "Min servants": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
        },
    )

    kept = [t for t in event.get("targets", []) if str(t.get("Department")) != dept]
    for shift_name, row in edited_targets.iterrows():
        values = {k: int(row.get(k) or 0) for k in ("Needed", "Min elders", "Min servants")}
        if any(values.values()):
            kept.append({"Department": dept, "Shift": str(shift_name), **values})
    if kept != event.get("targets", []):
        event["targets"] = kept
        persist()
        st.rerun()

    df = volunteers_df()
    assigned = df[df["Department"] == dept] if not df.empty else df
    st.markdown("#### Volunteers in this department")
    if assigned.empty:
        st.markdown(
            '<p class="note">No one is assigned yet. Add names on the Volunteers tab.</p>',
            unsafe_allow_html=True,
        )
    else:
        counts = assigned.groupby("Shift").size().to_dict()
        line = " &nbsp;·&nbsp; ".join(
            f"{name} {counts.get(name, 0)}" for name in shift_names() if counts.get(name, 0)
        )
        st.markdown(
            f'<p class="note">{line or "No shift assigned yet"}</p>', unsafe_allow_html=True
        )
        st.dataframe(assigned, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Tab 3 — Volunteers
# ---------------------------------------------------------------------------

with tab_vols:
    with st.expander("Shift times", expanded=False):
        st.markdown(
            '<p class="note">Name each shift and say when volunteers report. '
            'Half decides who counts as working the morning or the afternoon, so a '
            'setup shift that ends before the programme still belongs to the morning.</p>',
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
                "Report": st.column_config.TextColumn(
                    "Report at", validate=TIME_PATTERN, help="24-hour, e.g. 07:45", width="small"
                ),
                "Start": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
                "End": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
            },
        )
        shift_records = shift_edit.fillna("").to_dict("records")
        if shift_records != event.get("shifts", []):
            event["shifts"] = shift_records
            persist()
            st.rerun()

    st.markdown("#### Master volunteer list")
    st.markdown(
        '<p class="note">Keep each volunteer to one half of the day, so everyone gets '
        'to enjoy the other half.</p>',
        unsafe_allow_html=True,
    )

    base = volunteers_df()
    f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1.5])
    search = f1.text_input("Find by name", key=f"f_name_{event_key}")
    f_dept = f2.multiselect("Department", ALL_DEPTS, key=f"f_dept_{event_key}")
    f_shift = f3.multiselect("Shift", shift_names(), key=f"f_shift_{event_key}")
    f_status = f4.multiselect("Status", VOLUNTEER_STATUSES, key=f"f_status_{event_key}")

    filtering = bool(search.strip() or f_dept or f_shift or f_status)
    view = base
    if filtering and not base.empty:
        mask = pd.Series(True, index=base.index)
        if search.strip():
            mask &= base["Name"].str.contains(search.strip(), case=False, na=False)
        if f_dept:
            mask &= base["Department"].isin(f_dept)
        if f_shift:
            mask &= base["Shift"].isin(f_shift)
        if f_status:
            mask &= base["Status"].isin(f_status)
        view = base[mask]
        st.caption(
            f"{len(view)} of {len(base)} volunteers. Clear the filters to add or delete rows."
        )

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
            "Department": st.column_config.SelectboxColumn(options=ALL_DEPTS, width="medium"),
            "Shift": st.column_config.SelectboxColumn(options=shift_names(), width="small"),
            "Status": st.column_config.SelectboxColumn(options=VOLUNTEER_STATUSES, width="small"),
            "Phone": st.column_config.TextColumn(width="small"),
            "Notes": st.column_config.TextColumn(width="large"),
        },
    )

    if filtering:
        merged = base.copy()
        merged.loc[edited.index] = edited
        records = merged.fillna("").to_dict("records")
    else:
        records = edited.fillna("").to_dict("records")

    if records != event.get("volunteers", []):
        event["volunteers"] = records
        persist()

    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df

    if named.empty:
        st.markdown(
            '<p class="note">Add a row above to start the list. Each department needs '
            'names before recruitment closes.</p>',
            unsafe_allow_html=True,
        )
    else:
        halves = named["Shift"].map(shift_half)
        keys = named["Name"].str.strip().str.lower()
        both = halves.groupby(keys).nunique().pipe(lambda s: s[s > 1])
        if len(both):
            names = named[keys.isin(both.index)]["Name"].unique()
            st.warning(
                "Assigned to both halves of the day — move one shift: " + ", ".join(sorted(names))
            )

        empty_depts = [d for d in ALL_DEPTS if d not in set(named["Department"])]
        if empty_depts:
            st.info("No volunteers yet: " + ", ".join(empty_depts))

        summary = (
            named.pivot_table(index="Department", columns="Shift", values="Name", aggfunc="count")
            .reindex(ALL_DEPTS)
            .fillna(0)
            .astype(int)
        )
        for name in shift_names():
            if name not in summary.columns:
                summary[name] = 0
        summary = summary[[n for n in shift_names() if n in summary.columns]]
        st.markdown("#### Coverage by shift")
        st.caption(
            " · ".join(f"{n} {shift_hours(n)}".strip() for n in shift_names() if shift_hours(n))
        )
        st.dataframe(summary, width="stretch")

        mix = pd.DataFrame(index=ALL_DEPTS)
        for p in PRIVILEGES:
            mix[p] = named[named["Privilege"] == p].groupby("Department").size()
        for g in GENDERS:
            mix[g] = named[named["Gender"] == g].groupby("Department").size()
        mix = mix.fillna(0).astype(int)
        st.markdown("#### Make-up of each department")
        st.caption("Everyone on the list, whatever their status")
        st.dataframe(mix, width="stretch")

# ---------------------------------------------------------------------------
# Tab 4 — Follow-up
# ---------------------------------------------------------------------------

with tab_follow:
    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df
    pending = named[named["Status"] == "Invited"] if not named.empty else named

    st.markdown("#### Who still owes you an answer")
    st.markdown(
        f'<p class="note">{"Recruitment closed " + f"{-days_to_deadline} days ago" if late else f"{days_to_deadline} days until recruitment closes"}.</p>',
        unsafe_allow_html=True,
    )

    if pending.empty:
        st.markdown(
            '<p class="note">Nobody is sitting at Invited. Everyone on the list has '
            'either confirmed or been marked unavailable.</p>',
            unsafe_allow_html=True,
        )
    else:
        counts = pending.groupby("Department").size().sort_values(ascending=False)
        for dept_name, n in counts.items():
            st.markdown(
                f'<div class="row part"><span class="name">{dept_name}</span>'
                f'<span class="state">{n} awaiting reply</span></div>',
                unsafe_allow_html=True,
            )

        st.markdown("#### Chase list")
        chase_dept = st.selectbox(
            "Department", ["All"] + sorted(counts.index.tolist()), key="chase_dept"
        )
        rows = pending if chase_dept == "All" else pending[pending["Department"] == chase_dept]
        st.dataframe(
            rows[["Name", "Department", "Shift", "Phone", "Notes"]],
            hide_index=True,
            width="stretch",
        )

        lines = [f"General Gathering {part}, {int(year)} — {event_date:%d %B}"]
        for dept_name, group in rows.groupby("Department"):
            lines.append(f"\n{dept_name}:")
            for _, v in group.iterrows():
                phone = f" — {v['Phone']}" if str(v["Phone"]).strip() else ""
                lines.append(f"  {v['Name']} ({v['Shift']}){phone}")
        st.text_area(
            "Copy this into a message",
            "\n".join(lines),
            height=200,
            key="chase_text",
        )

    no_phone = named[named["Phone"].str.strip() == ""] if not named.empty else named
    if not no_phone.empty:
        st.markdown("#### Missing phone numbers")
        st.markdown(
            f'<p class="note">{len(no_phone)} people have no number on file, so they '
            'cannot be reached if the date or time changes.</p>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            no_phone[["Name", "Department", "Shift", "Status"]],
            hide_index=True,
            width="stretch",
        )

# ---------------------------------------------------------------------------
# Tab 4 — Export
# ---------------------------------------------------------------------------

with tab_export:
    st.markdown("#### Take it off the screen")
    df = volunteers_df()
    stem = f"{part.replace(' ', '')}_{int(year)}"

    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "Volunteer list (CSV)",
        df.to_csv(index=False),
        file_name=f"{stem}_volunteers.csv",
        mime="text/csv",
        width="stretch",
    )

    dept_rows = []
    for dept_name in ALL_DEPTS:
        d = event["departments"].get(dept_name, dict(DEPT_FIELDS))
        done, total = dept_progress(dept_name)
        dept_rows.append(
            {
                "Department": dept_name,
                "Responsible": DEPT_OWNER[dept_name],
                "Overseer": d.get("overseer", ""),
                "Assistant": d.get("assistant", ""),
                "Keymen": d.get("keymen", ""),
                "Meeting": d.get("meeting_date") or "",
                "Training set": "Yes" if d.get("training_set") else "No",
                "Hazard form": "Yes" if d.get("hazard_done") else "No",
                "Steps done": f"{done}/{total}",
            }
        )
    dept_df = pd.DataFrame(dept_rows)
    c2.download_button(
        "Department sheet (CSV)",
        dept_df.to_csv(index=False),
        file_name=f"{stem}_departments.csv",
        mime="text/csv",
        width="stretch",
    )

    report = f"""<!doctype html><meta charset="utf-8">
<title>General Gathering {part} {int(year)}</title>
<style>
 body {{ font-family: Georgia, serif; margin: 2.5cm; color:#16202b; }}
 h1 {{ font-size: 20pt; margin-bottom: .2em; }}
 p.sub {{ color:#5b6b7c; margin-top:0; }}
 table {{ border-collapse: collapse; width:100%; margin: 1.2em 0; font-size: 10pt; }}
 th, td {{ border-bottom:1px solid #dcd8cf; text-align:left; padding:6px 8px; }}
 th {{ background:#f7f5f0; }}
 @media print {{ body {{ margin: 1.5cm; }} }}
</style>
<h1>General Gathering {part}, {int(year)}</h1>
<p class="sub">{event_date:%A, %d %B %Y}{' &middot; ' + event['venue'] if event.get('venue') else ''}
&middot; recruitment closed {deadline:%d %B} &middot; prepared {today:%d %B %Y}</p>
<h2>Shifts</h2>
{shifts_df().to_html(index=False, border=0)}
<h2>Departments</h2>
{dept_df.to_html(index=False, border=0)}
<h2>Volunteers</h2>
{df.to_html(index=False, border=0) if not df.empty else '<p>No volunteers recorded.</p>'}
"""
    c3.download_button(
        "Printable summary (HTML)",
        report,
        file_name=f"{stem}_summary.html",
        mime="text/html",
        width="stretch",
    )

    st.divider()
    st.markdown("#### Assignment slips")
    st.markdown(
        '<p class="note">One card per volunteer, six to a page. Open the file and print it, '
        'then cut along the dashed lines.</p>',
        unsafe_allow_html=True,
    )

    named_all = df[df["Name"].str.strip() != ""] if not df.empty else df
    slip_depts = st.multiselect(
        "Departments", ALL_DEPTS, default=ALL_DEPTS, key="slip_depts"
    )
    confirmed_only = st.checkbox("Confirmed volunteers only", value=True, key="slip_confirmed")

    slip_rows = named_all[named_all["Department"].isin(slip_depts)] if not named_all.empty else named_all
    if confirmed_only and not slip_rows.empty:
        slip_rows = slip_rows[slip_rows["Status"] == "Confirmed"]
    slip_rows = slip_rows.sort_values(["Department", "Shift", "Name"]) if not slip_rows.empty else slip_rows

    if slip_rows.empty:
        st.markdown(
            '<p class="note">No volunteers match that selection yet.</p>', unsafe_allow_html=True
        )
    else:
        cards = []
        for _, v in slip_rows.iterrows():
            s = shift_record(v["Shift"])
            d = event["departments"].get(v["Department"], dict(DEPT_FIELDS))
            contact = d.get("overseer", "") or "—"
            report = s.get("Report") or s.get("Start") or ""
            cards.append(
                f"""<div class="slip">
  <p class="ev">General Gathering {part}, {int(year)}</p>
  <p class="who">{v['Name']}</p>
  <table>
    <tr><td>Department</td><th>{v['Department']}</th></tr>
    <tr><td>Shift</td><th>{v['Shift']} {shift_hours(v['Shift'])}</th></tr>
    <tr><td>Report at</td><th>{report or '—'}</th></tr>
    <tr><td>Date</td><th>{event_date:%a %d %B %Y}</th></tr>
    <tr><td>Venue</td><th>{event.get('venue') or '—'}</th></tr>
    <tr><td>See</td><th>{contact}</th></tr>
  </table>
  <p class="foot">Bring your own food and the protective gear your task needs.</p>
</div>"""
            )

        slips_html = f"""<!doctype html><meta charset="utf-8">
<title>Assignment slips — {part} {int(year)}</title>
<style>
 @page {{ size: A4; margin: 10mm; }}
 body {{ font-family: Helvetica, Arial, sans-serif; color:#16202b; margin:0; }}
 .sheet {{ display:grid; grid-template-columns: 1fr 1fr; gap:0; }}
 .slip {{ border:1px dashed #8b93a0; padding:8mm 7mm; height:82mm; box-sizing:border-box;
          break-inside:avoid; page-break-inside:avoid; }}
 .ev {{ font-size:8pt; letter-spacing:.02em; color:#5b6b7c; margin:0 0 4mm; }}
 .who {{ font-size:16pt; font-weight:700; margin:0 0 4mm; }}
 table {{ border-collapse:collapse; font-size:9.5pt; width:100%; }}
 td {{ color:#5b6b7c; padding:1.1mm 0; width:26mm; vertical-align:top; }}
 th {{ text-align:left; font-weight:600; padding:1.1mm 0; }}
 .foot {{ font-size:7.5pt; color:#5b6b7c; margin:5mm 0 0; }}
</style>
<div class="sheet">
{''.join(cards)}
</div>"""

        st.download_button(
            f"Assignment slips for {len(slip_rows)} volunteers (HTML)",
            slips_html,
            file_name=f"{stem}_slips.html",
            mime="text/html",
            type="primary",
        )

    st.divider()
    st.markdown("#### Backup")
    st.download_button(
        "Download all gathering data (JSON)",
        json.dumps(st.session_state.store, indent=2, default=str),
        file_name=f"{stem}_backup.json",
        mime="application/json",
    )
    restored = st.file_uploader("Restore from a backup file", type="json")
    if restored is not None and st.button("Replace current data with this backup"):
        st.session_state.store = json.load(restored)
        save_store(st.session_state.store)
        st.success("Backup restored.")
        st.rerun()

    with st.expander("Delete this gathering's data"):
        st.markdown(
            f'<p class="note">This file holds the names, phone numbers and privileges '
            f'of {len(volunteers_df())} people. Once the gathering is over and the '
            'reports are filed, delete it rather than leaving it on a laptop. Download '
            'a backup first if head office may ask for figures later.</p>',
            unsafe_allow_html=True,
        )
        sure = st.checkbox(f"Yes, delete everything for {event_key}", key="del_sure")
        if st.button("Delete now", disabled=not sure):
            store = load_store()
            store.pop(event_key, None)
            save_store(store)
            st.session_state.store = store
            st.success(f"{event_key} deleted.")
            st.rerun()
