"""Single-page-per-day ReportLab PDF rendering."""

from collections import Counter
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from matplotlib import get_data_path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Flowable,
    Image,
    LayoutError,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..diagnostics import DiagnosticCode, RenderError
from ..formatting import format_duration, format_entry_clock, format_entry_title
from ..scheduler import ScheduledDay, ScheduleResult
from ._assets import validate_logo
from ._common import course_warnings, day_warnings, trainer_names
from ._errors import raise_render_error
from .colors import assign_trainer_colors
from .gantt import render_gantt_pdf_chart

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"


class _DayMarker(Flowable):
    def __init__(self, day_index: int):
        super().__init__()
        self.day_index = day_index
        self.width = 0
        self.height = 0

    def draw(self) -> None:
        pass


class _TrackedDocTemplate(SimpleDocTemplate):
    def __init__(
        self,
        *args,
        title: str,
        subject: str,
        **kwargs,
    ):
        super().__init__(
            *args,
            title=title,
            subject=subject,
            author="Deltares",
            creator="Deltares",
            invariant=1,
            **kwargs,
        )
        self.current_day_index = 0
        self.page_day_indices: list[int] = []
        self._pdf_title = title
        self._pdf_subject = subject

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, _DayMarker):
            self.current_day_index = flowable.day_index

    def afterPage(self) -> None:
        self.page_day_indices.append(self.current_day_index)


def _register_fonts() -> None:
    font_directory = Path(get_data_path()) / "fonts" / "ttf"
    pdfmetrics.registerFont(TTFont(FONT, str(font_directory / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(
        TTFont(FONT_BOLD, str(font_directory / "DejaVuSans-Bold.ttf"))
    )
    pdfmetrics.registerFontFamily(
        FONT,
        normal=FONT,
        bold=FONT_BOLD,
        italic=FONT,
        boldItalic=FONT_BOLD,
    )


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "CourseTitle",
            fontName=FONT_BOLD,
            fontSize=16,
            leading=19,
            textColor=colors.HexColor("#333333"),
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            fontName=FONT_BOLD,
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#333333"),
            spaceBefore=5,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=FONT,
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#333333"),
        ),
        "table": ParagraphStyle(
            "Table",
            fontName=FONT,
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#333333"),
        ),
        "metadata": ParagraphStyle(
            "Metadata",
            fontName=FONT,
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#555B63"),
        ),
    }


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(value).replace("\n", "<br/>"), style)


def _day_heading(day: ScheduledDay) -> str:
    date_text = day.date.isoformat() if day.date is not None else "floating local time"
    details = [day.label, date_text]
    if day.location:
        details.append(day.location)
    details.append(f"{day.start_local:%H:%M}-{day.end_local:%H:%M} {day.timezone}")
    return " · ".join(details)


def _header(
    result: ScheduleResult,
    day: ScheduledDay,
    styles: dict[str, ParagraphStyle],
    logo: bytes | None,
    available_width: float,
) -> Table:
    title_content = [
        _paragraph(result.course.course.title, styles["title"]),
        _paragraph(_day_heading(day), styles["metadata"]),
    ]
    if result.course.course.project_id:
        title_content.append(
            _paragraph(f"Project {result.course.course.project_id}", styles["metadata"])
        )
    logo_image = None
    logo_width = 0
    if logo is not None:
        image = validate_logo(logo)
        logo_height = 12 * mm
        logo_width = min(logo_height * image.width / image.height, 130)
        logo_image = Image(
            BytesIO(logo),
            width=logo_width,
            height=logo_height,
            kind="proportional",
        )
    if logo_image is None:
        rows = [title_content]
        widths = [available_width]
    else:
        rows = [[title_content, logo_image]]
        widths = [available_width - logo_width, logo_width]
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    return table


def _timetable(
    result: ScheduleResult,
    day: ScheduledDay,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table:
    names = trainer_names(result)
    headings = ("Start", "End", "Duration", "Activity", "Type", "Trainers")
    rows = [[_paragraph(title, styles["table"]) for title in headings]]
    for entry in day.entries:
        rows.append(
            [
                _paragraph(format_entry_clock(day, entry, end=False), styles["table"]),
                _paragraph(format_entry_clock(day, entry, end=True), styles["table"]),
                _paragraph(format_duration(entry.duration_minutes), styles["table"]),
                _paragraph(format_entry_title(entry), styles["table"]),
                _paragraph(entry.session_type or "Break", styles["table"]),
                _paragraph(
                    ", ".join(names[trainer_id] for trainer_id in entry.trainer_ids),
                    styles["table"],
                ),
            ]
        )
    widths = [
        49,
        49,
        52,
        available_width * 0.31,
        60,
        available_width - (49 + 49 + 52 + available_width * 0.31 + 60),
    ]
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D1D5DB")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EDF1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row_index, entry in enumerate(day.entries, start=1):
        if entry.kind == "break":
            style.append(
                (
                    "BACKGROUND",
                    (0, row_index),
                    (-1, row_index),
                    colors.HexColor("#EEF0F2"),
                )
            )
    table.setStyle(TableStyle(style))
    return table


def _trainer_legend(
    result: ScheduleResult,
    day: ScheduledDay,
    colors_by_trainer: dict[str, str],
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table | None:
    trainer_ids = {trainer for entry in day.entries for trainer in entry.trainer_ids}
    trainers = [
        trainer for trainer in result.course.trainers if trainer.id in trainer_ids
    ]
    if not trainers:
        return None
    rows = [[_paragraph("Trainer legend", styles["heading"]), ""]]
    rows.extend(["", _paragraph(trainer.name, styles["table"])] for trainer in trainers)
    table = Table(rows, colWidths=[14, available_width - 14], hAlign="LEFT")
    table_style = [
        ("SPAN", (0, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for row_index, trainer in enumerate(trainers, start=1):
        table_style.append(
            (
                "BACKGROUND",
                (0, row_index),
                (0, row_index),
                colors.HexColor(colors_by_trainer[trainer.id]),
            )
        )
    table.setStyle(TableStyle(table_style))
    return table


def _warnings(warnings, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    if not warnings:
        return []
    return [
        _paragraph("Warnings", styles["heading"]),
        *[
            _paragraph(f"• {diagnostic.message}", styles["body"])
            for diagnostic in warnings
        ],
    ]


def _layout_overflow(day: ScheduledDay, day_index: int, reason: str) -> RenderError:
    return raise_render_error(
        DiagnosticCode.LAYOUT_OVERFLOW,
        f"Day '{day.label}' cannot fit on one portrait A4 page: {reason}",
        ("days", day_index),
    )


def render_pdf(result: ScheduleResult, *, logo: bytes | None = None) -> bytes:
    """Render one fixed-layout A4 page per scheduled day."""
    if logo is not None:
        validate_logo(logo)
    _register_fonts()
    styles = _styles()
    buffer = BytesIO()
    doc = _TrackedDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        allowSplitting=1,
        title=result.course.course.title,
        subject=result.course.course.project_id or "",
    )
    colors_by_trainer = assign_trainer_colors(result.course)
    story: list[Flowable] = []
    course_level_warnings = course_warnings(result)
    for day_index, day in enumerate(result.days):
        story.append(_DayMarker(day_index))
        story.append(_header(result, day, styles, logo, doc.width))
        story.append(Spacer(1, 4))
        if day_index == 0 and result.course.course.description:
            story.extend(
                [
                    _paragraph(result.course.course.description, styles["body"]),
                    Spacer(1, 4),
                ]
            )
        chart = render_gantt_pdf_chart(
            result,
            day_index=day_index,
            width_inches=doc.width / 72,
        )
        story.append(
            Image(
                BytesIO(chart),
                width=doc.width,
                height=doc.width * _png_aspect_ratio(chart),
            )
        )
        story.append(Spacer(1, 4))
        story.append(_timetable(result, day, styles, doc.width))
        legend = _trainer_legend(result, day, colors_by_trainer, styles, doc.width)
        if legend is not None:
            story.append(legend)
        warnings = list(day_warnings(result, day_index))
        if day_index == 0:
            warnings.extend(course_level_warnings)
        story.extend(_warnings(warnings, styles))
        if day_index < len(result.days) - 1:
            story.append(PageBreak())

    def make_canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        return canvas.Canvas(*args, **kwargs)

    try:
        doc.build(story, canvasmaker=make_canvas)
    except LayoutError as exc:
        if result.days:
            day_index = min(doc.current_day_index, len(result.days) - 1)
            raise _layout_overflow(result.days[day_index], day_index, str(exc)) from exc
        raise

    if len(doc.page_day_indices) != len(result.days):
        counts = Counter(doc.page_day_indices)
        day_index = next(
            (index for index in range(len(result.days)) if counts.get(index, 0) > 1),
            min(len(result.days) - 1, len(doc.page_day_indices)),
        )
        raise _layout_overflow(
            result.days[day_index],
            day_index,
            "content overflowed its assigned page",
        )
    return buffer.getvalue()


def _png_aspect_ratio(data: bytes) -> float:
    from PIL import Image as PILImage

    with PILImage.open(BytesIO(data)) as image:
        return image.height / image.width
