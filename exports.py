"""
Word and PDF versions of the master list and the rotation list.

Both formats follow the same design as the printable page: a dark masthead,
department sections with a hairline rule, a quiet numbered table, and a footer
carrying the page number. Everything returns bytes, ready for a download
button.

Needs python-docx and reportlab (both in requirements.txt).
"""

from __future__ import annotations

import io

import pandas as pd

from documents import rotation_frame

INK = "16202B"
SOFT = "5B6B7C"
LINE = "DCD8CF"
PAPER = "F7F5F0"

MASTER_HEADS = ["", "Name", "Congregation", "Gender", "Privilege", "Shift", "Status"]


def in_shift_order(rows: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    """Alphabetical order would put Afternoon before Morning."""
    order = {name: i for i, name in enumerate(ctx["shifts"])}
    return rows.assign(_seq=rows["Shift"].map(lambda s: order.get(s, len(order)))).sort_values(
        ["_seq", "Name"]
    ).drop(columns="_seq")


def _oversight(ctx: dict, dept: str) -> str:
    d = ctx["departments"].get(dept, {})
    bits = []
    if d.get("overseer"):
        bits.append(f"Overseer: {d['overseer']}")
    if d.get("assistant"):
        bits.append(f"Assistant: {d['assistant']}")
    if d.get("keymen"):
        bits.append(f"Keymen: {d['keymen']}")
    return "   ·   ".join(bits) or "No oversight recorded"


def _subtitle(ctx: dict, extra: str = "") -> str:
    venue = f"   ·   {ctx['venue']}" if ctx.get("venue") else ""
    return (
        f"{ctx.get('part_label', ctx['part'])}, {ctx['year']}   ·   "
        f"{ctx['event_date']:%A, %d %B %Y}{venue}{extra}   ·   "
        f"printed {ctx['today']:%d %B %Y}"
    )


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


def _shade(cell, colour: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")  # never "solid" — it renders black
    shd.set(qn("w:fill"), colour)
    cell._tc.get_or_add_tcPr().append(shd)


def _no_borders(table) -> None:
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "left", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "none")
        borders.append(element)
    table._tbl.tblPr.append(borders)


def _rule(paragraph, colour: str = INK, size: int = 12) -> None:
    """A bottom border on a paragraph, rather than a one-row table."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), colour)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _page_numbers(section) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, RGBColor

    para = section.footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = para.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        element = OxmlElement("w:fldChar") if kind else OxmlElement("w:instrText")
        if kind:
            element.set(qn("w:fldCharType"), kind)
        else:
            element.set(qn("xml:space"), "preserve")
            element.text = text
        run._r.append(element)
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(SOFT)


def _masthead(doc, title: str, ctx: dict, extra: str = "") -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    band = doc.add_table(rows=1, cols=1)
    _no_borders(band)
    cell = band.rows[0].cells[0]
    _shade(cell, INK)
    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = cell.paragraphs[0].add_run(title)
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor.from_string("FFFFFF")

    line = cell.add_paragraph()
    sub = line.add_run(_subtitle(ctx, extra))
    sub.font.size = Pt(8.5)
    sub.font.color.rgb = RGBColor.from_string("D8DCE1")

    doc.add_paragraph()


def _section_heading(doc, dept: str, ctx: dict) -> None:
    from docx.shared import Pt, RGBColor

    head = doc.add_paragraph()
    head.paragraph_format.space_before = Pt(14)
    head.paragraph_format.space_after = Pt(2)
    head.paragraph_format.keep_with_next = True
    run = head.add_run(dept)
    run.bold = True
    run.font.size = Pt(12.5)
    run.font.color.rgb = RGBColor.from_string(INK)
    _rule(head)

    lead = doc.add_paragraph()
    lead.paragraph_format.space_after = Pt(6)
    lead.paragraph_format.keep_with_next = True
    note = lead.add_run(_oversight(ctx, dept))
    note.font.size = Pt(8.5)
    note.font.color.rgb = RGBColor.from_string(SOFT)


def _table_borders(table) -> None:
    """Hairlines under each row, no boxes — the grid style is far too heavy."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "insideH"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:color"), LINE)
        borders.append(element)
    for edge in ("left", "right", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "none")
        borders.append(element)
    table._tbl.tblPr.append(borders)


def _fixed_widths(table, widths: list) -> None:
    """Word ignores cell widths unless the table layout is fixed."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)

    total = OxmlElement("w:tblW")
    total.set(qn("w:w"), str(sum(w.twips for w in widths)))
    total.set(qn("w:type"), "dxa")
    table._tbl.tblPr.append(total)

    # both the grid columns and every cell, or Word re-distributes them
    for column, width in zip(table.columns, widths):
        column.width = width
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = width


def _table(doc, headings: list, rows: list, widths: list) -> None:
    from docx.shared import Pt, RGBColor

    table = doc.add_table(rows=1, cols=len(headings))
    table.autofit = False
    _table_borders(table)

    for i, (cell, text, width) in enumerate(zip(table.rows[0].cells, headings, widths)):
        cell.width = width
        _shade(cell, PAPER)
        run = cell.paragraphs[0].add_run(str(text))
        run.bold = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor.from_string(INK)

    for values in rows:
        cells = table.add_row().cells
        for i, (cell, value, width) in enumerate(zip(cells, values, widths)):
            cell.width = width
            run = cell.paragraphs[0].add_run(str(value))
            run.font.size = Pt(9)
            if i == 0:
                run.font.color.rgb = RGBColor.from_string(SOFT)

    _fixed_widths(table, widths)


def _document(ctx: dict):
    from docx import Document
    from docx.shared import Pt, Cm

    doc = Document()
    section = doc.sections[0]
    for side in ("top", "bottom", "left", "right"):
        setattr(section, f"{side}_margin", Cm(1.6))
    style = doc.styles["Normal"]
    style.font.name = "Helvetica"
    style.font.size = Pt(9.5)
    style.paragraph_format.space_after = Pt(0)     # Word's 8pt default bloats every row
    style.paragraph_format.line_spacing = 1.0
    _page_numbers(section)
    return doc


def _bytes(doc) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def master_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    from docx.shared import Cm, Pt, RGBColor

    doc = _document(ctx)
    _masthead(doc, "Master volunteer list", ctx, f"   ·   {len(frame)} volunteers")
    widths = [Cm(0.8), Cm(4.8), Cm(4.0), Cm(1.8), Cm(2.1), Cm(2.6), Cm(2.3)]

    for dept in ctx["dept_order"]:
        rows = in_shift_order(frame[frame["Department"] == dept], ctx)
        if rows.empty:
            continue
        _section_heading(doc, dept, ctx)
        _table(
            doc,
            MASTER_HEADS,
            [
                [
                    i,
                    r["Name"],
                    r["Congregation"],
                    r["Gender"],
                    r["Privilege"],
                    r["Shift"],
                    r["Status"],
                ]
                for i, (_, r) in enumerate(rows.iterrows(), start=1)
            ],
            widths,
        )
        tail = doc.add_paragraph()
        tail.paragraph_format.space_before = Pt(3)
        count = tail.add_run(f"{len(rows)} volunteers")
        count.font.size = Pt(8.5)
        count.font.color.rgb = RGBColor.from_string(SOFT)

    return _bytes(doc)


def rotation_docx(frame: pd.DataFrame, ctx: dict) -> bytes:
    from docx.shared import Cm, Pt, RGBColor

    doc = _document(ctx)
    _masthead(doc, "Department rotation list", ctx)
    shifts = list(ctx["shifts"])
    span = 17.6 - 0.8
    widths = [Cm(0.8)] + [Cm(span / max(len(shifts), 1))] * len(shifts)

    for dept in ctx["dept_order"]:
        table = rotation_frame(frame, ctx, dept)
        if table.empty:
            continue
        _section_heading(doc, dept, ctx)
        heads = [""] + [f"{n}  {ctx['hours'].get(n, '')}".strip() for n in table.columns]
        _table(
            doc,
            heads,
            [[i + 1, *row] for i, row in enumerate(table.itertuples(index=False))],
            widths,
        )
        tail = doc.add_paragraph()
        tail.paragraph_format.space_before = Pt(3)
        counts = tail.add_run(
            "   ·   ".join(f"{c}: {(table[c] != '').sum()}" for c in table.columns)
        )
        counts.font.size = Pt(8.5)
        counts.font.color.rgb = RGBColor.from_string(SOFT)

    return _bytes(doc)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle

    return {
        "dept": ParagraphStyle(
            "dept",
            fontName="Helvetica-Bold",
            fontSize=12.5,
            textColor=HexColor(f"#{INK}"),
            spaceBefore=14,
            spaceAfter=1,
        ),
        "lead": ParagraphStyle(
            "lead",
            fontName="Helvetica",
            fontSize=8.5,
            textColor=HexColor(f"#{SOFT}"),
            spaceAfter=5,
        ),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=11),
    }


def _pdf_chrome(title: str, ctx: dict, extra: str = ""):
    """Masthead on the first page, page number on every page."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    def draw(canvas, doc):
        canvas.saveState()
        width, height = A4
        if canvas.getPageNumber() == 1:
            canvas.setFillColor(HexColor(f"#{INK}"))
            canvas.rect(0, height - 34 * mm, width, 34 * mm, stroke=0, fill=1)
            canvas.setFillColor(white)
            canvas.setFont("Helvetica-Bold", 20)
            canvas.drawString(16 * mm, height - 19 * mm, title)
            canvas.setFillColor(HexColor("#D8DCE1"))
            canvas.setFont("Helvetica", 8.5)
            canvas.drawString(16 * mm, height - 26 * mm, _subtitle(ctx, extra))

        canvas.setFillColor(HexColor(f"#{SOFT}"))
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(width - 16 * mm, 10 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    return draw


def _pdf_table(headings: list, rows: list, widths: list):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle

    table = Table([headings] + rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HexColor(f"#{PAPER}")),
                ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(f"#{INK}")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("TEXTCOLOR", (0, 1), (0, -1), HexColor(f"#{SOFT}")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor(f"#{LINE}")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _pdf_build(story, title: str, ctx: dict, extra: str = "") -> bytes:
    """First page leaves room for the masthead; later pages start at the top."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageTemplate

    buffer = io.BytesIO()
    width, height = A4
    side, foot = 16 * mm, 16 * mm
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        title=f"{title} — {ctx.get('part_label', ctx['part'])} {ctx['year']}",
        author="Assembly Portal",
    )
    chrome = _pdf_chrome(title, ctx, extra)
    doc.addPageTemplates(
        [
            PageTemplate(
                id="first",
                frames=[
                    Frame(side, foot, width - 2 * side, height - 42 * mm - foot, id="f1")
                ],
                onPage=chrome,
            ),
            PageTemplate(
                id="later",
                frames=[
                    Frame(side, foot, width - 2 * side, height - 18 * mm - foot, id="f2")
                ],
                onPage=chrome,
            ),
        ]
    )
    doc.build([NextPageTemplate("later")] + story)
    return buffer.getvalue()


def master_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph

    styles = _pdf_styles()
    widths = [8 * mm, 40 * mm, 34 * mm, 16 * mm, 20 * mm, 24 * mm, 20 * mm]
    story = []

    for dept in ctx["dept_order"]:
        rows = in_shift_order(frame[frame["Department"] == dept], ctx)
        if rows.empty:
            continue
        block = [
            Paragraph(dept, styles["dept"]),
            Paragraph(_oversight(ctx, dept), styles["lead"]),
            _pdf_table(
                MASTER_HEADS,
                [
                    [i, r["Name"], r["Congregation"], r["Gender"], r["Privilege"], r["Shift"], r["Status"]]
                    for i, (_, r) in enumerate(rows.iterrows(), start=1)
                ],
                widths,
            ),
            Paragraph(f"{len(rows)} volunteers", styles["lead"]),
        ]
        story.append(KeepTogether(block) if len(rows) <= 12 else block[0])
        if len(rows) > 12:
            story.extend(block[1:])

    if not story:
        story = [Paragraph("No volunteers recorded.", styles["lead"])]
    return _pdf_build(story, "Master volunteer list", ctx, f"   ·   {len(frame)} volunteers")


def rotation_pdf(frame: pd.DataFrame, ctx: dict) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph

    styles = _pdf_styles()
    shifts = list(ctx["shifts"])
    span = 162 - 8
    widths = [8 * mm] + [(span / max(len(shifts), 1)) * mm] * len(shifts)
    story = []

    for dept in ctx["dept_order"]:
        table = rotation_frame(frame, ctx, dept)
        if table.empty:
            continue
        heads = [""] + [f"{n}  {ctx['hours'].get(n, '')}".strip() for n in table.columns]
        block = [
            Paragraph(dept, styles["dept"]),
            Paragraph(_oversight(ctx, dept), styles["lead"]),
            _pdf_table(
                heads,
                [[i + 1, *row] for i, row in enumerate(table.itertuples(index=False))],
                widths,
            ),
            Paragraph(
                "   ·   ".join(f"{c}: {(table[c] != '').sum()}" for c in table.columns),
                styles["lead"],
            ),
        ]
        story.append(KeepTogether(block) if len(table) <= 12 else block[0])
        if len(table) > 12:
            story.extend(block[1:])

    if not story:
        story = [Paragraph("No volunteers recorded.", styles["lead"])]
    return _pdf_build(story, "Department rotation list", ctx)
