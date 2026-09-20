"""
The two documents that come out of the app.

* Master list — every department in turn, its oversight above the names.
* Rotation list — one column per shift, so a department can see the handover.

Every function takes the volunteer frame and a context dict:

    part, year, event_date, venue, today, departments, dept_order, shifts, hours
"""

from __future__ import annotations

import pandas as pd

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


def _lead(ctx: dict, dept: str) -> str:
    d = ctx["departments"].get(dept, {})
    bits = []
    if d.get("overseer"):
        bits.append(f"Overseer: {d['overseer']}")
    if d.get("assistant"):
        bits.append(f"Assistant: {d['assistant']}")
    if d.get("keymen"):
        bits.append(f"Keymen: {d['keymen']}")
    return " &nbsp;·&nbsp; ".join(bits) or "No oversight recorded"


def _header(ctx: dict, title: str, extra: str = "") -> str:
    venue = f" &middot; {ctx['venue']}" if ctx.get("venue") else ""
    return f"""<!doctype html><meta charset="utf-8">
<title>{title} — {ctx['part']} {ctx['year']}</title><style>{PRINT_CSS}</style>
<h1>{title}</h1>
<p class="sub">General Gathering {ctx['part']}, {ctx['year']} &middot;
{ctx['event_date']:%A, %d %B %Y}{venue}{extra} &middot; printed {ctx['today']:%d %B %Y}</p>
"""


def master_list_html(frame: pd.DataFrame, ctx: dict) -> str:
    sections = []
    for dept in ctx["dept_order"]:
        rows = frame[frame["Department"] == dept].sort_values(["Shift", "Name"])
        body = "".join(
            f"<tr><td class='num'>{i}</td><td>{r['Name']}</td><td>{r['Congregation']}</td>"
            f"<td>{r['Gender']}</td><td>{r['Privilege']}</td><td>{r['Shift']}</td>"
            f"<td>{r['Status']}</td></tr>"
            for i, (_, r) in enumerate(rows.iterrows(), start=1)
        ) or "<tr><td colspan='7'>No volunteers recorded.</td></tr>"
        sections.append(
            f"<section><h2>{dept}</h2><p class='lead'>{_lead(ctx, dept)}</p>"
            "<table><tr><th></th><th>Name</th><th>Congregation</th><th>Gender</th>"
            f"<th>Privilege</th><th>Shift</th><th>Status</th></tr>{body}</table>"
            f"<p class='lead'>{len(rows)} volunteers</p></section>"
        )
    extra = f" &middot; {len(frame)} volunteers"
    return _header(ctx, "Master volunteer list", extra) + "".join(sections)


def rotation_frame(frame: pd.DataFrame, ctx: dict, dept: str) -> pd.DataFrame:
    cols = {
        name: sorted(
            frame[(frame["Department"] == dept) & (frame["Shift"] == name)]["Name"].tolist()
        )
        for name in ctx["shifts"]
    }
    depth = max((len(v) for v in cols.values()), default=0)
    return pd.DataFrame({k: v + [""] * (depth - len(v)) for k, v in cols.items()})


def rotation_html(frame: pd.DataFrame, ctx: dict) -> str:
    sections = []
    for dept in ctx["dept_order"]:
        table = rotation_frame(frame, ctx, dept)
        if table.empty:
            continue
        heads = "".join(
            f"<th>{n}<br><span style='font-weight:400'>{ctx['hours'].get(n) or '—'}</span></th>"
            for n in table.columns
        )
        body = "".join(
            f"<tr><td class='num'>{i + 1}</td>"
            + "".join(f"<td>{v}</td>" for v in row)
            + "</tr>"
            for i, row in enumerate(table.itertuples(index=False))
        )
        counts = " &nbsp;·&nbsp; ".join(f"{c}: {(table[c] != '').sum()}" for c in table.columns)
        sections.append(
            f"<section><h2>{dept}</h2><p class='lead'>{_lead(ctx, dept)}</p>"
            f"<table><tr><th></th>{heads}</tr>{body}</table>"
            f"<p class='lead'>{counts}</p></section>"
        )
    return _header(ctx, "Department rotation list") + (
        "".join(sections) or "<p>No volunteers recorded.</p>"
    )


def master_csv(frame: pd.DataFrame, ctx: dict) -> str:
    out = []
    for dept in ctx["dept_order"]:
        d = ctx["departments"].get(dept, {})
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


def rotation_csv(frame: pd.DataFrame, ctx: dict) -> str:
    out = []
    for dept in ctx["dept_order"]:
        for _, row in rotation_frame(frame, ctx, dept).iterrows():
            out.append({"Department": dept, **row.to_dict()})
    return pd.DataFrame(out).to_csv(index=False)
