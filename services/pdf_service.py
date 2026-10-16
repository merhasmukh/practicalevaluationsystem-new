"""PDF generation service for Practical Evaluation System.

Generates institutional practical-list PDFs in the Gujarat Vidyapith format:
  - 100% Monochrome / Black & White design: optimized for clean, toner-efficient
    physical printouts and university submissions (zero colored backgrounds/tints).
  - Enclosed 4-row institutional header box (Institution, Department, Course, Year,
    Teacher, Subject, Enrollment No.) matching the official university format.
  - Centered Unit heading above the practical listing.
  - Tabular practical listing with 7 columns:
      Practical No. | Lab Activity | Date Assigned | Date Submission | Sign | Remarks | Grade
  - Clean hierarchical activity layout (Level 1 ●, Level 2 ○, Level 3 ■, code blocks).
  - Bottom-aligned dates and top-aligned activities/remarks matching the sample layout.
  - Final grading scheme reference table (Grade | Marks) centered on page.
  - Centered page numbers (1, 2, 3...) at the bottom of every page.

Usage::

    from services.pdf_service import generate_practical_list_pdf
    from io import BytesIO

    # Personalized student PDF
    pdf_bytes: BytesIO = generate_practical_list_pdf(db, subject_id=1, student_id=3)

    # Blank master sheet (for physical distribution)
    blank_bytes: BytesIO = generate_practical_list_pdf(db, subject_id=1)
"""

from __future__ import annotations

import html
import os
import re
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sqlalchemy.orm import Session


# ── Monochrome Palette (Zero colors for printouts) ────────────────────────────
# Clean black and white ensures maximum contrast and toner efficiency on paper.
COLOR_TEXT = colors.black
COLOR_LINE = colors.black
COLOR_BG   = colors.white


# ── Styles ────────────────────────────────────────────────────────────────────
_styles = getSampleStyleSheet()

INST_NAME_STYLE = ParagraphStyle(
    "InstName",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=16,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

DEPT_NAME_STYLE = ParagraphStyle(
    "DeptName",
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

HDR_FIELD_L = ParagraphStyle(
    "HdrFieldL",
    fontName="Helvetica",
    fontSize=8.5,
    leading=11,
    alignment=TA_LEFT,
    textColor=COLOR_TEXT,
)

HDR_FIELD_C = ParagraphStyle(
    "HdrFieldC",
    fontName="Helvetica",
    fontSize=8.5,
    leading=11,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

HDR_FIELD_R = ParagraphStyle(
    "HdrFieldR",
    fontName="Helvetica",
    fontSize=8.5,
    leading=11,
    alignment=TA_RIGHT,
    textColor=COLOR_TEXT,
)

UNIT_TITLE_STYLE = ParagraphStyle(
    "UnitTitle",
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    alignment=TA_CENTER,
    spaceBefore=4,
    spaceAfter=4,
    textColor=COLOR_TEXT,
)

TBL_HDR_STYLE = ParagraphStyle(
    "TblHdr",
    fontName="Helvetica-Bold",
    fontSize=8,
    leading=10,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

ACTIVITY_TITLE = ParagraphStyle(
    "ActTitle",
    fontName="Helvetica-Bold",
    fontSize=8,
    leading=10.5,
    spaceAfter=2,
    textColor=COLOR_TEXT,
)

ACTIVITY_BODY = ParagraphStyle(
    "ActBody",
    fontName="Helvetica",
    fontSize=7.5,
    leading=9.5,
    spaceAfter=1,
    textColor=COLOR_TEXT,
)

ACTIVITY_BULLET_1 = ParagraphStyle(
    "ActB1",
    parent=ACTIVITY_BODY,
    leftIndent=10,
    firstLineIndent=-6,
    spaceAfter=1,
)

ACTIVITY_BULLET_2 = ParagraphStyle(
    "ActB2",
    parent=ACTIVITY_BODY,
    leftIndent=18,
    firstLineIndent=-6,
    spaceAfter=1,
)

ACTIVITY_BULLET_3 = ParagraphStyle(
    "ActB3",
    parent=ACTIVITY_BODY,
    leftIndent=26,
    firstLineIndent=-6,
    spaceAfter=1,
)

ACTIVITY_CODE = ParagraphStyle(
    "ActCode",
    parent=ACTIVITY_BODY,
    fontName="Courier",
    fontSize=7,
    leading=8.5,
    leftIndent=8,
    spaceAfter=0.5,
)

CELL_CENTER = ParagraphStyle(
    "CellCenter",
    fontName="Helvetica",
    fontSize=7.5,
    leading=9.5,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

CELL_CENTER_BOLD = ParagraphStyle(
    "CellCenterBold",
    fontName="Helvetica-Bold",
    fontSize=8,
    leading=10,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

CELL_LEFT = ParagraphStyle(
    "CellLeft",
    fontName="Helvetica",
    fontSize=7.5,
    leading=9.5,
    alignment=TA_LEFT,
    textColor=COLOR_TEXT,
)

GRADE_HDR_STYLE = ParagraphStyle(
    "GradeHdr",
    fontName="Helvetica-Bold",
    fontSize=8.5,
    leading=11,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

GRADE_CELL_STYLE = ParagraphStyle(
    "GradeCell",
    fontName="Helvetica",
    fontSize=8,
    leading=10.5,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

SIGN_TEXT_STYLE = ParagraphStyle(
    "SignText",
    fontName="Helvetica-Bold",
    fontSize=7,
    leading=9,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)

SIGN_DATE_STYLE = ParagraphStyle(
    "SignDate",
    fontName="Helvetica",
    fontSize=6.5,
    leading=8,
    alignment=TA_CENTER,
    textColor=COLOR_TEXT,
)


def _format_signer_name(full_name: str) -> str:
    """Format full name into a clean, readable faculty signer name.

    Removes academic titles ('Shri', 'Dr.', 'Prof.'), system tags ('Admin', '(...)'),
    and ensures it never looks like a grade letter or technical identifier.
    """
    if not full_name:
        return "Faculty"
    # Remove bracketed technical parts like '(hasmukh.cs)'
    cleaned_str = re.sub(r"\(.*?\)", "", full_name).strip()
    words = cleaned_str.split()
    ignored_words = {
        "shri", "smt", "dr", "dr.", "prof", "prof.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
        "admin", "administrator", "faculty", "user",
    }
    meaningful = [w for w in words if w.lower() not in ignored_words]
    if not meaningful:
        match = re.search(r"\((.*?)\)", full_name)
        if match:
            return match.group(1).split(".")[0].capitalize()
        return "Faculty"
    if len(meaningful) == 1:
        return meaningful[0]
    return f"{meaningful[0]} {meaningful[-1]}"


# ── Numbered Canvas (centered page numbers in black) ──────────────────────────

class _NumberedCanvas(Canvas):
    """Canvas that writes a clean, centered page number at the bottom of every page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number()
            Canvas.showPage(self)
        Canvas.save(self)

    def _draw_page_number(self) -> None:
        self.setFont("Helvetica", 9)
        self.setFillColor(COLOR_TEXT)
        self.drawCentredString(A4[0] / 2, 18, str(self._pageNumber))


# ── Safe XML text formatting helper ──────────────────────────────────────────

def _safe_xml(text: str) -> str:
    """Escape &, <, > safely while allowing basic bold/italic tags."""
    if not text:
        return ""
    # If the text already has allowed tags, replace entities carefully
    s = html.escape(text, quote=False)
    # Restore allowed ReportLab formatting tags
    s = s.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    s = s.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
    s = s.replace("&lt;br/&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
    return s


BOX_DRAWING_MAP = {
    "├── ": "+-- ",
    "└── ": "\\-- ",
    "├──": "+--",
    "└──": "\\--",
    "│   ": "|   ",
    "│  ": "|  ",
    "│ ": "| ",
    "│": "|",
    "─": "-",
    "┌": "+",
    "└": "\\",
    "├": "+",
    "┬": "+",
    "┴": "+",
    "┼": "+",
}

ACTION_VERBS = {
    "create", "generate", "read", "display", "validate", "write",
    "accept", "identify", "check", "update", "implement", "define",
    "show", "calculate", "print", "import", "test", "configure",
    "setup", "install", "build", "develop", "load", "parse", "convert",
    "add", "remove", "delete", "modify", "extract", "save", "execute",
    "run", "prepare", "design", "verify", "demonstrate", "apply"
}

INTRO_PROMPTS = (
    "the application should", "the program should", "the project should",
    "perform the following", "follow the structure", "following tasks",
    "instructions:", "requirements:", "steps:", "procedure:",
)


def _clean_box_chars(text: str) -> str:
    for k, v in BOX_DRAWING_MAP.items():
        text = text.replace(k, v)
    return text


def _build_activity_cell(practical) -> list:
    """Return a list of ReportLab flowables for the 'Lab Activity' column.

    Properly formats:
      - Title line (bold)
      - Introductory description & prompt lines
      - Multi-level bullets (Level 1, Level 2, or numbered) using standard HTML &bull;
      - CLI / tree / code blocks in Courier monospace with box-drawing characters converted to ASCII
      - Optional learning outcome in italics
    """
    parts: list = []

    title = (practical.title or "").strip()
    description = (practical.description or "").strip()

    title_rendered = False
    if title and not description.startswith(title):
        parts.append(Paragraph(f"<b>{_safe_xml(title)}</b>", ACTIVITY_TITLE))
        title_rendered = True

    if description:
        raw_lines = description.splitlines()
        lines: list[str] = []
        for l in raw_lines:
            # If a line is exceptionally long (> 350 chars) and not a tree structure, split by sentences
            if len(l) > 350 and not any(c in l for c in ("├", "└", "│", "+-", "|-")):
                sentences = re.split(r"(?<=\.)\s+", l)
                lines.extend(s for s in sentences if s)
            else:
                lines.append(l)

        in_tree_block = False
        in_sub_items = False

        for line_idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped:
                in_sub_items = False
                parts.append(Spacer(1, 2))
                continue

            # Detect leading indentation
            leading_spaces = len(raw_line) - len(raw_line.lstrip(" "))
            if "\t" in raw_line[:leading_spaces]:
                leading_spaces += raw_line[:leading_spaces].count("\t") * 3

            # Clean any unicode box characters to pure ASCII
            cleaned_line = _clean_box_chars(raw_line)
            cleaned_stripped = cleaned_line.strip()

            # 1. Tree / code snippet detection
            has_box_chars = any(c in raw_line for c in ("├", "└", "│", "─", "┌", "┬", "┴", "┼"))
            is_tree_branch = cleaned_stripped.startswith(("+--", "|--", "\\--", "| ", "|", "+-", "\\-"))
            is_cli = cleaned_stripped.startswith(("$ ", "> ", "# ", "python ", "npm ", "pip ", "git ", "node "))
            is_tree_root = (
                stripped.endswith("/")
                and len(stripped.split()) == 1
                and not any(stripped.lower().startswith(v) for v in ACTION_VERBS)
            )

            if has_box_chars or is_tree_branch or is_cli or is_tree_root or (
                in_tree_block and (stripped.endswith("/") or ("." in stripped and " " not in stripped))
            ):
                in_tree_block = True
                in_sub_items = False
                parts.append(Paragraph(_safe_xml(cleaned_line.rstrip()), ACTIVITY_CODE))
                continue

            in_tree_block = False

            # If title was not rendered above, make line 0 the bold activity title
            if line_idx == 0 and not title_rendered:
                parts.append(Paragraph(f"<b>{_safe_xml(stripped)}</b>", ACTIVITY_TITLE))
                title_rendered = True
                continue

            # 2. Explicit bullet or numbered markers
            bullet_match = re.match(r"^([•●○■▪▫–—⁃\-\*\+]|&bull;)\s*(.*)$", stripped)
            num_match = re.match(r"^(\d+|[a-zA-Z]|[ivxIVX]+)[\.\)]\s*(.*)$", stripped)

            if bullet_match:
                content = bullet_match.group(2).strip()
                if leading_spaces >= 4 or in_sub_items:
                    parts.append(Paragraph(f"&bull;&nbsp;&nbsp;{_safe_xml(content)}", ACTIVITY_BULLET_2))
                else:
                    parts.append(Paragraph(f"&bull;&nbsp;&nbsp;{_safe_xml(content)}", ACTIVITY_BULLET_1))
                in_sub_items = content.endswith(":")
                continue

            if num_match:
                prefix = num_match.group(1)
                content = num_match.group(2).strip()
                if leading_spaces >= 4 or in_sub_items:
                    parts.append(Paragraph(f"{_safe_xml(prefix)}.&nbsp;&nbsp;{_safe_xml(content)}", ACTIVITY_BULLET_2))
                else:
                    parts.append(Paragraph(f"{_safe_xml(prefix)}.&nbsp;&nbsp;{_safe_xml(content)}", ACTIVITY_BULLET_1))
                in_sub_items = content.endswith(":")
                continue

            # 3. Indented lines (sub-items)
            if leading_spaces >= 2:
                parts.append(Paragraph(f"&bull;&nbsp;&nbsp;{_safe_xml(stripped)}", ACTIVITY_BULLET_2))
                continue

            # 4. Intro prompts (e.g. 'The application should perform the following tasks:')
            low = stripped.lower()
            if any(p in low for p in INTRO_PROMPTS):
                in_sub_items = False
                parts.append(Paragraph(_safe_xml(stripped), ACTIVITY_BODY))
                continue

            # 5. First word check & sub-item continuation
            first_word = stripped.split()[0].rstrip(":,.;").lower() if stripped.split() else ""
            ends_with_colon = stripped.endswith(":")

            if in_sub_items and first_word not in ACTION_VERBS and not ends_with_colon:
                # Sub-item e.g. 'requests', 'pandas', 'API Key'
                parts.append(Paragraph(f"&bull;&nbsp;&nbsp;{_safe_xml(stripped)}", ACTIVITY_BULLET_2))
                continue

            if line_idx > 0 and (first_word in ACTION_VERBS or ends_with_colon):
                # Task action requirement
                parts.append(Paragraph(f"&bull;&nbsp;&nbsp;{_safe_xml(stripped)}", ACTIVITY_BULLET_1))
                in_sub_items = ends_with_colon
                continue

            # Default normal descriptive sentence
            parts.append(Paragraph(_safe_xml(stripped), ACTIVITY_BODY))

    # Learning outcome appended in italics if present
    outcome = (practical.learning_outcome or "").strip()
    if outcome:
        outcome_style = ParagraphStyle(
            "Outcome",
            parent=ACTIVITY_BODY,
            fontName="Helvetica-Oblique",
            leftIndent=0,
            spaceBefore=2,
        )
        parts.append(Paragraph(f"<i>Outcome: {_safe_xml(outcome)}</i>", outcome_style))

    return parts if parts else [Paragraph("—", ACTIVITY_BODY)]


# ── Helper: build 4-row institutional header box ─────────────────────────────

def _build_header_box(cfg: dict, enrollment_no: str, inner_width: float) -> Table:
    """Return the enclosed 4-row institutional header box matching the sample format."""
    inst_name = cfg.get("institution_name", "Gujarat Vidyapith") or "Gujarat Vidyapith"
    dept_name = cfg.get("department_name", "Department of Computer Science") or "Department of Computer Science"
    course_name = cfg.get("course_name", "") or ""
    academic_year = cfg.get("academic_year", "") or "2026-27"
    subject_teacher = cfg.get("subject_teacher", "") or ""
    subject_title = cfg.get("subject_title", "") or ""

    enrollment_val = f" {enrollment_no}" if enrollment_no else "________________"

    col_widths = [185, 135, inner_width - 185 - 135]

    row0 = [Paragraph(f"<b>{_safe_xml(inst_name)}</b>", INST_NAME_STYLE), "", ""]
    row1 = [Paragraph(f"<b>{_safe_xml(dept_name)}</b>", DEPT_NAME_STYLE), "", ""]
    row2 = [
        Paragraph(f"<b>Course :</b> {_safe_xml(course_name)}", HDR_FIELD_L),
        Paragraph(f"<b>Year :</b> {_safe_xml(academic_year)}", HDR_FIELD_C),
        Paragraph(f"<b>Subject Teacher :</b> {_safe_xml(subject_teacher)}", HDR_FIELD_R),
    ]
    row3 = [
        Paragraph(f"<b>Sub :</b> {_safe_xml(subject_title)}", HDR_FIELD_L),
        "",
        Paragraph(f"<b>Student Enrollment No.:</b>{_safe_xml(enrollment_val)}", HDR_FIELD_R),
    ]

    table = Table([row0, row1, row2, row3], colWidths=col_widths, repeatRows=0)
    table.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 0.75, COLOR_LINE),
        ('SPAN',          (0, 0), (2, 0)),
        ('SPAN',          (0, 1), (2, 1)),
        ('SPAN',          (0, 3), (1, 3)),
        ('LINEBELOW',     (0, 0), (2, 0), 0.5, COLOR_LINE),
        ('LINEBELOW',     (0, 1), (2, 1), 0.5, COLOR_LINE),
        ('LINEBELOW',     (0, 2), (2, 2), 0.5, COLOR_LINE),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ('BACKGROUND',    (0, 0), (-1, -1), COLOR_BG),
    ]))
    return table


# ── Helper: grading scheme reference table ────────────────────────────────────

def _build_grade_scheme_table() -> Table:
    """Return the centered Grade / Marks scheme table matching the sample format."""
    grade_rows = [
        [Paragraph("Grade", GRADE_HDR_STYLE), Paragraph("Marks", GRADE_HDR_STYLE)],
        [Paragraph("A", GRADE_CELL_STYLE), Paragraph("18 - 20", GRADE_CELL_STYLE)],
        [Paragraph("B", GRADE_CELL_STYLE), Paragraph("14 - 17", GRADE_CELL_STYLE)],
        [Paragraph("C", GRADE_CELL_STYLE), Paragraph("11 - 13", GRADE_CELL_STYLE)],
        [Paragraph("D", GRADE_CELL_STYLE), Paragraph("8 - 10", GRADE_CELL_STYLE)],
        [Paragraph("E", GRADE_CELL_STYLE), Paragraph("5 - 7", GRADE_CELL_STYLE)],
        [Paragraph("F", GRADE_CELL_STYLE), Paragraph("1 - 4", GRADE_CELL_STYLE)],
        [Paragraph("S", GRADE_CELL_STYLE), Paragraph("Absent", GRADE_CELL_STYLE)],
    ]
    style = TableStyle([
        ('BOX',           (0, 0), (-1, -1), 0.75, COLOR_LINE),
        ('INNERGRID',     (0, 0), (-1, -1), 0.5,  COLOR_LINE),
        ('BACKGROUND',    (0, 0), (-1, -1), COLOR_BG),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ])
    t = Table(grade_rows, colWidths=[70, 95])
    t.setStyle(style)
    return t


def _chunk_activity_flowables(
    flowables: list,
    max_height: float = 420.0,
    col_width: float = 228.0,
) -> list[list]:
    """Split a practical's activity flowables into chunks if they exceed max_height.

    ReportLab cannot split a single table row across pages. If a single row's
    height exceeds the frame height (~773 pt), doc.build() raises a LayoutError.
    Chunking the flowables across multiple table rows ensures seamless page splits
    even for practicals with dozens of detailed requirements.
    """
    if not flowables:
        return [[]]

    chunks: list[list] = []
    current_chunk: list = []
    current_height = 0.0

    for f in flowables:
        try:
            _, h = f.wrap(col_width, 99999)
        except Exception:
            h = 10.0

        if current_chunk and (current_height + h > max_height):
            chunks.append(current_chunk)
            current_chunk = [f]
            current_height = h
        else:
            current_chunk.append(f)
            current_height += h

    if current_chunk:
        chunks.append(current_chunk)

    return chunks if chunks else [flowables]


# ── Helper: practicals main table ────────────────────────────────────────────

def _build_practicals_table(
    practicals: list,
    assignments_by_practical: dict,
    evaluations_by_practical: dict,
    header_cfg: Any,
    page_width: float,
) -> tuple[Table, str]:
    """Construct the main practicals table with 7 columns.

    Returns:
        (Table, primary_unit_title)
    """
    # Column widths summing exactly to page_width (538.5 pt)
    # Practical No. | Lab Activity | Date Assigned | Date Submission | Sign | Remarks | Grade
    col_widths = [46, 228, 50, 56, 52, 70, 36.5]

    header_row = [
        Paragraph("Practical<br/>No.", TBL_HDR_STYLE),
        Paragraph("Lab Activity", TBL_HDR_STYLE),
        Paragraph("Date<br/>Assigned", TBL_HDR_STYLE),
        Paragraph("Date<br/>Submission", TBL_HDR_STYLE),
        Paragraph("Sign", TBL_HDR_STYLE),
        Paragraph("Remarks", TBL_HDR_STYLE),
        Paragraph("Grade", TBL_HDR_STYLE),
    ]

    def _fmt_date(d) -> str:
        if d is None:
            return ""
        try:
            return d.strftime("%-d/%-m/%Y")
        except Exception:
            return str(d)

    # Detect unit title(s)
    unit_names = [p.unit_name.strip() for p in practicals if p.unit_name and p.unit_name.strip()]
    primary_unit = unit_names[0] if unit_names else ""
    multiple_units = len(set(unit_names)) > 1

    all_rows: list = [header_row]
    last_unit: str | None = None

    for p in sorted(practicals, key=lambda x: x.practical_number):
        current_unit = (p.unit_name or "").strip()

        # If practicals span multiple distinct units, insert a clean separator row
        if multiple_units and current_unit and current_unit != last_unit and last_unit is not None:
            sep_row = [
                Paragraph(f"<b>{_safe_xml(current_unit)}</b>", TBL_HDR_STYLE),
                "", "", "", "", "", "",
            ]
            all_rows.append(sep_row)
            last_unit = current_unit

        if last_unit is None and current_unit:
            last_unit = current_unit

        pcode = (p.practical_code or "").strip() or f"P{p.practical_number}"

        # Assigned and submission dates
        assigned_dt = _fmt_date(p.assigned_date)
        assignment = assignments_by_practical.get(p.id)

        if assignment:
            if not assigned_dt and assignment.assigned_at:
                assigned_dt = _fmt_date(assignment.assigned_at.date())
            if assignment.submission and assignment.submission.submitted_at:
                submission_dt = _fmt_date(assignment.submission.submitted_at.date())
            else:
                submission_dt = _fmt_date(p.submission_date)
        else:
            submission_dt = _fmt_date(p.submission_date)

        # Grade, remarks, and signature
        evaluation = evaluations_by_practical.get(p.id)
        if evaluation and evaluation.published:
            grade_val = evaluation.grade or ""
            remarks_val = evaluation.remarks or ""
            eval_dt = evaluation.evaluated_at.date() if evaluation.evaluated_at else None
            grading_date = _fmt_date(eval_dt) or _fmt_date(p.submission_date)

            # Determine signer name:
            # Prioritize official subject teacher configured in the subject header
            evaluator_user = getattr(evaluation, "evaluator", None)
            if header_cfg and getattr(header_cfg, "subject_teacher", None) and header_cfg.subject_teacher.strip():
                signer_full = header_cfg.subject_teacher.strip()
            elif evaluator_user and evaluator_user.full_name and evaluator_user.full_name.strip():
                signer_full = evaluator_user.full_name.strip()
            else:
                signer_full = "Faculty"
            signer_display = _format_signer_name(signer_full)

            # Check for signature image (subject-level or evaluator-level)
            sig_path = None
            if header_cfg and header_cfg.signature_image_path and os.path.isfile(header_cfg.signature_image_path):
                sig_path = header_cfg.signature_image_path
            elif evaluator_user and getattr(evaluator_user, "signature_image_path", None) and os.path.isfile(evaluator_user.signature_image_path):
                sig_path = evaluator_user.signature_image_path

            if sig_path:
                try:
                    img_flowable = Image(sig_path, width=38, height=18)
                    sign_cell = [
                        img_flowable,
                        Spacer(1, 1),
                        Paragraph(f"<font size=6.5>{_safe_xml(grading_date)}</font>", SIGN_DATE_STYLE),
                    ]
                except Exception:
                    sign_cell = [
                        Paragraph(f"<b>{_safe_xml(signer_display)}</b>", SIGN_TEXT_STYLE),
                        Paragraph(f"{_safe_xml(grading_date)}", SIGN_DATE_STYLE),
                    ]
            else:
                sign_cell = [
                    Paragraph(f"<b>{_safe_xml(signer_display)}</b>", SIGN_TEXT_STYLE),
                    Paragraph(f"{_safe_xml(grading_date)}", SIGN_DATE_STYLE),
                ]
        else:
            grade_val = ""
            remarks_val = ""
            sign_cell = Paragraph("", CELL_CENTER)

        pcode_para = Paragraph(f"<b>{_safe_xml(pcode)}</b>", CELL_CENTER_BOLD)
        assigned_para = Paragraph(_safe_xml(assigned_dt), CELL_CENTER)
        submit_para = Paragraph(_safe_xml(submission_dt), CELL_CENTER)
        remarks_para = Paragraph(_safe_xml(remarks_val), CELL_CENTER)
        grade_para = Paragraph(f"<b>{_safe_xml(grade_val)}</b>" if grade_val else "", CELL_CENTER)

        activity_flowables = _build_activity_cell(p)
        chunks = _chunk_activity_flowables(activity_flowables, max_height=420.0, col_width=col_widths[1])

        num_chunks = len(chunks)
        for c_idx, chunk in enumerate(chunks):
            is_first = (c_idx == 0)
            is_last = (c_idx == num_chunks - 1)

            if is_first:
                col_pcode = pcode_para
                col_assigned = assigned_para
            else:
                col_pcode = Paragraph(f"<font size=6.5><b>{_safe_xml(pcode)}</b><br/>(cont.)</font>", CELL_CENTER)
                col_assigned = Paragraph("", CELL_CENTER)

            if is_last:
                col_submit = submit_para
                col_sign = sign_cell
                col_remarks = remarks_para
                col_grade = grade_para
            else:
                col_submit = Paragraph("", CELL_CENTER)
                col_sign = Paragraph("", CELL_CENTER)
                col_remarks = Paragraph("", CELL_CENTER)
                col_grade = Paragraph("", CELL_CENTER)

            all_rows.append([
                col_pcode,
                chunk,
                col_assigned,
                col_submit,
                col_sign,
                col_remarks,
                col_grade,
            ])

    table = Table(all_rows, colWidths=col_widths, repeatRows=1, splitByRow=True)

    base_ts = TableStyle([
        # Pure monochrome
        ('BACKGROUND',    (0, 0), (-1, -1), COLOR_BG),
        ('TEXTCOLOR',     (0, 0), (-1, -1), COLOR_TEXT),

        # Outer box & inner grid lines
        ('BOX',           (0, 0), (-1, -1), 0.75, COLOR_LINE),
        ('INNERGRID',     (0, 0), (-1, -1), 0.5,  COLOR_LINE),

        # Header row
        ('VALIGN',        (0, 0), (-1, 0),  'MIDDLE'),
        ('ALIGN',         (0, 0), (-1, 0),  'CENTER'),

        # Data cell vertical alignments (all metadata centered vertically; activity starts at top)
        ('VALIGN',        (0, 1), (0, -1),  'MIDDLE'), # Practical No. at MIDDLE
        ('VALIGN',        (1, 1), (1, -1),  'TOP'),    # Lab Activity at TOP
        ('VALIGN',        (2, 1), (3, -1),  'MIDDLE'), # Assigned & Submission Dates at MIDDLE
        ('VALIGN',        (4, 1), (4, -1),  'MIDDLE'), # Sign at MIDDLE
        ('VALIGN',        (5, 1), (5, -1),  'MIDDLE'), # Remarks at MIDDLE
        ('VALIGN',        (6, 1), (6, -1),  'MIDDLE'), # Grade at MIDDLE

        # Data cell horizontal alignments
        ('ALIGN',         (0, 1), (0, -1),  'CENTER'),
        ('ALIGN',         (1, 1), (1, -1),  'LEFT'),
        ('ALIGN',         (2, 1), (3, -1),  'CENTER'),
        ('ALIGN',         (4, 1), (4, -1),  'CENTER'),
        ('ALIGN',         (5, 1), (5, -1),  'CENTER'), # Remarks at CENTER
        ('ALIGN',         (6, 1), (6, -1),  'CENTER'),

        # Cell paddings
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, 0), 2),
        ('RIGHTPADDING',  (0, 0), (-1, 0), 2),
    ])

    # Handle spans for any unit separator rows
    for idx, r in enumerate(all_rows):
        if idx > 0 and len(r) == 7 and r[1] == "" and r[2] == "":
            base_ts.add('SPAN', (0, idx), (-1, idx))
            base_ts.add('ALIGN', (0, idx), (-1, idx), 'CENTER')
            base_ts.add('VALIGN', (0, idx), (-1, idx), 'MIDDLE')

    table.setStyle(base_ts)
    return table, primary_unit


# ── Public API ────────────────────────────────────────────────────────────────

def generate_practical_list_pdf(
    db: Session,
    subject_id: int,
    student_id: int | None = None,
) -> BytesIO:
    """Generate an institutional practical-list PDF in Gujarat Vidyapith format.

    Args:
        db:          SQLAlchemy session.
        subject_id:  Subject to generate the PDF for.
        student_id:  If supplied, fills in the student's enrollment number,
                     assigned/submitted dates, and published grades+remarks.
                     If ``None``, produces a blank master sheet.

    Returns:
        BytesIO buffer containing the PDF bytes, seeked to position 0.
    """
    from models.schema import (
        Assignment,
        Evaluation,
        Practical,
        Student,
        Subject,
        SubjectHeaderConfig,
        Submission,
    )
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    # ── 1. Load subject & header config ──────────────────────────────────────
    subject: Subject = db.get(Subject, subject_id)
    if subject is None:
        raise ValueError(f"Subject with id={subject_id} not found.")

    header_cfg: SubjectHeaderConfig | None = subject.header_config

    cfg: dict[str, str] = {
        "institution_name": (header_cfg.institution_name if header_cfg else "Gujarat Vidyapith"),
        "department_name":  (header_cfg.department_name  if header_cfg else
                             (subject.department.name if subject.department else "Department of Computer Science")),
        "course_name":      (header_cfg.course_name      if header_cfg else
                             f"{subject.program.code if subject.program else ''} Semester-{subject.semester}"),
        "academic_year":    (header_cfg.academic_year    if header_cfg else "2026-27"),
        "subject_teacher":  (header_cfg.subject_teacher  if header_cfg else ""),
        "subject_title":    (header_cfg.subject_title    if header_cfg else subject.name),
    }

    # ── 2. Load student & enrollment number ───────────────────────────────────
    enrollment_no = ""
    student_obj: Student | None = None
    if student_id is not None:
        student_obj = db.get(Student, student_id)
        if student_obj:
            enrollment_no = student_obj.enrollment_no

    # ── 3. Load practicals ────────────────────────────────────────────────────
    practicals: list[Practical] = list(
        db.scalars(
            select(Practical)
            .where(Practical.subject_id == subject_id)
            .order_by(Practical.practical_number)
        )
    )
    if not practicals:
        raise ValueError(f"No practicals found for subject id={subject_id}.")

    # ── 4. Load assignments & evaluations for this student ────────────────────
    assignments_by_practical: dict[int, Any] = {}
    evaluations_by_practical: dict[int, Any] = {}

    if student_obj:
        prac_ids = [p.id for p in practicals]
        asgn_rows = db.scalars(
            select(Assignment)
            .where(
                Assignment.student_id == student_obj.id,
                Assignment.practical_id.in_(prac_ids),
            )
            .options(
                joinedload(Assignment.submission)
                .joinedload(Submission.evaluation)
                .joinedload(Evaluation.evaluator)
            )
        ).all()
        for a in asgn_rows:
            assignments_by_practical[a.practical_id] = a
            if a.submission and a.submission.evaluation:
                evaluations_by_practical[a.practical_id] = a.submission.evaluation

    # ── 5. Page layout ────────────────────────────────────────────────────────
    output = BytesIO()
    margin = 28.35        # 1 cm margin (standard university print format)
    page_w, page_h = A4
    inner_w = page_w - 2 * margin

    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=f"Practical List — {subject.name}",
        author=cfg["subject_teacher"] or "Faculty",
    )

    story: list = []

    # ── 6. Enclosed 4-row header box ──────────────────────────────────────────
    story.append(_build_header_box(cfg, enrollment_no, inner_w))

    # ── 7. Build practicals table & unit title ─────────────────────────────────
    prac_table, unit_title = _build_practicals_table(
        practicals,
        assignments_by_practical,
        evaluations_by_practical,
        header_cfg,
        inner_w,
    )

    if unit_title:
        story.append(Paragraph(f"<b>{_safe_xml(unit_title)}</b>", UNIT_TITLE_STYLE))
    else:
        story.append(Spacer(1, 6))

    story.append(prac_table)
    story.append(Spacer(1, 14))

    # ── 8. Centered grading scheme reference table ────────────────────────────
    outer_table = Table(
        [[_build_grade_scheme_table()]],
        colWidths=[inner_w],
    )
    outer_table.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(KeepTogether([outer_table]))

    # ── 9. Build PDF with Numbered Canvas ─────────────────────────────────────
    doc.build(story, canvasmaker=_NumberedCanvas)
    output.seek(0)
    return output


def generate_blank_master_pdf(db: Session, subject_id: int) -> BytesIO:
    """Convenience wrapper — generates a blank master sheet (no student data)."""
    return generate_practical_list_pdf(db, subject_id, student_id=None)
