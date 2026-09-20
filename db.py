"""
Storage for the gathering portal.

One set of SQL statements runs against two drivers:

* Neon Postgres when a [postgres] block exists in secrets — the deployed case.
* SQLite in data/portal.db otherwise, so it runs on a laptop with no setup.

A person exists once, in `people`, with their congregation. What they do at a
particular gathering is an `assignment`. That split is what lets you ask
whether someone served last year, and it means a spelling correction edits the
person instead of looking like a deletion.

Assignments are never hard deleted by the app: removing someone stamps
removed_at, so it can be undone. Every write is recorded in `changes`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

DB_FILE = Path("data") / "portal.db"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS events (
        event_key  TEXT PRIMARY KEY,
        event_date TEXT NOT NULL,
        venue      TEXT DEFAULT '',
        checklist  TEXT DEFAULT '{}',
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS departments (
        event_key    TEXT NOT NULL,
        dept         TEXT NOT NULL,
        overseer     TEXT DEFAULT '',
        assistant    TEXT DEFAULT '',
        keymen       TEXT DEFAULT '',
        meeting_date TEXT,
        attending    INTEGER DEFAULT 0,
        training_set INTEGER DEFAULT 0,
        hazard_done  INTEGER DEFAULT 0,
        notes        TEXT DEFAULT '',
        PRIMARY KEY (event_key, dept)
    )""",
    """CREATE TABLE IF NOT EXISTS shifts (
        event_key TEXT NOT NULL,
        shift     TEXT NOT NULL,
        half      TEXT DEFAULT 'Morning',
        report    TEXT DEFAULT '',
        start_t   TEXT DEFAULT '',
        end_t     TEXT DEFAULT '',
        position  INTEGER DEFAULT 0,
        PRIMARY KEY (event_key, shift)
    )""",
    """CREATE TABLE IF NOT EXISTS targets (
        event_key    TEXT NOT NULL,
        dept         TEXT NOT NULL,
        shift        TEXT NOT NULL,
        needed       INTEGER DEFAULT 0,
        min_elders   INTEGER DEFAULT 0,
        min_servants INTEGER DEFAULT 0,
        PRIMARY KEY (event_key, dept, shift)
    )""",
    """CREATE TABLE IF NOT EXISTS people (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        gender       TEXT DEFAULT '',
        privilege    TEXT DEFAULT '',
        congregation TEXT DEFAULT '',
        created_at   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS assignments (
        id         TEXT PRIMARY KEY,
        event_key  TEXT NOT NULL,
        person_id  TEXT NOT NULL,
        dept       TEXT DEFAULT '',
        shift      TEXT DEFAULT '',
        status     TEXT DEFAULT 'Invited',
        notes      TEXT DEFAULT '',
        removed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS changes (
        id        TEXT PRIMARY KEY,
        at        TEXT NOT NULL,
        actor     TEXT DEFAULT '',
        event_key TEXT DEFAULT '',
        area      TEXT DEFAULT '',
        detail    TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS assignments_event ON assignments (event_key)",
    "CREATE INDEX IF NOT EXISTS assignments_person ON assignments (person_id)",
    "CREATE INDEX IF NOT EXISTS changes_event ON changes (event_key, at)",
]


# Ordered schema steps. Never edit a released one — append a new number.
# Step 1 is the baseline every table above already creates.
MIGRATIONS: list = [
    (1, []),
]


def _dsn(secrets) -> str | None:
    try:
        block = secrets["postgres"]
    except Exception:
        return None
    if "dsn" in block:
        return str(block["dsn"])
    try:
        return (
            f"postgresql://{block['user']}:{block['password']}"
            f"@{block['host']}/{block['database']}?sslmode=require"
        )
    except Exception:
        return None


class Database:
    # class-level defaults, so an instance built by an older copy of this file
    # and kept alive in Streamlit's resource cache still answers for them
    queries = 0
    _meta_ready = False

    def __init__(self, secrets=None):
        self.dsn = _dsn(secrets) if secrets is not None else None
        self.kind = "postgres" if self.dsn else "sqlite"
        self._lock = threading.RLock()
        self._live = None
        self.queries = 0
        if self.kind == "sqlite":
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.setup()
        self.absorb_legacy()

    @property
    def label(self) -> str:
        return "Neon Postgres" if self.kind == "postgres" else f"SQLite ({DB_FILE})"

    @contextmanager
    def _conn(self):
        """One connection, kept open and reused.

        Opening a connection per query is free on SQLite and expensive on Neon —
        a TLS handshake every time. The lock is here because Streamlit shares
        this object across sessions and threads.
        """
        with self._lock:
            conn = self._live or self._open()
            self._live = conn
            try:
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    self._drop()
                raise

    def _open(self):
        if self.kind == "postgres":
            import psycopg

            return psycopg.connect(self.dsn, connect_timeout=15)
        return sqlite3.connect(DB_FILE, check_same_thread=False)

    def _drop(self) -> None:
        try:
            if self._live is not None:
                self._live.close()
        except Exception:
            pass
        self._live = None

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.kind == "postgres" else statement

    def _attempt(self, work, retries: int = 1):
        """Run work(cursor); reconnect once if the connection went stale."""
        for attempt in range(retries + 1):
            try:
                with self._conn() as conn:
                    return work(conn.cursor())
            except Exception:
                self._drop()
                if attempt == retries:
                    raise
                time.sleep(0.2)

    def run(self, statement: str, params: tuple = ()) -> None:
        self.queries += 1
        self._attempt(lambda cur: cur.execute(self._sql(statement), params))

    def run_many(self, pairs: list) -> None:
        def work(cur):
            for statement, params in pairs:
                cur.execute(self._sql(statement), params)

        self.queries += 1
        self._attempt(work)

    def rows(self, statement: str, params: tuple = ()) -> list:
        def work(cur):
            cur.execute(self._sql(statement), params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

        self.queries += 1
        return self._attempt(work)

    def setup(self) -> None:
        with self._conn() as conn:
            conn.cursor().execute(
                "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT, at TEXT)"
            )
        self._meta_ready = True
        with self._conn() as conn:
            cur = conn.cursor()
            for statement in SCHEMA:
                cur.execute(statement)
        self.migrate()

    def version(self) -> int:
        rows = self.rows("SELECT MAX(version) AS v FROM schema_version")
        return int(rows[0]["v"] or 0) if rows else 0

    def migrate(self) -> list:
        """Apply any schema step this database has not seen.

        SCHEMA above only creates missing tables; it cannot alter one that
        already exists. Adding a column later means appending a step here, and
        every database catches up on its next start.
        """
        applied = []
        at = self.version()
        for number, statements in MIGRATIONS:
            if number <= at:
                continue
            for statement in statements:
                try:
                    self.run(statement)
                except Exception:
                    # a step already satisfied by SCHEMA on a fresh database
                    pass
            self.run(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (number, datetime.now().isoformat(timespec="seconds")),
            )
            applied.append(number)
        return applied

    def absorb_legacy(self) -> int:
        """Move rows from the earlier single `volunteers` table, once."""
        try:
            legacy = self.rows(
                "SELECT event_key, name, gender, privilege, dept, shift, status, notes "
                "FROM volunteers"
            )
        except Exception:
            return 0
        moved = 0
        for r in legacy:
            if not str(r.get("name") or "").strip():
                continue
            pid = ensure_person(
                self,
                {
                    "name": r["name"],
                    "gender": r["gender"] or "",
                    "privilege": r["privilege"] or "",
                    "congregation": "",
                },
            )
            self.run(
                "INSERT INTO assignments (id, event_key, person_id, dept, shift, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    r["event_key"],
                    pid,
                    r["dept"] or "",
                    r["shift"] or "",
                    r["status"] or "Invited",
                    r["notes"] or "",
                ),
            )
            moved += 1
        self.run("ALTER TABLE volunteers RENAME TO volunteers_legacy")
        return moved


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def log(db: Database, actor: str, event_key: str, area: str, detail: str) -> None:
    entry_id = str(uuid.uuid4())
    db.run(
        "INSERT INTO changes (id, at, actor, event_key, area, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (
            entry_id,
            datetime.now().isoformat(timespec="seconds"),
            actor or "",
            event_key,
            area,
            detail,
        ),
    )
    # the cache stamp is one row rather than a count, so it stays cheap forever
    set_meta(db, f"stamp:{event_key}", entry_id)


def data_stamp(db: Database, event_key: str) -> str:
    """Changes whenever anyone writes to this gathering.

    One row lookup, not a count over the whole change log, so it does not get
    slower as that log grows. Shared, so another person's edit invalidates a
    cached view here too.
    """
    row = get_meta(db, f"stamp:{event_key}")
    return str(row.get("v") or "new")


def prune_changes(db: Database, months: int = 12) -> int:
    """Drop change-log entries older than a year, so the table cannot grow
    without limit. Nothing else reads them."""
    cutoff = (datetime.now() - timedelta(days=30 * months)).isoformat(timespec="seconds")
    before = db.rows("SELECT COUNT(*) AS n FROM changes WHERE at < ?", (cutoff,))[0]["n"]
    if before:
        db.run("DELETE FROM changes WHERE at < ?", (cutoff,))
    return int(before)


def recent_changes(db: Database, event_key: str, limit: int = 25) -> list:
    rows = db.rows(
        "SELECT at, actor, area, detail FROM changes WHERE event_key = ? "
        "ORDER BY at DESC LIMIT ?",
        (event_key, int(limit)),
    )
    return [
        {
            "ts": (r["at"] or "").replace("T", " ")[:16],
            "actor": r["actor"],
            "area": r["area"],
            "detail": r["detail"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def list_events(db: Database) -> list:
    return db.rows("SELECT event_key, event_date, venue FROM events ORDER BY event_date DESC")


def event_exists(db: Database, key: str) -> bool:
    return bool(db.rows("SELECT 1 FROM events WHERE event_key = ?", (key,)))


def create_event(db: Database, key: str, event_date: date, venue: str = "", actor: str = "") -> None:
    db.run(
        "INSERT INTO events (event_key, event_date, venue, checklist, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_key) DO NOTHING",
        (key, event_date.isoformat(), venue, "{}", datetime.now().isoformat(timespec="seconds")),
    )
    log(db, actor, key, "gathering", f"created for {event_date:%d %B %Y}")


def load_event(db: Database, key: str) -> dict:
    rows = db.rows(
        "SELECT event_key, event_date, venue, checklist FROM events WHERE event_key = ?", (key,)
    )
    if not rows:
        return {}
    ev = rows[0]
    try:
        ev["checklist"] = json.loads(ev.get("checklist") or "{}")
    except (TypeError, json.JSONDecodeError):
        ev["checklist"] = {}
    return ev


def save_event(
    db: Database, key: str, event_date: date, venue: str, checklist: dict, actor: str = ""
) -> None:
    before = load_event(db, key)
    db.run(
        "UPDATE events SET event_date = ?, venue = ?, checklist = ?, updated_at = ? "
        "WHERE event_key = ?",
        (
            event_date.isoformat(),
            venue,
            json.dumps(checklist),
            datetime.now().isoformat(timespec="seconds"),
            key,
        ),
    )
    bits = []
    if before and before.get("event_date") != event_date.isoformat():
        bits.append(f"date set to {event_date:%d %B %Y}")
    if before and (before.get("venue") or "") != venue:
        bits.append(f"venue set to {venue or 'blank'}")
    if before and before.get("checklist") != checklist:
        done = sum(1 for v in checklist.values() if v)
        bits.append(f"checklist at {done} confirmed")
    if bits:
        log(db, actor, key, "gathering", "; ".join(bits))


def delete_event(db: Database, key: str, actor: str = "") -> None:
    db.run_many(
        [
            ("DELETE FROM assignments WHERE event_key = ?", (key,)),
            ("DELETE FROM targets WHERE event_key = ?", (key,)),
            ("DELETE FROM shifts WHERE event_key = ?", (key,)),
            ("DELETE FROM departments WHERE event_key = ?", (key,)),
            ("DELETE FROM events WHERE event_key = ?", (key,)),
        ]
    )
    log(db, actor, key, "gathering", "deleted, with everyone in it")


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def load_departments(db: Database, key: str) -> dict:
    out = {}
    for r in db.rows("SELECT * FROM departments WHERE event_key = ?", (key,)):
        out[r["dept"]] = {
            "overseer": r["overseer"] or "",
            "assistant": r["assistant"] or "",
            "keymen": r["keymen"] or "",
            "meeting_date": r["meeting_date"],
            "attending": bool(r["attending"]),
            "training_set": bool(r["training_set"]),
            "hazard_done": bool(r["hazard_done"]),
            "notes": r["notes"] or "",
        }
    return out


def save_department(db: Database, key: str, dept: str, f: dict, actor: str = "") -> None:
    db.run(
        """INSERT INTO departments
           (event_key, dept, overseer, assistant, keymen, meeting_date,
            attending, training_set, hazard_done, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (event_key, dept) DO UPDATE SET
             overseer = excluded.overseer, assistant = excluded.assistant,
             keymen = excluded.keymen, meeting_date = excluded.meeting_date,
             attending = excluded.attending, training_set = excluded.training_set,
             hazard_done = excluded.hazard_done, notes = excluded.notes""",
        (
            key,
            dept,
            f.get("overseer", ""),
            f.get("assistant", ""),
            f.get("keymen", ""),
            f.get("meeting_date"),
            int(bool(f.get("attending"))),
            int(bool(f.get("training_set"))),
            int(bool(f.get("hazard_done"))),
            f.get("notes", ""),
        ),
    )
    marks = [
        n
        for n, v in (
            ("training agreed", f.get("training_set")),
            ("hazard form in", f.get("hazard_done")),
        )
        if v
    ]
    log(db, actor, key, dept, "saved" + (f" ({', '.join(marks)})" if marks else ""))


# ---------------------------------------------------------------------------
# Shifts and targets
# ---------------------------------------------------------------------------


def load_shifts(db: Database, key: str) -> list:
    rows = db.rows(
        "SELECT shift, half, report, start_t, end_t FROM shifts "
        "WHERE event_key = ? ORDER BY position, shift",
        (key,),
    )
    return [
        {
            "Shift": r["shift"],
            "Half": r["half"],
            "Report": r["report"] or "",
            "Start": r["start_t"] or "",
            "End": r["end_t"] or "",
        }
        for r in rows
    ]


def rename_shift(db: Database, key: str, old: str, new: str, actor: str = "") -> int:
    """Carry volunteers and targets across a shift rename.

    Without this, renaming a shift would leave everybody pointing at a name
    that no longer exists: they would vanish from the rotation list and from
    their department's headcount, with nothing to show for it.
    """
    moved = db.rows(
        "SELECT COUNT(*) AS n FROM assignments WHERE event_key = ? AND shift = ? "
        "AND removed_at IS NULL",
        (key, old),
    )[0]["n"]
    db.run_many(
        [
            ("UPDATE assignments SET shift = ? WHERE event_key = ? AND shift = ?", (new, key, old)),
            ("UPDATE targets SET shift = ? WHERE event_key = ? AND shift = ?", (new, key, old)),
        ]
    )
    log(db, actor, key, "shifts", f"{old} renamed to {new}, {moved} volunteers moved")
    return int(moved)


def half_of(start: str) -> str:
    """Before midday is the morning half, after it the afternoon."""
    try:
        return "Morning" if int(str(start).split(":")[0]) < 12 else "Afternoon"
    except (ValueError, IndexError):
        return "Morning"


def save_shifts(db: Database, key: str, rows: list, actor: str = "") -> None:
    pairs = [("DELETE FROM shifts WHERE event_key = ?", (key,))]
    for i, r in enumerate(rows):
        name = str(r.get("Shift", "")).strip()
        if not name:
            continue
        pairs.append(
            (
                "INSERT INTO shifts (event_key, shift, half, report, start_t, end_t, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    name,
                    half_of(r.get("Start")),
                    str(r.get("Report") or ""),
                    str(r.get("Start") or ""),
                    str(r.get("End") or ""),
                    i,
                ),
            )
        )
    db.run_many(pairs)
    log(db, actor, key, "shifts", f"{len(pairs) - 1} shifts saved")


def stranded_shifts(db: Database, key: str) -> list:
    """Volunteers whose shift is not in the shift table any more."""
    return db.rows(
        """SELECT shift, COUNT(*) AS n FROM assignments
           WHERE event_key = ? AND removed_at IS NULL AND shift <> ''
             AND shift NOT IN (SELECT shift FROM shifts WHERE event_key = ?)
           GROUP BY shift ORDER BY shift""",
        (key, key),
    )


def load_targets(db: Database, key: str) -> list:
    rows = db.rows(
        "SELECT dept, shift, needed, min_elders, min_servants FROM targets WHERE event_key = ?",
        (key,),
    )
    return [
        {
            "Department": r["dept"],
            "Shift": r["shift"],
            "Needed": int(r["needed"] or 0),
            "Min elders": int(r["min_elders"] or 0),
            "Min servants": int(r["min_servants"] or 0),
        }
        for r in rows
    ]


def save_dept_targets(db: Database, key: str, dept: str, rows: list, actor: str = "") -> None:
    pairs = [("DELETE FROM targets WHERE event_key = ? AND dept = ?", (key, dept))]
    total = 0
    for r in rows:
        needed = int(r.get("Needed") or 0)
        min_e = int(r.get("Min elders") or 0)
        min_s = int(r.get("Min servants") or 0)
        if not (needed or min_e or min_s):
            continue
        total += needed
        pairs.append(
            (
                "INSERT INTO targets (event_key, dept, shift, needed, min_elders, min_servants) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, dept, str(r.get("Shift")), needed, min_e, min_s),
            )
        )
    db.run_many(pairs)
    log(db, actor, key, dept, f"headcount set to {total} across all shifts")


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

PERSON_FIELDS = ("name", "gender", "privilege", "congregation")


def find_person(db: Database, name: str, congregation: str):
    rows = db.rows(
        "SELECT * FROM people WHERE lower(name) = ? AND lower(congregation) = ?",
        (str(name).strip().lower(), str(congregation or "").strip().lower()),
    )
    return rows[0] if rows else None


def ensure_person(db: Database, fields: dict) -> str:
    name = str(fields.get("name", "")).strip()
    cong = str(fields.get("congregation", "") or "").strip()
    found = find_person(db, name, cong)
    if found:
        changed = {
            f: str(fields.get(f, "") or "")
            for f in ("gender", "privilege")
            if str(fields.get(f, "") or "") and str(fields.get(f) or "") != (found.get(f) or "")
        }
        if changed:
            sets = ", ".join(f"{f} = ?" for f in changed)
            db.run(f"UPDATE people SET {sets} WHERE id = ?", tuple(changed.values()) + (found["id"],))
        return found["id"]

    pid = str(uuid.uuid4())
    db.run(
        "INSERT INTO people (id, name, gender, privilege, congregation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            pid,
            name,
            str(fields.get("gender", "") or ""),
            str(fields.get("privilege", "") or ""),
            cong,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return pid


def served_before(db: Database, key: str) -> list:
    """People who served at earlier gatherings and are not on this one."""
    return db.rows(
        """SELECT p.id, p.name, p.congregation, COUNT(*) AS times
           FROM assignments a JOIN people p ON p.id = a.person_id
           WHERE a.event_key <> ? AND a.status = 'Confirmed' AND a.removed_at IS NULL
             AND p.id NOT IN (
               SELECT person_id FROM assignments
               WHERE event_key = ? AND removed_at IS NULL)
           GROUP BY p.id, p.name, p.congregation
           ORDER BY times DESC, p.name""",
        (key, key),
    )


def add_person_to_event(
    db: Database, key: str, person_id: str, dept: str, shift: str, actor: str = ""
) -> None:
    existing = db.rows(
        "SELECT id FROM assignments WHERE event_key = ? AND person_id = ? AND removed_at IS NULL",
        (key, person_id),
    )
    if existing:
        return
    db.run(
        "INSERT INTO assignments (id, event_key, person_id, dept, shift, status, notes) "
        "VALUES (?, ?, ?, ?, ?, 'Invited', '')",
        (str(uuid.uuid4()), key, person_id, dept, shift),
    )
    log(db, actor, key, dept, "a returning volunteer added")


def add_names(
    db: Database,
    key: str,
    lines: list,
    dept: str,
    shift: str,
    congregation: str = "",
    actor: str = "",
) -> int:
    """Add a pasted block of names to one department and shift.

    Everybody is looked up once up front rather than per name, so pasting a
    hundred names is a handful of round trips instead of a few hundred.
    """
    index = {
        (str(p["name"]).strip().lower(), str(p["congregation"] or "").strip().lower()): p["id"]
        for p in db.rows("SELECT id, name, congregation FROM people")
    }
    taken = {
        r["person_id"]
        for r in db.rows(
            "SELECT person_id FROM assignments WHERE event_key = ? AND removed_at IS NULL", (key,)
        )
    }

    new_people: list = []
    new_rows: list = []
    now = datetime.now().isoformat(timespec="seconds")

    for raw in lines:
        name = str(raw).strip().strip(",;")
        if not name:
            continue
        cong = congregation
        if "," in name:  # "Name, Congregation" on one line
            name, _, tail = name.partition(",")
            name, cong = name.strip(), tail.strip() or congregation

        ident = (name.lower(), str(cong or "").strip().lower())
        pid = index.get(ident)
        if pid is None:
            pid = str(uuid.uuid4())
            index[ident] = pid
            new_people.append(
                (
                    "INSERT INTO people (id, name, gender, privilege, congregation, created_at) "
                    "VALUES (?, ?, '', '', ?, ?)",
                    (pid, name, str(cong or "").strip(), now),
                )
            )
        if pid in taken:
            continue
        taken.add(pid)
        new_rows.append(
            (
                "INSERT INTO assignments (id, event_key, person_id, dept, shift, status, notes) "
                "VALUES (?, ?, ?, ?, ?, 'Invited', '')",
                (str(uuid.uuid4()), key, pid, dept, shift),
            )
        )

    if new_people or new_rows:
        db.run_many(new_people + new_rows)
    if new_rows:
        log(db, actor, key, dept, f"{len(new_rows)} names pasted in for {shift}")
    return len(new_rows)


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


def load_volunteers(db: Database, key: str) -> list:
    return db.rows(
        """SELECT a.id, a.person_id, p.name, p.gender, p.privilege, p.congregation,
                  a.dept, a.shift, a.status, a.notes
           FROM assignments a JOIN people p ON p.id = a.person_id
           WHERE a.event_key = ? AND a.removed_at IS NULL
           ORDER BY a.dept, a.shift, p.name""",
        (key,),
    )


def load_removed(db: Database, key: str) -> list:
    return db.rows(
        """SELECT a.id, p.name, p.congregation, a.dept, a.shift, a.status, a.removed_at
           FROM assignments a JOIN people p ON p.id = a.person_id
           WHERE a.event_key = ? AND a.removed_at IS NOT NULL
           ORDER BY a.removed_at DESC""",
        (key,),
    )


def restore_assignment(db: Database, assignment_id: str, key: str = "", actor: str = "") -> None:
    rows = db.rows(
        "SELECT p.name FROM assignments a JOIN people p ON p.id = a.person_id WHERE a.id = ?",
        (assignment_id,),
    )
    db.run("UPDATE assignments SET removed_at = NULL WHERE id = ?", (assignment_id,))
    if key:
        who = rows[0]["name"] if rows else "someone"
        log(db, actor, key, "volunteers", f"{who} restored")


def _repoint_person(db: Database, person_id: str, fields: dict) -> str:
    """Update the person behind a row, or hand the row to whoever it now names."""
    rows = db.rows("SELECT * FROM people WHERE id = ?", (person_id,))
    current = rows[0] if rows else None
    if current is None:
        return ensure_person(db, fields)
    if all(str(current.get(f) or "") == fields[f] for f in PERSON_FIELDS):
        return person_id

    other = find_person(db, fields["name"], fields["congregation"])
    if other and other["id"] != person_id:
        return other["id"]

    db.run(
        "UPDATE people SET name = ?, gender = ?, privilege = ?, congregation = ? WHERE id = ?",
        (fields["name"], fields["gender"], fields["privilege"], fields["congregation"], person_id),
    )
    return person_id


def save_volunteers(db: Database, key: str, rows: list, actor: str = "") -> dict:
    """Write the edited grid back.

    Each row carries the assignment id it came from, so an edit is an update
    and a new row is an insert — no guessing from names. Rows that disappear
    are flagged rather than deleted, so they can be restored.
    """
    existing = {r["id"]: r for r in load_volunteers(db, key)}
    counts = {"added": 0, "changed": 0, "removed": 0}
    seen = set()

    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        person = {f: str(row.get(f, "") or "") for f in PERSON_FIELDS}
        fields = tuple(str(row.get(f, "") or "") for f in ("dept", "shift", "status", "notes"))
        aid = str(row.get("id") or "").strip()
        before = existing.get(aid)

        if before is None:
            db.run(
                "INSERT INTO assignments (id, event_key, person_id, dept, shift, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), key, ensure_person(db, person), *fields),
            )
            counts["added"] += 1
            continue

        seen.add(aid)
        was = tuple(before.get(f) or "" for f in ("dept", "shift", "status", "notes"))
        person_changed = any(str(before.get(f) or "") != person[f] for f in PERSON_FIELDS)

        if person_changed:
            pid = _repoint_person(db, before["person_id"], person)
            db.run("UPDATE assignments SET person_id = ? WHERE id = ?", (pid, aid))
        if was != fields:
            db.run(
                "UPDATE assignments SET dept = ?, shift = ?, status = ?, notes = ? WHERE id = ?",
                (*fields, aid),
            )
        if person_changed or was != fields:
            counts["changed"] += 1

    for aid in existing:
        if aid not in seen:
            db.run(
                "UPDATE assignments SET removed_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), aid),
            )
            counts["removed"] += 1

    bits = [f"{v} {k}" for k, v in counts.items() if v]
    if bits:
        log(db, actor, key, "volunteers", ", ".join(bits))
    return counts


# ---------------------------------------------------------------------------
# Carrying a gathering forward
# ---------------------------------------------------------------------------


def copy_forward(db: Database, source: str, target: str, actor: str = "", reset_status: str = "Invited") -> dict:
    """Copy departments, shifts, targets and people from an earlier gathering.

    Assignments point at the same person records, so service history follows.
    Statuses reset — last year's yes is not this year's yes.
    """
    counts = {"volunteers": 0, "departments": 0, "shifts": 0, "targets": 0}
    pairs = []

    for dept, f in load_departments(db, source).items():
        pairs.append(
            (
                """INSERT INTO departments
                   (event_key, dept, overseer, assistant, keymen, meeting_date,
                    attending, training_set, hazard_done, notes)
                   VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, ?)
                   ON CONFLICT (event_key, dept) DO UPDATE SET
                     overseer = excluded.overseer, assistant = excluded.assistant,
                     keymen = excluded.keymen""",
                (target, dept, f["overseer"], f["assistant"], f["keymen"], f["notes"]),
            )
        )
        counts["departments"] += 1

    for i, s in enumerate(load_shifts(db, source)):
        pairs.append(
            (
                "INSERT INTO shifts (event_key, shift, half, report, start_t, end_t, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (event_key, shift) DO NOTHING",
                (target, s["Shift"], s["Half"], s["Report"], s["Start"], s["End"], i),
            )
        )
        counts["shifts"] += 1

    for t in load_targets(db, source):
        pairs.append(
            (
                "INSERT INTO targets (event_key, dept, shift, needed, min_elders, min_servants) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (event_key, dept, shift) DO NOTHING",
                (target, t["Department"], t["Shift"], t["Needed"], t["Min elders"], t["Min servants"]),
            )
        )
        counts["targets"] += 1

    for v in load_volunteers(db, source):
        pairs.append(
            (
                "INSERT INTO assignments (id, event_key, person_id, dept, shift, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, '')",
                (str(uuid.uuid4()), target, v["person_id"], v["dept"], v["shift"], reset_status),
            )
        )
        counts["volunteers"] += 1

    if pairs:
        db.run_many(pairs)
    log(
        db,
        actor,
        target,
        "gathering",
        f"copied from {source}: {counts['volunteers']} people, {counts['departments']} departments",
    )
    return counts


# ---------------------------------------------------------------------------
# Backup and restore
# ---------------------------------------------------------------------------

TABLES = {
    "events": ("event_key",),
    "departments": ("event_key", "dept"),
    "shifts": ("event_key", "shift"),
    "targets": ("event_key", "dept", "shift"),
    "people": ("id",),
    "assignments": ("id",),
    "changes": ("id",),
}


def _ensure_meta(db: Database) -> None:
    if getattr(db, "_meta_ready", False):
        return
    db.run("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT, at TEXT)")
    db._meta_ready = True


def set_meta(db: Database, key: str, value: str = "") -> None:
    _ensure_meta(db)
    db.run(
        "INSERT INTO meta (k, v, at) VALUES (?, ?, ?) "
        "ON CONFLICT (k) DO UPDATE SET v = excluded.v, at = excluded.at",
        (key, value, datetime.now().isoformat(timespec="seconds")),
    )


def get_meta(db: Database, key: str) -> dict:
    _ensure_meta(db)
    rows = db.rows("SELECT k, v, at FROM meta WHERE k = ?", (key,))
    return rows[0] if rows else {}


def export_all(db: Database) -> dict:
    """Everything in the database, as plain JSON-safe rows."""
    out = {
        "format": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source": "postgres" if db.kind == "postgres" else "sqlite",
        "tables": {},
    }
    for table in TABLES:
        rows = db.rows(f"SELECT * FROM {table}")
        out["tables"][table] = [
            {k: (v if v is None else str(v)) for k, v in row.items()} for row in rows
        ]
    return out


def describe_backup(data: dict) -> dict:
    """What a backup file holds, for showing before anything is written."""
    tables = data.get("tables", {})
    people = {p["id"]: p for p in tables.get("people", [])}
    gatherings = []
    for ev in tables.get("events", []):
        key = ev.get("event_key")
        live = [
            a
            for a in tables.get("assignments", [])
            if a.get("event_key") == key and not a.get("removed_at")
        ]
        gatherings.append(
            {
                "Gathering": key,
                "Date": ev.get("event_date"),
                "Venue": ev.get("venue") or "",
                "Volunteers": len(live),
                "Departments": sum(
                    1 for d in tables.get("departments", []) if d.get("event_key") == key
                ),
            }
        )
    return {
        "exported_at": data.get("exported_at", "unknown"),
        "people": len(people),
        "gatherings": sorted(gatherings, key=lambda g: str(g["Date"]), reverse=True),
        "counts": {t: len(rows) for t, rows in tables.items()},
    }


def validate_backup(data: dict) -> list:
    """Problems that would make a restore write nonsense. Empty means fine."""
    problems = []
    if not isinstance(data, dict):
        return ["The file is not a backup — expected an object at the top level."]
    if data.get("format") != 1:
        problems.append(f"Unknown backup format: {data.get('format')!r}.")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        return problems + ["The file has no tables in it."]

    for name, rows in tables.items():
        if name not in TABLES:
            problems.append(f"Unknown table {name!r}.")
            continue
        if not isinstance(rows, list):
            problems.append(f"{name} is not a list of rows.")
            continue
        for i, row in enumerate(rows[:200]):
            if not isinstance(row, dict):
                problems.append(f"{name} row {i} is not a record.")
                break
            missing = [c for c in TABLES[name] if not str(row.get(c, "")).strip()]
            if missing:
                problems.append(f"{name} row {i} has no {', '.join(missing)}.")
                break

    people = {p.get("id") for p in tables.get("people", []) if isinstance(p, dict)}
    orphans = [
        a
        for a in tables.get("assignments", [])
        if isinstance(a, dict) and a.get("person_id") not in people
    ]
    if orphans:
        problems.append(f"{len(orphans)} assignments name a person the file does not contain.")
    return problems


def restore_all(db: Database, data: dict, mode: str = "merge", actor: str = "") -> dict:
    """Write a backup back into the database.

    merge   — add rows that are missing, leave anything present untouched.
    replace — empty every table first, so the database ends up matching the file.
    """
    problems = validate_backup(data)
    if problems:
        raise ValueError("; ".join(problems[:4]))
    tables = data.get("tables", {})
    counts = {}
    pairs: list = []

    if mode == "replace":
        for table in reversed(list(TABLES)):
            pairs.append((f"DELETE FROM {table}", ()))

    existing = {}
    if mode != "replace":
        for table, pk in TABLES.items():
            existing[table] = {
                tuple(str(r[c]) for c in pk) for r in db.rows(f"SELECT * FROM {table}")
            }

    for table, pk in TABLES.items():
        rows = tables.get(table) or []
        written = 0
        for row in rows:
            if mode != "replace":
                ident = tuple(str(row.get(c)) for c in pk)
                if ident in existing.get(table, set()):
                    continue
            cols = list(row)
            placeholders = ", ".join("?" for _ in cols)
            pairs.append(
                (
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
            )
            written += 1
        counts[table] = written

    db.run_many(pairs)
    set_meta(db, "last_restore", mode)
    log(db, actor, "", "backup", f"restored ({mode}): " + ", ".join(
        f"{v} {k}" for k, v in counts.items() if v
    ))
    return counts
