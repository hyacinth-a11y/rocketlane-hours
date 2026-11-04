"""Builds the per-project timesheet PDF with ReportLab."""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#2563eb")
RULE = colors.HexColor("#e5e7eb")
ZEBRA = colors.HexColor("#f9fafb")

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_ss["Title"], alignment=0, fontSize=18, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("h2", parent=_ss["Heading2"], fontSize=12, textColor=INK, spaceBefore=10, spaceAfter=4)
META = ParagraphStyle("meta", parent=_ss["Normal"], fontSize=9, textColor=MUTED, leading=12)
CELL = ParagraphStyle("cell", parent=_ss["Normal"], fontSize=8.5, leading=10.5, textColor=INK)
KPI_V = ParagraphStyle("kpiv", parent=_ss["Normal"], fontSize=15, leading=18, textColor=INK, fontName="Helvetica-Bold")
KPI_L = ParagraphStyle("kpil", parent=_ss["Normal"], fontSize=8, leading=10, textColor=MUTED)


def _fmt(h: float) -> str:
    return f"{h:,.2f}"


def _table(rows, widths, num_cols=(), header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for c in num_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for r in range(2, len(rows), 2):
        style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def _summary(df: pd.DataFrame, key: str, label: str, width: float, split: bool = True):
    g = (
        df.groupby(key)
        .agg(hours=("hours", "sum"),
             billable=("hours", lambda s: s[df.loc[s.index, "billable"]].sum()),
             entries=("hours", "size"))
        .sort_values("hours", ascending=False)
    )
    total = g["hours"].sum() or 1
    rows = [[label, "Hours"] + (["Billable"] if split else []) + ["Entries", "% of total"]]
    for name, r in g.iterrows():
        rows.append([Paragraph(str(name), CELL), _fmt(r.hours)] + ([_fmt(r.billable)] if split else [])
                    + [int(r.entries), f"{r.hours / total:.0%}"])
    rows.append(["Total", _fmt(g["hours"].sum())] + ([_fmt(g["billable"].sum())] if split else [])
                + [int(g["entries"].sum()), "100%"])
    widths = [25 * mm] + ([25 * mm] if split else []) + [22 * mm, 28 * mm]
    t = _table(rows, [width - sum(widths)] + widths, num_cols=tuple(range(1, len(widths) + 1)))
    t.setStyle(TableStyle([("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.5),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
                           ("BACKGROUND", (0, -1), (-1, -1), colors.white)]))
    return t


def _kpis(df: pd.DataFrame, width: float, split: bool = True):
    bill = df.loc[df.billable, "hours"].sum()
    cells = [(_fmt(df.hours.sum()), "Total hours")]
    if split:
        cells += [(_fmt(bill), "Billable hours"), (_fmt(df.hours.sum() - bill), "Non-billable hours")]
    cells += [(str(df.person.nunique()), "People"), (str(len(df)), "Time entries")]
    t = Table([[Paragraph(v, KPI_V) for v, _ in cells], [Paragraph(l, KPI_L) for _, l in cells]],
              colWidths=[width / len(cells)] * len(cells))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, RULE),
                           ("LINEAFTER", (0, 0), (-2, -1), 0.5, RULE),
                           ("TOPPADDING", (0, 0), (-1, 0), 8),
                           ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    return t


def build_pdf(df: pd.DataFrame, start: date, end: date, filters: dict,
              include_detail: bool = True, company: str = "", show_notes: bool = True,
              billable_split: bool = True, client: str = "") -> bytes:
    """One section per project. `df` columns: see rocketlane._normalize_entry."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Timesheet report", author=company or "Rocketlane Hours")
    width = doc.width
    generated = datetime.now().strftime("%d %b %Y %H:%M")
    period = f"{start:%d %b %Y} – {end:%d %b %Y}"
    filter_txt = " · ".join(f"{k}: {v}" for k, v in filters.items() if v) or "None"

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 9 * mm, f"{company + ' · ' if company else ''}Timesheet report · {period}")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {d.page}")
        canvas.restoreState()

    story = []
    projects = sorted(df["project"].unique()) if not df.empty else []
    if not projects:
        story += [Paragraph("Timesheet report", H1),
                  Paragraph(f"{period} · Generated {generated}", META), Spacer(1, 10),
                  Paragraph("No time entries match the selected filters.", CELL)]

    for i, proj in enumerate(projects):
        p = df[df.project == proj].sort_values(["date", "person"])
        if i:
            story.append(PageBreak())
        story += [
            Paragraph(proj, H1),
            *([Paragraph(f"<b>Client:</b> {client}", META)] if client else []),
            Paragraph(f"<b>Period:</b> {period} &nbsp;&nbsp; <b>Generated:</b> {generated}", META),
            Paragraph(f"<b>Filters:</b> {filter_txt}", META),
            Spacer(1, 8), _kpis(p, width, billable_split),
            Paragraph("Hours by person", H2), _summary(p, "person", "Person", width, billable_split),
            KeepTogether([Paragraph("Hours by task", H2), _summary(p, "task", "Task", width, billable_split)]),
        ]
        if include_detail:
            cols = ["Date", "Person", "Task", "Hours"] + (["Billable"] if billable_split else []) \
                + (["Notes"] if show_notes else [])
            rows = [cols]
            for r in p.itertuples():
                row = [r.date, Paragraph(r.person, CELL), Paragraph(r.task, CELL), _fmt(r.hours)]
                if billable_split:
                    row.append("Yes" if r.billable else "No")
                if show_notes:
                    row.append(Paragraph(r.notes or "", CELL))
                rows.append(row)
            fixed = [20 * mm, 36 * mm, 0, 15 * mm] + ([16 * mm] if billable_split else [])
            if show_notes:
                fixed[2] = 45 * mm
                widths = fixed + [width - sum(fixed)]
            else:
                fixed[2] = width - sum(fixed)
                widths = fixed
            story += [Paragraph("Time entry log", H2), _table(rows, widths, num_cols=(3,))]

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
