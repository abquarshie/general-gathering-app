"""
General Gathering Management Portal
-----------------------------------
For the gathering overseer and the assistant overseer.

    streamlit run app.py

Storage is Neon Postgres when configured, SQLite otherwise — see README.md.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import auth
import db as store
import documents as docs

APP_TITLE = "Gathering Portal"

OVERSEER_DEPTS = ["Accounts", "Attendant", "Cleaning", "First Aid", "Parking", "Rooming"]
ASSISTANT_DEPTS = ["Audio/Video", "Baptism", "Installation", "Lost & Found", "Checkroom"]
ALL_DEPTS = OVERSEER_DEPTS + ASSISTANT_DEPTS
DEPT_OWNER = {d: "Overseer" for d in OVERSEER_DEPTS}
DEPT_OWNER.update({d: "Assistant Overseer" for d in ASSISTANT_DEPTS})

SHIFT_COLUMNS = ["Shift", "Report", "Start", "End"]
DEFAULT_SHIFTS = [
    {"Shift": "Early setup", "Report": "06:00", "Start": "06:15", "End": "08:30"},
    {"Shift": "Morning", "Report": "07:45", "Start": "08:30", "End": "12:45"},
    {"Shift": "Afternoon", "Report": "12:30", "Start": "13:15", "End": "17:15"},
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
STATUSES = ["Confirmed", "Invited", "Unavailable", "Moved out"]
GRID_COLUMNS = ["Name", "Gender", "Privilege", "Congregation", "Department", "Shift", "Status", "Notes"]
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

NEON_STEPS = """
1. Create a project at [console.neon.tech](https://console.neon.tech), region
   `eu-central-1` (Frankfurt) for the best latency from Accra.
2. Under **Roles**, create a role for the app rather than using the owner
   account. Copy its password — it is shown once.
3. Under **Connection Details**, pick that role and turn on **Pooled
   connection**.
4. Put it in `.streamlit/secrets.toml`:

   ```toml
   [postgres]
   dsn = "postgresql://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require"
   ```

5. `python3 -m pip install "psycopg[binary]"` — the quotes are required in zsh.
6. Restart. **Take a backup first:** Neon starts empty and you restore into it.
"""

SHEETS_STEPS = """
1. At [console.cloud.google.com](https://console.cloud.google.com), create a
   project, then **APIs & Services → Library** and enable the **Google Sheets
   API**.
2. **IAM & Admin → Service Accounts → Create**, then **Keys → Add key → JSON**.
3. Create the spreadsheet and **share it with the service account's
   `client_email` as an Editor** — the step that gets forgotten.
4. Paste the key's fields into `[gcp_service_account]`, keeping `private_key`
   on one line with its `\\n` sequences intact, and add the id from the sheet's
   URL between `/d/` and `/edit`:

   ```toml
   [sheets]
   spreadsheet_id = "1AbC..."
   ```

5. `python3 -m pip install gspread`.
"""

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
.countdown .days small { font-size: 1rem; font-weight: 400; opacity: .75; margin-left: .4rem; }
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


@st.cache_resource
def get_db() -> store.Database:
    return store.Database(auth.all_secrets())


try:
    DB = get_db()
except Exception as exc:  # noqa: BLE001
    st.error(f"The database could not be opened. Check the [postgres] block.\n\n{exc}")
    st.stop()

# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------

if st.session_state.get("role") and auth.session_expired():
    st.session_state.role = None
    st.warning("Signed out after four hours idle.")

if not st.session_state.get("role"):
    auth.login_screen(APP_TITLE, DB.label, set(CHECKLIST))
    st.stop()

role: str = st.session_state.role
actor: str = st.session_state.get("username", "")

# ---------------------------------------------------------------------------
# Which gathering
# ---------------------------------------------------------------------------

events = store.list_events(DB)
labels = {e["event_key"]: f"{e['event_key']} — {e['event_date']}" for e in events}

with st.sidebar:
    st.markdown(f"**{APP_TITLE}**")
    st.caption(f"Signed in as {role}")

    keys = list(labels)
    if st.session_state.get("event_key") not in keys:
        st.session_state.event_key = keys[0] if keys else None

    if keys:
        st.session_state.event_key = st.selectbox(
            "Gathering",
            keys,
            index=keys.index(st.session_state.event_key),
            format_func=lambda k: labels[k],
            key="event_pick",
        )

    with st.expander("Start a new gathering", expanded=not keys):
        new_part = st.selectbox("Part", ["Part 1", "Part 2"], key="new_part")
        new_year = st.number_input("Year", value=datetime.now().year, step=1, key="new_year")
        new_date = st.date_input("Date", value=date.today() + timedelta(days=90), key="new_date")
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
                        f"{counts['volunteers']} people carried forward, all set back to Invited."
                    )
                st.session_state.event_key = new_key
                st.rerun()

    st.divider()
    st.caption(DB.label)
    if st.button("Sign out"):
        st.session_state.role = None
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
part, year = event_key.rsplit(" ", 1)

with st.sidebar:
    new_date2 = st.date_input("Gathering date", value=event_date, key=f"date_{event_key}")
    new_venue = st.text_input("Venue", value=event.get("venue") or "", key=f"venue_{event_key}")
    if new_date2 != event_date or new_venue != (event.get("venue") or ""):
        store.save_event(DB, event_key, new_date2, new_venue, event["checklist"], actor=actor)
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
    """The grid, with each row's assignment id carried along out of sight."""
    if not vol_rows:
        return pd.DataFrame(columns=["id"] + GRID_COLUMNS)
    rows = [
        {"id": r["id"], **{k: r.get(v, "") or "" for k, v in DB_FIELDS.items()}} for r in vol_rows
    ]
    return pd.DataFrame(rows, columns=["id"] + GRID_COLUMNS).astype(str)


def save_grid(frame: pd.DataFrame) -> None:
    rows = [
        {"id": r.get("id", ""), **{DB_FIELDS[c]: str(r.get(c, "") or "") for c in GRID_COLUMNS}}
        for r in frame.fillna("").to_dict("records")
    ]
    store.save_volunteers(DB, event_key, rows, actor=actor)


def shift_names() -> list[str]:
    names = [str(s.get("Shift", "")).strip() for s in shift_rows]
    return [n for n in names if n] or [s["Shift"] for s in DEFAULT_SHIFTS]


def shift_record(name: str) -> dict:
    for s in shift_rows:
        if str(s.get("Shift", "")).strip().lower() == str(name).strip().lower():
            return s
    return {}


def shift_half(name: str) -> str:
    return store.half_of(shift_record(name).get("Start", ""))


def shift_hours(name: str) -> str:
    s = shift_record(name)
    return f"{s['Start']}–{s['End']}" if s.get("Start") and s.get("End") else ""


def congregation_options() -> list[str]:
    seen = {str(r.get("congregation") or "").strip() for r in vol_rows}
    return CONGREGATIONS + sorted(c for c in seen if c and c not in CONGREGATIONS)


def dept_record(dept: str) -> dict:
    return dept_state.get(dept, dict(DEPT_FIELDS))


def staffing_rows() -> list[dict]:
    df = volunteers_df()
    ready = df[(df["Name"].str.strip() != "") & (df["Status"] == "Confirmed")] if not df.empty else df
    out = []
    for t in target_rows:
        if not (t["Needed"] or t["Min elders"] or t["Min servants"]):
            continue
        pool = (
            ready[(ready["Department"] == t["Department"]) & (ready["Shift"] == t["Shift"])]
            if not ready.empty
            else ready
        )
        have = len(pool)
        out.append(
            {
                "Department": t["Department"],
                "Shift": t["Shift"],
                "have": have,
                "needed": t["Needed"],
                "short": max(t["Needed"] - have, 0),
                "elders": int((pool["Privilege"] == "Elder").sum()) if have else 0,
                "min_elders": t["Min elders"],
                "servants": int((pool["Privilege"] == "Servant").sum()) if have else 0,
                "min_servants": t["Min servants"],
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


def doc_context() -> dict:
    return {
        "part": part,
        "year": year,
        "event_date": event_date,
        "venue": event.get("venue") or "",
        "today": today,
        "departments": dept_state,
        "dept_order": ALL_DEPTS,
        "shifts": shift_names(),
        "hours": {n: shift_hours(n) for n in shift_names()},
    }


def flash(message: str) -> None:
    st.session_state.setdefault("flash", []).append(message)


for message in st.session_state.pop("flash", []):
    st.success(message)

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

tab_ready, tab_depts, tab_vols, tab_docs, tab_settings = st.tabs(
    ["Readiness", "Departments", "Volunteers", "Documents", "Settings"]
)

# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

with tab_ready:
    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("#### Your checklist")
        items = CHECKLIST[role]
        saved = dict(event["checklist"])
        done = sum(1 for k, _ in items if saved.get(k))
        pct = round(done / len(items) * 100)
        st.markdown(f'<div class="bar"><span style="width:{pct}%"></span></div>', unsafe_allow_html=True)
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
            css = "ready" if d_done == d_total else ("behind" if d_done == 0 else "part")
            st.markdown(
                f'<div class="row {css}"><span><span class="name">{dept}</span>'
                f'<span class="owner">{DEPT_OWNER[dept]}</span></span>'
                f'<span class="state">{d_done}/{d_total} steps{staff}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<p class="note" style="margin-top:.8rem">{ready_total} of {len(ALL_DEPTS)} '
            "departments fully prepared</p>",
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
                "needs per shift on the Departments tab.</p>",
                unsafe_allow_html=True,
            )
        elif gaps:
            st.markdown(
                f'<p class="note">{sum(s["short"] for s in staffing)} confirmed volunteers '
                f"short across {len(gaps)} shifts.</p>",
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
    dept = st.selectbox("Department", mine if scope == "My departments" else ALL_DEPTS, key="dept_pick")
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
        '<p class="note">Headcount per shift, counting confirmed volunteers only. '
        "Leave a row at zero if this department does not work that shift.</p>",
        unsafe_allow_html=True,
    )
    current = {
        t["Shift"]: (t["Needed"], t["Min elders"], t["Min servants"])
        for t in target_rows
        if t["Department"] == dept
    }
    grid = pd.DataFrame(
        [
            {
                "Shift": s,
                "Needed": current.get(s, (0, 0, 0))[0],
                "Min elders": current.get(s, (0, 0, 0))[1],
                "Min servants": current.get(s, (0, 0, 0))[2],
            }
            for s in shift_names()
        ]
    ).set_index("Shift")

    edited = st.data_editor(
        grid,
        num_rows="fixed",
        width="stretch",
        key=f"targets_{event_key}_{dept}",
        column_config={
            c: st.column_config.NumberColumn(min_value=0, step=1, width="small")
            for c in ("Needed", "Min elders", "Min servants")
        },
    )
    wanted = {
        str(s): tuple(int(row[c] or 0) for c in ("Needed", "Min elders", "Min servants"))
        for s, row in edited.iterrows()
    }
    wanted = {s: v for s, v in wanted.items() if any(v)}
    if wanted != current:
        store.save_dept_targets(
            DB,
            event_key,
            dept,
            [
                {"Shift": s, "Needed": v[0], "Min elders": v[1], "Min servants": v[2]}
                for s, v in wanted.items()
            ],
            actor=actor,
        )
        st.rerun()

    st.markdown("#### Volunteers in this department")
    df = volunteers_df()
    assigned = df[df["Department"] == dept][GRID_COLUMNS] if not df.empty else df
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
            '<p class="note">Which half of the day a shift belongs to follows its '
            "start time, so nobody works both halves.</p>",
            unsafe_allow_html=True,
        )
        table = pd.DataFrame(shift_rows, columns=SHIFT_COLUMNS).astype(str)
        table.insert(1, "Half", [store.half_of(s.get("Start", "")) for s in shift_rows])
        shift_edit = st.data_editor(
            table,
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key=f"shift_editor_{event_key}",
            column_config={
                "Shift": st.column_config.TextColumn(required=True, width="medium"),
                "Half": st.column_config.TextColumn(disabled=True, width="small"),
                "Report": st.column_config.TextColumn("Report at", validate=TIME_PATTERN, width="small"),
                "Start": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
                "End": st.column_config.TextColumn(validate=TIME_PATTERN, width="small"),
            },
        )
        new_shifts = shift_edit.drop(columns=["Half"]).fillna("").to_dict("records")
        # compare like with like: stored rows also carry the derived Half
        stored = [{c: str(s.get(c, "") or "") for c in SHIFT_COLUMNS} for s in shift_rows]
        if new_shifts != stored:
            store.save_shifts(DB, event_key, new_shifts, actor=actor)
            st.rerun()

        st.markdown(
            '<p class="note">Renaming a shift in the table above would strand its '
            "volunteers. Use this instead — it moves them and their headcount too.</p>",
            unsafe_allow_html=True,
        )
        r1, r2, r3 = st.columns([2, 2, 1])
        old_name = r1.selectbox("Rename", shift_names(), key="rn_old")
        new_name = r2.text_input("to", key="rn_new")
        if r3.button("Rename", disabled=not new_name.strip()):
            moved = store.rename_shift(DB, event_key, old_name, new_name.strip(), actor=actor)
            store.save_shifts(
                DB,
                event_key,
                [
                    {**s, "Shift": new_name.strip()} if s["Shift"] == old_name else s
                    for s in shift_rows
                ],
                actor=actor,
            )
            flash(f"{old_name} is now {new_name.strip()}, with {moved} volunteers.")
            st.rerun()

    stranded = store.stranded_shifts(DB, event_key)
    if stranded:
        st.warning(
            "On a shift that no longer exists: "
            + ", ".join(f"{s['n']} in {s['shift']}" for s in stranded)
            + ". Add that shift back, or reassign them below."
        )

    with st.expander("Paste a list of names"):
        st.markdown(
            '<p class="note">One name per line, or "Name, Congregation". They go in '
            "as Invited, and anyone already listed is skipped.</p>",
            unsafe_allow_html=True,
        )
        p1, p2, p3 = st.columns(3)
        paste_dept = p1.selectbox("Into department", ALL_DEPTS, key="paste_dept")
        paste_shift = p2.selectbox("On shift", shift_names(), key="paste_shift")
        paste_cong = p3.selectbox("Congregation", [""] + congregation_options(), key="paste_cong")
        pasted = st.text_area("Names", height=120, key="paste_names")
        if st.button("Add these names") and pasted.strip():
            n = store.add_names(
                DB, event_key, pasted.splitlines(), paste_dept, paste_shift, paste_cong, actor=actor
            )
            flash(f"{n} added to {paste_dept}, {paste_shift}.")
            st.rerun()

    previous = store.served_before(DB, event_key)
    if previous:
        with st.expander(f"Served before but not on this list ({len(previous)})"):
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
                store.log(DB, actor, event_key, "volunteers", f"{len(picked)} returning added")
                st.rerun()

    removed_rows = store.load_removed(DB, event_key)
    if removed_rows:
        with st.expander(f"Recently removed ({len(removed_rows)})"):
            for r in removed_rows[:15]:
                c1, c2 = st.columns([4, 1])
                c1.markdown(
                    f'<div class="row"><span><span class="name">{r["name"]}</span>'
                    f'<span class="owner">{r["dept"] or "no department"}'
                    f'{", " + r["shift"] if r["shift"] else ""}</span></span>'
                    f'<span class="state">{(r["removed_at"] or "")[:16].replace("T", " ")}</span></div>',
                    unsafe_allow_html=True,
                )
                if c2.button("Restore", key=f"restore_{r['id']}"):
                    store.restore_assignment(DB, r["id"], event_key, actor=actor)
                    st.rerun()

    st.markdown("#### Master volunteer list")
    base = volunteers_df()
    f1, f2, f3 = st.columns([3, 2, 2])
    search = f1.text_input("Find by name or congregation", key=f"f_name_{event_key}")
    f_dept = f2.multiselect("Department", ALL_DEPTS, key=f"f_dept_{event_key}")
    f_status = f3.multiselect("Status", STATUSES, key=f"f_status_{event_key}")

    filtering = bool(search.strip() or f_dept or f_status)
    view = base
    if filtering and not base.empty:
        mask = pd.Series(True, index=base.index)
        if search.strip():
            term = search.strip()
            mask &= base["Name"].str.contains(term, case=False, na=False) | base[
                "Congregation"
            ].str.contains(term, case=False, na=False)
        if f_dept:
            mask &= base["Department"].isin(f_dept)
        if f_status:
            mask &= base["Status"].isin(f_status)
        view = base[mask]
        st.caption(f"{len(view)} of {len(base)}. Clear the filters to add or delete rows.")

    edited_vols = st.data_editor(
        view,
        num_rows="fixed" if filtering else "dynamic",
        hide_index=True,
        width="stretch",
        key=f"vol_editor_{event_key}",
        column_order=GRID_COLUMNS,
        column_config={
            "Name": st.column_config.TextColumn(required=True, width="medium"),
            "Gender": st.column_config.SelectboxColumn(options=GENDERS, width="small"),
            "Privilege": st.column_config.SelectboxColumn(options=PRIVILEGES, width="small"),
            "Congregation": st.column_config.SelectboxColumn(
                options=congregation_options(), width="medium"
            ),
            "Department": st.column_config.SelectboxColumn(options=ALL_DEPTS, width="medium"),
            "Shift": st.column_config.SelectboxColumn(options=shift_names(), width="small"),
            "Status": st.column_config.SelectboxColumn(options=STATUSES, width="small"),
            "Notes": st.column_config.TextColumn(width="large"),
        },
    )

    if filtering:
        merged = base.copy()
        merged.loc[edited_vols.index] = edited_vols
        result = merged
    else:
        result = edited_vols

    if result.fillna("").to_dict("records") != base.to_dict("records"):
        save_grid(result)
        st.rerun()

    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df

    if named.empty:
        st.markdown('<p class="note">Add a row above to start the list.</p>', unsafe_allow_html=True)
    else:
        halves = named["Shift"].map(shift_half)
        keys_l = named["Name"].str.strip().str.lower()
        both = halves.groupby(keys_l).nunique().pipe(lambda s: s[s > 1])
        if len(both):
            st.warning(
                "Assigned to both halves of the day: "
                + ", ".join(sorted(named[keys_l.isin(both.index)]["Name"].unique()))
            )

        empty_depts = [x for x in ALL_DEPTS if x not in set(named["Department"])]
        if empty_depts:
            st.info("No volunteers yet: " + ", ".join(empty_depts))

        pending = named[named["Status"] == "Invited"]
        if not pending.empty:
            with st.expander(f"Still to reply ({len(pending)})"):
                st.markdown(
                    f'<p class="note">{f"Recruitment closed {-days_to_deadline} days ago" if late else f"{days_to_deadline} days until recruitment closes"}.</p>',
                    unsafe_allow_html=True,
                )
                for dept_name, n in pending.groupby("Department").size().sort_values(ascending=False).items():
                    who = dept_record(dept_name).get("overseer") or "no overseer named"
                    st.markdown(
                        f'<div class="row part"><span><span class="name">{dept_name}</span>'
                        f'<span class="owner">ask {who}</span></span>'
                        f'<span class="state">{n} awaiting reply</span></div>',
                        unsafe_allow_html=True,
                    )
                lines = [f"General Gathering {part}, {year} — {event_date:%d %B}"]
                for dept_name, group in pending.groupby("Department"):
                    lines.append(f"\n{dept_name}:")
                    lines += [f"  {v['Name']} ({v['Shift']})" for _, v in group.iterrows()]
                st.text_area("Copy into a message", "\n".join(lines), height=180, key="chase_text")

        c_left, c_right = st.columns(2, gap="large")
        with c_left:
            st.markdown("#### Coverage by shift")
            st.caption(" · ".join(f"{n} {shift_hours(n)}".strip() for n in shift_names() if shift_hours(n)))
            summary = (
                named.pivot_table(index="Department", columns="Shift", values="Name", aggfunc="count")
                .reindex(ALL_DEPTS)
                .fillna(0)
                .astype(int)
            )
            for n in shift_names():
                if n not in summary.columns:
                    summary[n] = 0
            st.dataframe(summary[[n for n in shift_names()]], width="stretch")

        with c_right:
            st.markdown("#### Make-up")
            mix = pd.DataFrame(index=ALL_DEPTS)
            for p in PRIVILEGES:
                mix[p] = named[named["Privilege"] == p].groupby("Department").size()
            for g in GENDERS:
                mix[g] = named[named["Gender"] == g].groupby("Department").size()
            st.dataframe(mix.fillna(0).astype(int), width="stretch")

        with_cong = named[named["Congregation"].str.strip() != ""]
        if not with_cong.empty:
            with st.expander("Where they come from"):
                spread = (
                    with_cong.pivot_table(
                        index="Congregation", columns="Status", values="Name", aggfunc="count"
                    )
                    .fillna(0)
                    .astype(int)
                )
                spread["Total"] = spread.sum(axis=1)
                st.dataframe(spread.sort_values("Total", ascending=False), width="stretch")
                missing = len(named) - len(with_cong)
                if missing:
                    st.markdown(
                        f'<p class="note">{missing} have no congregation set.</p>',
                        unsafe_allow_html=True,
                    )

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

with tab_docs:
    df = volunteers_df()
    named = df[df["Name"].str.strip() != ""] if not df.empty else df
    stem = f"{part.replace(' ', '')}_{year}"
    ctx = doc_context()

    only_confirmed = st.checkbox("Confirmed volunteers only", value=True, key="doc_conf")
    frame = named[named["Status"] == "Confirmed"] if only_confirmed and not named.empty else named

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("#### Master list")
        st.markdown(
            '<p class="note">Every department in turn, each with its overseer, '
            "assistant and keymen above the names.</p>",
            unsafe_allow_html=True,
        )
        st.download_button(
            "Master list",
            docs.master_list_html(frame, ctx),
            file_name=f"{stem}_master_list.html",
            mime="text/html",
            type="primary",
            width="stretch",
        )
    with c2:
        st.markdown("#### Rotation list")
        st.markdown(
            '<p class="note">One column per shift with its hours, so a department '
            "can see who takes over.</p>",
            unsafe_allow_html=True,
        )
        st.download_button(
            "Rotation list",
            docs.rotation_html(frame, ctx),
            file_name=f"{stem}_rotation.html",
            mime="text/html",
            type="primary",
            width="stretch",
        )

    with st.expander("Also as CSV"):
        d1, d2 = st.columns(2)
        d1.download_button(
            "Master list (CSV)",
            docs.master_csv(frame, ctx),
            file_name=f"{stem}_master_list.csv",
            mime="text/csv",
            width="stretch",
        )
        d2.download_button(
            "Rotation list (CSV)",
            docs.rotation_csv(frame, ctx),
            file_name=f"{stem}_rotation.csv",
            mime="text/csv",
            width="stretch",
        )

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def sheets_problems() -> list[str]:
    missing = []
    creds = auth.secret_block("gcp_service_account")
    sheets = auth.secret_block("sheets")
    if not creds:
        missing.append("no [gcp_service_account] block in secrets")
    else:
        for field in ("client_email", "private_key", "project_id"):
            if not str(creds.get(field, "")).strip():
                missing.append(f"[gcp_service_account] has no {field}")
    if not str(sheets.get("spreadsheet_id", "")).strip():
        missing.append("no spreadsheet_id under [sheets]")
    return missing


with tab_settings:
    st.markdown("#### Backup")
    dump = store.export_all(DB)
    facts = store.describe_backup(dump)
    st.markdown(
        f'<p class="note">Everything in the database — {facts["people"]} people across '
        f'{len(facts["gatherings"])} '
        f'{"gathering" if len(facts["gatherings"]) == 1 else "gatherings"}. The file '
        "name carries the date and time, so downloads pile up as a history.</p>",
        unsafe_allow_html=True,
    )
    st.download_button(
        "Download backup (JSON)",
        json.dumps(dump, indent=1),
        file_name=f"gathering_backup_{datetime.now():%Y%m%d_%H%M}.json",
        mime="application/json",
    )

    with st.expander("Restore from a backup file"):
        uploaded = st.file_uploader("Backup file", type="json", key="restore_file")
        incoming, summary = None, None
        if uploaded is not None:
            try:
                incoming = json.load(uploaded)
                summary = store.describe_backup(incoming)
            except Exception as exc:  # noqa: BLE001
                st.error(f"That file could not be read as a backup: {exc}")

        if summary:
            st.markdown(
                f'<p class="note">Taken {summary["exported_at"].replace("T", " ")[:16]}, '
                f'holding {summary["people"]} people.</p>',
                unsafe_allow_html=True,
            )
            if summary["gatherings"]:
                st.dataframe(pd.DataFrame(summary["gatherings"]), hide_index=True, width="stretch")
            mode = st.radio(
                "How to apply it",
                ["Merge", "Replace everything"],
                horizontal=True,
                key="restore_mode",
                help=(
                    "Merge adds what is missing and leaves anything already here alone. "
                    "Replace empties the database first."
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
                        DB, incoming, "replace" if danger else "merge", actor=actor
                    )
                    written = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
                    flash(f"Restored: {written or 'nothing new to add'}.")
                    st.session_state.event_key = None
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Restore stopped, nothing was written: {exc}")

    st.divider()
    st.markdown("#### Google Sheets")
    gaps = sheets_problems()
    last_push = store.get_meta(DB, "last_sheets_push")
    if gaps:
        st.markdown(
            '<p class="note">Not ready yet: ' + "; ".join(gaps) + ".</p>", unsafe_allow_html=True
        )
        with st.expander("How to set Sheets up"):
            st.markdown(SHEETS_STEPS)
    elif last_push:
        stale = (datetime.now() - datetime.fromisoformat(last_push["at"])).days
        st.markdown(
            f'<p class="note">Last pushed {last_push["at"].replace("T", " ")[:16]}.</p>',
            unsafe_allow_html=True,
        )
        if stale >= 7:
            st.warning(f"The Sheets copy is {stale} days old.")

    if st.button("Push to Sheets", disabled=bool(gaps)):
        try:
            import gspread

            creds = auth.secret_block("gcp_service_account")
            client = gspread.service_account_from_dict(creds)
            book = client.open_by_key(str(auth.secret_block("sheets")["spreadsheet_id"]))

            frame_all = volunteers_df()
            frame_all = frame_all[frame_all["Name"].str.strip() != ""]
            pages = [
                (f"{event_key} master", pd.read_csv(io.StringIO(docs.master_csv(frame_all, doc_context())))),
                (f"{event_key} rotation", pd.read_csv(io.StringIO(docs.rotation_csv(frame_all, doc_context())))),
            ]
            for table_name, rows in store.export_all(DB)["tables"].items():
                pages.append((f"data {table_name}", pd.DataFrame(rows)))

            for title, table in pages:
                table = table.fillna("")
                try:
                    sheet = book.worksheet(title)
                    sheet.clear()
                except Exception:
                    sheet = book.add_worksheet(title, rows=400, cols=26)
                if not table.empty:
                    sheet.update([table.columns.tolist()] + table.astype(str).values.tolist())

            store.set_meta(DB, "last_sheets_push", str(len(pages)))
            store.log(DB, actor, event_key, "backup", f"{len(pages)} sheets pushed")
            flash(f"{len(pages)} sheets updated.")
            st.rerun()
        except ModuleNotFoundError:
            st.error("gspread is not installed. Run: python3 -m pip install gspread")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Sheets rejected the update: {exc}")

    st.divider()
    st.markdown("#### Storage")
    st.markdown(f'<p class="note">{DB.label}</p>', unsafe_allow_html=True)
    if DB.kind != "postgres":
        with st.expander("Move to Neon Postgres"):
            st.markdown(
                '<p class="note">A file-backed database is fine on one laptop. '
                "Deployed, it is wiped on every redeploy and two people editing it "
                "overwrite each other.</p>",
                unsafe_allow_html=True,
            )
            st.markdown(NEON_STEPS)

    with st.expander("Check the setup"):
        if st.button("Run checks", key="run_checks"):
            auth.forget_secrets()
            try:
                import check_setup

                st.session_state["checks"] = check_setup.run_all(auth.all_secrets())
            except ModuleNotFoundError:
                st.session_state["checks"] = [("Setup", "fail", "check_setup.py is not here.")]
            except Exception as exc:  # noqa: BLE001
                st.session_state["checks"] = [("Setup", "fail", f"The checks stopped: {exc}")]

        if st.session_state.get("checks"):
            st.markdown(
                f'<p class="note">Blocks visible: '
                f'{", ".join(sorted(auth.all_secrets())) or "none"}</p>',
                unsafe_allow_html=True,
            )
            icon = {"ok": "✓", "fail": "✗", "warn": "!"}
            last_section = None
            for section, status, message in st.session_state["checks"]:
                if section != last_section:
                    st.markdown(f"**{section}**")
                    last_section = section
                colour = {"ok": "var(--done-ok)", "fail": "var(--signal)"}.get(status, "var(--ink-soft)")
                st.markdown(
                    f'<p class="note" style="margin:.1rem 0"><span style="color:{colour};'
                    f'font-weight:700">{icon[status]}</span> {message}</p>',
                    unsafe_allow_html=True,
                )

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

    with st.expander("Delete this gathering"):
        st.markdown(
            f'<p class="note">This gathering holds {len(named)} names. Records are '
            "normally kept so next year can be copied forward — delete only if it "
            "was entered by mistake.</p>",
            unsafe_allow_html=True,
        )
        if st.checkbox(f"Yes, delete {event_key} and everyone in it", key="del_sure"):
            if st.button("Delete now"):
                store.delete_event(DB, event_key, actor=actor)
                st.session_state.event_key = None
                st.rerun()
