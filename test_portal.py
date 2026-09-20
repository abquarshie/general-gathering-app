"""
Tests for the gathering portal.

    python3 -m pytest -q

They run against a throwaway SQLite database, so they need no Neon connection
and no secrets. The same SQL runs on Postgres, so a passing suite here is good
evidence the deployed app is sound — the one thing it cannot catch is a
Postgres-only syntax difference.

Every test here exists because the behaviour it checks is easy to break by
accident: matching an edited row to the right person, a shift rename leaving
volunteers stranded, a restore silently dropping data.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as store  # noqa: E402
import documents as docs  # noqa: E402

KEY = "Part 1 2026"
DAY = datetime.date(2026, 11, 14)

SHIFTS = [
    {"Shift": "Early setup", "Report": "06:00", "Start": "06:15", "End": "08:30"},
    {"Shift": "Morning", "Report": "07:45", "Start": "08:30", "End": "12:45"},
    {"Shift": "Afternoon", "Report": "12:30", "Start": "13:15", "End": "17:15"},
]


def person(name, dept="Parking", shift="Morning", status="Confirmed", cong="Kaneshie Ga", **kw):
    return {
        "name": name,
        "gender": kw.get("gender", "Female"),
        "privilege": kw.get("privilege", "Publisher"),
        "congregation": cong,
        "phone": kw.get("phone", ""),
        "dept": dept,
        "shift": shift,
        "status": status,
        "notes": kw.get("notes", ""),
    }


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "test.db")
    d = store.Database(None)
    store.create_event(d, KEY, DAY, "Achimota Hall", actor="test")
    store.save_shifts(d, KEY, SHIFTS, actor="test")
    return d


def names(d, key=KEY):
    return sorted(r["name"] for r in store.load_volunteers(d, key))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_version_recorded(db):
    assert db.version() >= 1


def test_migrate_is_idempotent(db):
    assert db.migrate() == []  # already at the latest step
    assert db.version() >= 1


# ---------------------------------------------------------------------------
# Saving the volunteer grid
# ---------------------------------------------------------------------------


def test_new_rows_insert(db):
    counts = store.save_volunteers(db, KEY, [person("Ama Tetteh"), person("Kofi Larbi")])
    assert counts["added"] == 2
    assert names(db) == ["Ama Tetteh", "Kofi Larbi"]


def test_editing_a_row_keeps_its_identity(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    row = store.load_volunteers(db, KEY)[0]

    edited = person("Ama Tetteh", shift="Afternoon", status="Unavailable")
    edited["id"] = row["id"]
    counts = store.save_volunteers(db, KEY, [edited])

    after = store.load_volunteers(db, KEY)
    assert counts == {"added": 0, "changed": 1, "removed": 0}
    assert len(after) == 1
    assert after[0]["person_id"] == row["person_id"]
    assert after[0]["shift"] == "Afternoon"


def test_correcting_a_spelling_is_not_a_delete_and_add(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    row = store.load_volunteers(db, KEY)[0]

    fixed = person("Ama Tettey")
    fixed["id"] = row["id"]
    store.save_volunteers(db, KEY, [fixed])

    after = store.load_volunteers(db, KEY)
    assert len(after) == 1
    assert after[0]["person_id"] == row["person_id"]  # same person, new spelling
    assert not store.load_removed(db, KEY)


def test_a_number_is_kept_with_the_person_not_the_assignment(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh", phone="024 555 0101")])
    assert store.load_volunteers(db, KEY)[0]["phone"] == "024 555 0101"

    row = store.load_volunteers(db, KEY)[0]
    edited = person("Ama Tetteh", phone="024 555 0199")
    edited["id"] = row["id"]
    store.save_volunteers(db, KEY, [edited])

    after = store.load_volunteers(db, KEY)
    assert len(after) == 1
    assert after[0]["phone"] == "024 555 0199"
    assert after[0]["person_id"] == row["person_id"]


def test_an_older_database_gains_the_phone_column(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "old.db")
    monkeypatch.setattr(store, "MIGRATIONS", [(1, [])])
    old = store.Database(None)
    old.run(
        "CREATE TABLE IF NOT EXISTS people_legacy (id TEXT)"
    )  # a database that stopped at step 1
    assert old.version() == 1

    monkeypatch.undo()
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "old.db")
    upgraded = store.Database(None)
    assert upgraded.version() >= 2
    store.create_event(upgraded, KEY, DAY, actor="test")
    store.save_volunteers(upgraded, KEY, [person("Ama Tetteh", phone="024")])
    assert store.load_volunteers(upgraded, KEY)[0]["phone"] == "024"


def test_two_people_with_the_same_name_stay_apart(db):
    store.save_volunteers(
        db,
        KEY,
        [
            person("Kofi Larbi", cong="Kaneshie Ga"),
            person("Kofi Larbi", cong="Adabraka Ga", dept="Attendant"),
        ],
    )
    rows = store.load_volunteers(db, KEY)
    assert len({r["person_id"] for r in rows}) == 2


def test_dropping_a_row_soft_deletes_and_restores(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh"), person("Kofi Larbi")])
    keep = [r for r in store.load_volunteers(db, KEY) if r["name"] == "Ama Tetteh"]
    kept = person("Ama Tetteh")
    kept["id"] = keep[0]["id"]

    counts = store.save_volunteers(db, KEY, [kept])
    assert counts["removed"] == 1
    assert names(db) == ["Ama Tetteh"]

    gone = store.load_removed(db, KEY)
    assert [r["name"] for r in gone] == ["Kofi Larbi"]

    store.restore_assignment(db, gone[0]["id"], KEY, actor="test")
    assert names(db) == ["Ama Tetteh", "Kofi Larbi"]
    assert not store.load_removed(db, KEY)


def test_blank_names_are_ignored(db):
    store.save_volunteers(db, KEY, [person("  "), person("Ama Tetteh")])
    assert names(db) == ["Ama Tetteh"]


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


def test_half_follows_the_start_time():
    assert store.half_of("06:15") == "Morning"
    assert store.half_of("11:59") == "Morning"
    assert store.half_of("12:00") == "Afternoon"
    assert store.half_of("") == "Morning"


def test_renaming_a_shift_carries_volunteers_and_targets(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh"), person("Kofi Larbi")])
    store.save_dept_targets(
        db, KEY, "Parking", [{"Shift": "Morning", "Needed": 6, "Min elders": 1, "Min servants": 0}]
    )

    moved = store.rename_shift(db, KEY, "Morning", "Main session", actor="test")
    store.save_shifts(
        db,
        KEY,
        [{**s, "Shift": "Main session"} if s["Shift"] == "Morning" else s for s in SHIFTS],
        actor="test",
    )

    assert moved == 2
    assert {r["shift"] for r in store.load_volunteers(db, KEY)} == {"Main session"}
    assert [t["Shift"] for t in store.load_targets(db, KEY)] == ["Main session"]
    assert store.stranded_shifts(db, KEY) == []


def test_dropping_a_shift_strands_its_volunteers_visibly(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    store.save_shifts(db, KEY, [s for s in SHIFTS if s["Shift"] != "Morning"], actor="test")

    stranded = store.stranded_shifts(db, KEY)
    assert [(s["shift"], s["n"]) for s in stranded] == [("Morning", 1)]


# ---------------------------------------------------------------------------
# Pasting and returning volunteers
# ---------------------------------------------------------------------------


def test_pasted_names_skip_duplicates_and_read_congregations(db):
    added = store.add_names(
        db,
        KEY,
        ["Ama Tetteh", "Kofi Larbi, Adabraka Ga", "", "  "],
        "Cleaning",
        "Afternoon",
        "Kaneshie Ga",
        actor="test",
    )
    assert added == 2
    rows = {r["name"]: r for r in store.load_volunteers(db, KEY)}
    assert rows["Kofi Larbi"]["congregation"] == "Adabraka Ga"
    assert rows["Ama Tetteh"]["congregation"] == "Kaneshie Ga"

    assert store.add_names(db, KEY, ["Ama Tetteh"], "Cleaning", "Afternoon", "Kaneshie Ga") == 0


def test_service_history_follows_a_person_between_gatherings(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    later = "Part 1 2027"
    store.create_event(db, later, datetime.date(2027, 11, 13), actor="test")
    counts = store.copy_forward(db, KEY, later, actor="test")

    assert counts["volunteers"] == 1
    carried = store.load_volunteers(db, later)
    original = store.load_volunteers(db, KEY)
    assert carried[0]["person_id"] == original[0]["person_id"]
    assert carried[0]["status"] == "Invited"  # last year's yes is not this year's

    # nobody is offered twice: they are already on the new list
    assert store.served_before(db, later) == []

    store.save_volunteers(db, later, [])  # remove them again
    offered = store.served_before(db, later)
    assert [(p["name"], p["times"]) for p in offered] == [("Ama Tetteh", 1)]


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def test_backup_round_trip(db, tmp_path, monkeypatch):
    store.save_volunteers(db, KEY, [person("Ama Tetteh"), person("Kofi Larbi")])
    store.save_department(db, KEY, "Parking", {"overseer": "K. Mensah"}, actor="test")
    dump = json.loads(json.dumps(store.export_all(db)))

    monkeypatch.setattr(store, "DB_FILE", tmp_path / "fresh.db")
    fresh = store.Database(None)
    assert store.list_events(fresh) == []

    store.restore_all(fresh, dump, "replace", actor="test")
    assert names(fresh) == ["Ama Tetteh", "Kofi Larbi"]
    assert store.load_departments(fresh, KEY)["Parking"]["overseer"] == "K. Mensah"


def test_merge_adds_nothing_twice_and_keeps_later_edits(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    dump = store.export_all(db)

    row = store.load_volunteers(db, KEY)[0]
    edited = person("Ama Tetteh", status="Unavailable")
    edited["id"] = row["id"]
    store.save_volunteers(db, KEY, [edited])

    store.restore_all(db, dump, "merge", actor="test")
    after = store.load_volunteers(db, KEY)
    assert len(after) == 1
    assert after[0]["status"] == "Unavailable"  # the live edit survives a merge


def test_a_damaged_backup_is_refused_before_anything_is_written(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    broken = {
        "format": 1,
        "tables": {
            "people": [],
            "assignments": [{"id": "x", "event_key": KEY, "person_id": "ghost"}],
        },
    }
    assert store.validate_backup(broken)
    with pytest.raises(ValueError):
        store.restore_all(db, broken, "replace", actor="test")
    assert names(db) == ["Ama Tetteh"]  # untouched


def test_nonsense_files_are_refused():
    assert store.validate_backup({"format": 9, "tables": {}})
    assert store.validate_backup("not a backup")
    assert store.validate_backup({"format": 1, "tables": {"nope": []}})


# ---------------------------------------------------------------------------
# Change log
# ---------------------------------------------------------------------------


def test_every_write_moves_the_cache_stamp(db):
    first = store.data_stamp(db, KEY)
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    second = store.data_stamp(db, KEY)
    assert first != second

    store.save_department(db, KEY, "Parking", {"overseer": "K. Mensah"}, actor="test")
    assert store.data_stamp(db, KEY) != second


def test_old_log_entries_are_pruned(db):
    store.save_volunteers(db, KEY, [person("Ama Tetteh")])
    old = (datetime.datetime.now() - datetime.timedelta(days=600)).isoformat(timespec="seconds")
    db.run(
        "INSERT INTO changes (id, at, actor, event_key, area, detail) VALUES (?, ?, ?, ?, ?, ?)",
        ("ancient", old, "test", KEY, "volunteers", "long ago"),
    )
    assert store.prune_changes(db, months=12) == 1
    assert all(c["detail"] != "long ago" for c in store.recent_changes(db, KEY, 50))


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@pytest.fixture()
def frame():
    return pd.DataFrame(
        [
            ("Yaw Boateng", "Male", "Servant", "Chorkor Ga", "024 1", "Parking", "Afternoon", "Confirmed"),
            ("Ama Tetteh", "Female", "Publisher", "Kaneshie Ga", "024 2", "Parking", "Morning", "Confirmed"),
            ("Nii Armah", "Male", "Elder", "Mataheko Ga", "", "Parking", "Early setup", "Confirmed"),
        ],
        columns=[
            "Name",
            "Gender",
            "Privilege",
            "Congregation",
            "Phone",
            "Department",
            "Shift",
            "Status",
        ],
    )


@pytest.fixture()
def ctx():
    return {
        "part": "Part 1",
        "year": "2026",
        "event_date": DAY,
        "venue": "Achimota Hall",
        "today": datetime.date(2026, 9, 20),
        "departments": {"Parking": {"overseer": "K. Mensah", "keymen": "T. Addo"}},
        "dept_order": ["Parking", "Attendant"],
        "shifts": ["Early setup", "Morning", "Afternoon"],
        "hours": {"Early setup": "06:15–08:30", "Morning": "08:30–12:45", "Afternoon": "13:15–17:15"},
    }


def test_rows_follow_the_running_order_not_the_alphabet(frame, ctx):
    ordered = docs.in_shift_order(frame, ctx)["Shift"].tolist()
    assert ordered == ["Early setup", "Morning", "Afternoon"]


def test_master_list_carries_the_oversight_names(frame, ctx):
    html = docs.master_list_html(frame, ctx)
    assert "Overseer: K. Mensah" in html
    assert "Keymen: T. Addo" in html
    assert "Kaneshie Ga" in html
    assert "024 2" in html  # the number travels with the master list


def test_rotation_puts_each_shift_in_its_own_column(frame, ctx):
    table = docs.rotation_frame(frame, ctx, "Parking")
    assert list(table.columns) == ["Early setup", "Morning", "Afternoon"]
    assert table["Morning"].tolist() == ["Ama Tetteh"]


def test_empty_data_still_produces_usable_files(ctx):
    empty = pd.DataFrame(
        columns=[
            "Name",
            "Gender",
            "Privilege",
            "Congregation",
            "Phone",
            "Department",
            "Shift",
            "Status",
        ]
    )
    assert "Master volunteer list" in docs.master_list_html(empty, ctx)
    assert docs.master_csv(empty, ctx).startswith("Department,")
    assert list(docs.rotation_table(empty, ctx).columns)[0] == "Department"


def test_word_and_pdf_build(frame, ctx):
    pytest.importorskip("docx")
    pytest.importorskip("reportlab")
    assert docs.master_list_docx(frame, ctx)[:2] == b"PK"  # a zip, as .docx is
    assert docs.rotation_docx(frame, ctx)[:2] == b"PK"
    assert docs.master_list_pdf(frame, ctx).startswith(b"%PDF")
    assert docs.rotation_pdf(frame, ctx).startswith(b"%PDF")
