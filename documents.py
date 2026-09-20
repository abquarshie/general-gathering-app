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


def in_shift_order(rows: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    """Alphabetical order would put Afternoon before Morning."""
    order = {name: i for i, name in enumerate(ctx["shifts"])}
    return rows.assign(_seq=rows["Shift"].map(lambda s: order.get(s, len(order)))).sort_values(
        ["_seq", "Name"]
    ).drop(columns="_seq")


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
<title>{title} — {ctx.get('part_label', ctx['part'])} {ctx['year']}</title><style>{PRINT_CSS}</style>
<h1>{title}</h1>
<p class="sub">{ctx.get('part_label', ctx['part'])}, {ctx['year']} &middot;
{ctx['event_date']:%A, %d %B %Y}{venue}{extra} &middot; printed {ctx['today']:%d %B %Y}</p>
"""


def master_list_html(frame: pd.DataFrame, ctx: dict) -> str:
    sections = []
    for dept in ctx["dept_order"]:
        rows = in_shift_order(frame[frame["Department"] == dept], ctx)
        body = "".join(
            f"<tr><td class='num'>{i}</td><td>{r['Name']}</td><td>{r['Congregation']}</td>"
            f"<td>{r.get('Phone', '')}</td><td>{r['Gender']}</td><td>{r['Privilege']}</td>"
            f"<td>{r['Shift']}</td><td>{r['Status']}</td></tr>"
            for i, (_, r) in enumerate(rows.iterrows(), start=1)
        ) or "<tr><td colspan='8'>No volunteers recorded.</td></tr>"
        sections.append(
            f"<section><h2>{dept}</h2><p class='lead'>{_lead(ctx, dept)}</p>"
            "<table><tr><th></th><th>Name</th><th>Congregation</th><th>Phone</th>"
            f"<th>Gender</th><th>Privilege</th><th>Shift</th><th>Status</th></tr>{body}</table>"
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


MASTER_COLUMNS = [
    "Department",
    "Dept overseer",
    "Dept assistant",
    "Keymen",
    "Name",
    "Congregation",
    "Phone",
    "Gender",
    "Privilege",
    "Shift",
    "Status",
]


def master_frame(frame: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    """The master list as one flat table. Empty still carries its headings."""
    out = []
    for dept in ctx["dept_order"]:
        d = ctx["departments"].get(dept, {})
        for _, r in in_shift_order(frame[frame["Department"] == dept], ctx).iterrows():
            out.append(
                {
                    "Department": dept,
                    "Dept overseer": d.get("overseer", ""),
                    "Dept assistant": d.get("assistant", ""),
                    "Keymen": d.get("keymen", ""),
                    "Name": r["Name"],
                    "Congregation": r["Congregation"],
                    "Phone": r.get("Phone", ""),
                    "Gender": r["Gender"],
                    "Privilege": r["Privilege"],
                    "Shift": r["Shift"],
                    "Status": r["Status"],
                }
            )
    return pd.DataFrame(out, columns=MASTER_COLUMNS)


def master_csv(frame: pd.DataFrame, ctx: dict) -> str:
    return master_frame(frame, ctx).to_csv(index=False)


def rotation_table(frame: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    out = []
    for dept in ctx["dept_order"]:
        for _, row in rotation_frame(frame, ctx, dept).iterrows():
            out.append({"Department": dept, **row.to_dict()})
    return pd.DataFrame(out, columns=["Department"] + list(ctx["shifts"]))


def rotation_csv(frame: pd.DataFrame, ctx: dict) -> str:
    return rotation_table(frame, ctx).to_csv(index=False)


# ---------------------------------------------------------------------------
# Word and PDF
#
# Both are generated in pure Python so they work on Streamlit Cloud, where
# there is no LibreOffice and no Node. The libraries are imported inside the
# functions, so the app still starts if they are missing.
# ---------------------------------------------------------------------------

INK = "16202B"
PAPER = "F7F5F0"
LINE = "DCD8CF"
SOFT = "5B6B7C"


def _subtitle(ctx: dict, extra: str = "") -> str:
    venue = f" · {ctx['venue']}" if ctx.get("venue") else ""
    return (
        f"{ctx.get('part_label', ctx['part'])}, {ctx['year']} · "
        f"{ctx['event_date']:%A, %d %B %Y}{venue}{extra} · printed {ctx['today']:%d %B %Y}"
    )


def _plain_lead(ctx: dict, dept: str) -> str:
    return _lead(ctx, dept).replace(" &nbsp;·&nbsp; ", " · ")


def _master_sections(frame: pd.DataFrame, ctx: dict):
    """(department, lead line, header row, body rows) for each department."""
    header = ["", "Name", "Congregation", "Gender", "Privilege", "Shift", "Status"]
    for dept in ctx["dept_order"]:
        rows = in_shift_order(frame[frame["Department"] == dept], ctx)
        body = [
            [
                str(i),
                r["Name"],
                r["Congregation"],
                r["Gender"],
                r["Privilege"],
                r["Shift"],
                r["Status"],
            ]
            for i, (_, r) in enumerate(rows.iterrows(), start=1)
        ]
        yield dept, _plain_lead(ctx, dept), header, body, len(rows)


def _rotation_sections(frame: pd.DataFrame, ctx: dict):
    for dept in ctx["dept_order"]:
        table = rotation_frame(frame, ctx, dept)
        if table.empty:
            continue
        header = [""] + [f"{c}\n{ctx['hours'].get(c) or '—'}" for c in table.columns]
        body = [
            [str(i + 1)] + [str(v) for v in row]
            for i, row in enumerate(table.itertuples(index=False))
        ]
        counts = " · ".join(f"{c}: {(table[c] != '').sum()}" for c in table.columns)
        yield dept, _plain_lead(ctx, dept), header, body, counts


def _docx(title: str, subtitle: str, sections, widths) -> bytes:
    from io import BytesIO

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Cm(1.5)
        section.left_margin = section.right_margin = Cm(1.5)

    normal = doc.styles["Normal"]
    normal.font.name = "Helvetica"
    normal.font.size = Pt(9)

    heading = doc.add_paragraph()
    run = heading.add_run(title)
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor.from_string(INK)

    sub = doc.add_paragraph()
    sub_run = sub.add_run(subtitle)
    sub_run.font.size = Pt(8)
    sub_run.font.color.rgb = RGBColor.from_string(SOFT)

    for dept, lead, header, body, tail in sections:
        h = doc.add_paragraph()
        h_run = h.add_run(dept)
        h_run.bold = True
        h_run.font.size = Pt(12)

        lead_p = doc.add_paragraph()
        lead_run = lead_p.add_run(lead)
        lead_run.font.size = Pt(8)
        lead_run.font.color.rgb = RGBColor.from_string(SOFT)

        table = doc.add_table(rows=1, cols=len(header))
        table.style = "Table Grid"
        # Word ignores cell widths unless autofit is off, and then wraps names
        table.autofit = False
        for column, width in zip(table.columns, widths(len(header))):
            column.width = Cm(width)
        for cell, text, width in zip(table.rows[0].cells, header, widths(len(header))):
            cell.width = Cm(width)
            para = cell.paragraphs[0]
            cell_run = para.add_run(text.replace("\n", " "))
            cell_run.bold = True
            cell_run.font.size = Pt(8)

        for line in body or [["", "No volunteers recorded."] + [""] * (len(header) - 2)]:
            cells = table.add_row().cells
            for cell, text, width in zip(cells, line, widths(len(header))):
                cell.width = Cm(width)
                cell_run = cell.paragraphs[0].add_run(str(text))
                cell_run.font.size = Pt(8.5)

        foot = doc.add_paragraph()
        foot_run = foot.add_run(f"{tail} volunteers" if isinstance(tail, int) else str(tail))
        foot_run.font.size = Pt(8)
        foot_run.font.color.rgb = RGBColor.from_string(SOFT)
        foot.alignment = WD_ALIGN_PARAGRAPH.LEFT

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _pdf(title: str, subtitle: str, sections, col_widths) -> bytes:
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title=title,
    )
    base = getSampleStyleSheet()
    ink = colors.HexColor(f"#{INK}")
    soft = colors.HexColor(f"#{SOFT}")

    title_style = ParagraphStyle(
        "title", parent=base["Title"], fontSize=17, alignment=TA_LEFT, textColor=ink, spaceAfter=2
    )
    sub_style = ParagraphStyle("sub", parent=base["Normal"], fontSize=8.5, textColor=soft)
    dept_style = ParagraphStyle(
        "dept", parent=base["Heading2"], fontSize=12, textColor=ink, spaceBefore=0, spaceAfter=1
    )
    lead_style = ParagraphStyle("lead", parent=base["Normal"], fontSize=8, textColor=soft)
    cell_style = ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=10)
    head_style = ParagraphStyle("head", parent=cell_style, fontSize=8, textColor=ink)

    story = [Paragraph(title, title_style), Paragraph(subtitle, sub_style), Spacer(1, 6 * mm)]

    for dept, lead, header, body, tail in sections:
        widths = col_widths(len(header), doc.width)
        data = [[Paragraph(h.replace("\n", "<br/>"), head_style) for h in header]]
        for line in body or [["", "No volunteers recorded."] + [""] * (len(header) - 2)]:
            data.append([Paragraph(str(v), cell_style) for v in line])

        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{PAPER}")),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(f"#{LINE}")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ]
            )
        )
        foot = f"{tail} volunteers" if isinstance(tail, int) else str(tail)
        story.append(
            KeepTogether(
                [
                    Paragraph(dept, dept_style),
                    Paragraph(lead, lead_style),
                    Spacer(1, 2 * mm),
                    table,
                    Spacer(1, 1 * mm),
                    Paragraph(foot, lead_style),
                    Spacer(1, 6 * mm),
                ]
            )
        )

    doc.build(story)
    return buffer.getvalue()


def _master_widths_cm(n: int) -> list:
    return [0.9, 4.2, 3.6, 1.8, 2.2, 2.6, 2.2][:n]


def _master_widths_pt(n: int, total: float) -> list:
    share = [0.05, 0.24, 0.21, 0.1, 0.13, 0.15, 0.12][:n]
    return [total * s for s in share]


def _even_widths_cm(n: int) -> list:
    return [0.9] + [(17.0 - 0.9) / max(n - 1, 1)] * (n - 1)


def _even_widths_pt(n: int, total: float) -> list:
    first = total * 0.05
    return [first] + [(total - first) / max(n - 1, 1)] * (n - 1)


def master_list_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    return _docx(
        "Master volunteer list",
        _subtitle(ctx, f" · {len(frame)} volunteers"),
        _master_sections(frame, ctx),
        lambda n: _master_widths_cm(n),
    )


def rotation_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    return _docx(
        "Department rotation list",
        _subtitle(ctx),
        _rotation_sections(frame, ctx),
        lambda n: _even_widths_cm(n),
    )


def master_list_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    return _pdf(
        "Master volunteer list",
        _subtitle(ctx, f" · {len(frame)} volunteers"),
        _master_sections(frame, ctx),
        _master_widths_pt,
    )


def rotation_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    return _pdf(
        "Department rotation list", _subtitle(ctx), _rotation_sections(frame, ctx), _even_widths_pt
    )


# ---------------------------------------------------------------------------
# Word and PDF
#
# The heavy lifting lives in exports.py, which imports from this module — so
# these wrappers import it lazily rather than at the top of the file.
# ---------------------------------------------------------------------------


def master_list_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    from exports import master_docx

    return master_docx(frame, ctx)


def rotation_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    from exports import rotation_docx as build

    return build(frame, ctx)


def master_list_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    from exports import master_pdf

    return master_pdf(frame, ctx)


def rotation_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    from exports import rotation_pdf as build

    return build(frame, ctx)
