"""
Storage for the gathering portal.

One set of SQL statements runs against two drivers:

* Neon Postgres, when a [postgres] block exists in secrets. This is what a
  deployed app uses — the data outlives redeploys and each row is written on
  its own, so two people editing different departments don't overwrite each
  other.
* SQLite in data/portal.db otherwise, so the app runs on a laptop with no
  setup at all.

Records are kept after the gathering. Next year you copy the previous
gathering forward and edit it rather than typing everyone in again.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
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
    """CREATE TABLE IF NOT EXISTS volunteers (
        id        TEXT PRIMARY KEY,
        event_key TEXT NOT NULL,
        name      TEXT DEFAULT '',
        gender    TEXT DEFAULT '',
        privilege TEXT DEFAULT '',
        dept      TEXT DEFAULT '',
        shift     TEXT DEFAULT '',
        status    TEXT DEFAULT 'Invited',
        notes     TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS volunteers_event ON volunteers (event_key)",
]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _dsn(secrets) -> str | None:
    """Connection string from secrets, if Postgres is configured."""
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
    """Thin wrapper so the app never cares which driver is underneath."""

    def __init__(self, secrets=None):
        self.dsn = _dsn(secrets) if secrets is not None else None
        self.kind = "postgres" if self.dsn else "sqlite"
        if self.kind == "sqlite":
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.setup()

    @property
    def label(self) -> str:
        return "Neon Postgres" if self.kind == "postgres" else f"SQLite ({DB_FILE})"

    @contextmanager
    def _conn(self):
        if self.kind == "postgres":
            import psycopg

            conn = psycopg.connect(self.dsn)
        else:
            conn = sqlite3.connect(DB_FILE)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.kind == "postgres" else statement

    def run(self, statement: str, params: tuple = ()) -> None:
        with self._conn() as conn:
            conn.cursor().execute(self._sql(statement), params)

    def run_many(self, pairs: list[tuple[str, tuple]]) -> None:
        """Several statements in one transaction — all of them land, or none."""
        with self._conn() as conn:
            cur = conn.cursor()
            for statement, params in pairs:
                cur.execute(self._sql(statement), params)

    def rows(self, statement: str, params: tuple = ()) -> list[dict]:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(self._sql(statement), params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def setup(self) -> None:
        with self._conn() as conn:
            cur = conn.cursor()
            for statement in SCHEMA:
                cur.execute(statement)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def list_events(db: Database) -> list[dict]:
    return db.rows("SELECT event_key, event_date, venue FROM events ORDER BY event_date DESC")


def event_exists(db: Database, key: str) -> bool:
    return bool(db.rows("SELECT 1 FROM events WHERE event_key = ?", (key,)))


def create_event(db: Database, key: str, event_date: date, venue: str = "") -> None:
    db.run(
        "INSERT INTO events (event_key, event_date, venue, checklist, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_key) DO NOTHING",
        (key, event_date.isoformat(), venue, "{}", datetime.now().isoformat(timespec="seconds")),
    )


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


def save_event(db: Database, key: str, event_date: date, venue: str, checklist: dict) -> None:
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


def delete_event(db: Database, key: str) -> None:
    db.run_many(
        [
            ("DELETE FROM volunteers WHERE event_key = ?", (key,)),
            ("DELETE FROM targets WHERE event_key = ?", (key,)),
            ("DELETE FROM shifts WHERE event_key = ?", (key,)),
            ("DELETE FROM departments WHERE event_key = ?", (key,)),
            ("DELETE FROM events WHERE event_key = ?", (key,)),
        ]
    )


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def load_departments(db: Database, key: str) -> dict[str, dict]:
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


def save_department(db: Database, key: str, dept: str, f: dict) -> None:
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


# ---------------------------------------------------------------------------
# Shifts and targets
# ---------------------------------------------------------------------------


def load_shifts(db: Database, key: str) -> list[dict]:
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


def save_shifts(db: Database, key: str, rows: list[dict]) -> None:
    pairs: list[tuple[str, tuple]] = [("DELETE FROM shifts WHERE event_key = ?", (key,))]
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
                    str(r.get("Half") or "Morning"),
                    str(r.get("Report") or ""),
                    str(r.get("Start") or ""),
                    str(r.get("End") or ""),
                    i,
                ),
            )
        )
    db.run_many(pairs)


def load_targets(db: Database, key: str) -> list[dict]:
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


def save_dept_targets(db: Database, key: str, dept: str, rows: list[dict]) -> None:
    pairs: list[tuple[str, tuple]] = [
        ("DELETE FROM targets WHERE event_key = ? AND dept = ?", (key, dept))
    ]
    for r in rows:
        needed = int(r.get("Needed") or 0)
        min_e = int(r.get("Min elders") or 0)
        min_s = int(r.get("Min servants") or 0)
        if not (needed or min_e or min_s):
            continue
        pairs.append(
            (
                "INSERT INTO targets (event_key, dept, shift, needed, min_elders, min_servants) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, dept, str(r.get("Shift")), needed, min_e, min_s),
            )
        )
    db.run_many(pairs)


# ---------------------------------------------------------------------------
# Volunteers
# ---------------------------------------------------------------------------

VOL_FIELDS = ("name", "gender", "privilege", "dept", "shift", "status", "notes")


def load_volunteers(db: Database, key: str) -> list[dict]:
    return db.rows(
        "SELECT id, name, gender, privilege, dept, shift, status, notes "
        "FROM volunteers WHERE event_key = ? ORDER BY dept, shift, name",
        (key,),
    )


def save_volunteers(db: Database, key: str, rows: list[dict]) -> None:
    """Write only the rows that changed, and delete the ones that went away.

    Rows arrive without ids because they come from a table editor, so each one
    is matched to an existing record by name first. Two people editing
    different departments therefore don't collide.
    """
    existing = {r["id"]: r for r in load_volunteers(db, key)}
    by_name: dict[str, list[str]] = {}
    for vid, r in existing.items():
        by_name.setdefault(str(r["name"]).strip().lower(), []).append(vid)

    pairs: list[tuple[str, tuple]] = []
    claimed: set[str] = set()

    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        candidates = [v for v in by_name.get(name.lower(), []) if v not in claimed]
        vid = candidates[0] if candidates else str(uuid.uuid4())
        claimed.add(vid)
        values = tuple(str(row.get(f, "") or "") for f in VOL_FIELDS)
        before = existing.get(vid)
        if before and tuple(str(before.get(f, "") or "") for f in VOL_FIELDS) == values:
            continue
        pairs.append(
            (
                """INSERT INTO volunteers (id, event_key, name, gender, privilege, dept, shift, status, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET
                     name = excluded.name, gender = excluded.gender,
                     privilege = excluded.privilege, dept = excluded.dept,
                     shift = excluded.shift, status = excluded.status,
                     notes = excluded.notes""",
                (vid, key) + values,
            )
        )

    for vid in existing:
        if vid not in claimed:
            pairs.append(("DELETE FROM volunteers WHERE id = ?", (vid,)))

    if pairs:
        db.run_many(pairs)


# ---------------------------------------------------------------------------
# Carrying a gathering forward
# ---------------------------------------------------------------------------


def copy_forward(db: Database, source: str, target: str, reset_status: str = "Invited") -> dict:
    """Copy people, departments, shifts and targets from an earlier gathering.

    Statuses reset, because last year's yes is not this year's yes. The
    checklist and meeting dates do not come across either.
    """
    counts = {"volunteers": 0, "departments": 0, "shifts": 0, "targets": 0}
    pairs: list[tuple[str, tuple]] = []

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
                (
                    target,
                    t["Department"],
                    t["Shift"],
                    t["Needed"],
                    t["Min elders"],
                    t["Min servants"],
                ),
            )
        )
        counts["targets"] += 1

    for v in load_volunteers(db, source):
        pairs.append(
            (
                "INSERT INTO volunteers (id, event_key, name, gender, privilege, dept, shift, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    target,
                    v["name"],
                    v["gender"],
                    v["privilege"],
                    v["dept"],
                    v["shift"],
                    reset_status,
                    "",
                ),
            )
        )
        counts["volunteers"] += 1

    if pairs:
        db.run_many(pairs)
    return counts
