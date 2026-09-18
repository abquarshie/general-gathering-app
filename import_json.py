"""
One-off import of data/portal_state.json (the earlier JSON version) into the
database. Safe to run twice: gatherings that already exist are skipped.

    python3 import_json.py

Phone numbers are deliberately dropped — the app no longer keeps contacts.
"""

import json
from datetime import date
from pathlib import Path

import db as store

SOURCE = Path("data") / "portal_state.json"


def main() -> None:
    if not SOURCE.exists():
        print(f"Nothing to import: {SOURCE} does not exist.")
        return

    data = json.loads(SOURCE.read_text())
    d = store.Database(None)
    print(f"Importing into {d.label}")

    for key, ev in data.items():
        if store.event_exists(d, key):
            print(f"  {key}: already in the database, skipped")
            continue

        store.create_event(d, key, date.fromisoformat(ev["event_date"]), ev.get("venue", ""))
        store.save_event(
            d,
            key,
            date.fromisoformat(ev["event_date"]),
            ev.get("venue", ""),
            ev.get("checklist", {}),
        )

        for dept, fields in (ev.get("departments") or {}).items():
            store.save_department(d, key, dept, fields)

        if ev.get("shifts"):
            store.save_shifts(d, key, ev["shifts"])

        by_dept: dict[str, list[dict]] = {}
        for t in ev.get("targets") or []:
            by_dept.setdefault(str(t.get("Department")), []).append(t)
        for dept, rows in by_dept.items():
            store.save_dept_targets(d, key, dept, rows)

        people = [
            {
                "name": v.get("Name", ""),
                "gender": v.get("Gender", ""),
                "privilege": v.get("Privilege", ""),
                "dept": v.get("Department", ""),
                "shift": v.get("Shift", ""),
                "status": v.get("Status", "Invited"),
                "notes": v.get("Notes", ""),
            }
            for v in ev.get("volunteers") or []
            if str(v.get("Name", "")).strip()
        ]
        store.save_volunteers(d, key, people)
        print(f"  {key}: {len(people)} volunteers, {len(ev.get('departments') or {})} departments")

    print("\nDone. Check the app, then delete data/portal_state.json — it still")
    print("contains the phone numbers this version no longer stores.")


if __name__ == "__main__":
    main()
