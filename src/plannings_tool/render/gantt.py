"""Gantt chart model and deterministic Agg rendering."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Polygon, Rectangle

from ..diagnostics import DiagnosticCode, RenderError
from ..models import Break, Trainer
from ..scheduler import ScheduledDay, ScheduleResult
from ..values import minutes_since_midnight
from ._assets import validate_logo
from ._errors import raise_render_error
from .colors import assign_trainer_colors


@dataclass(frozen=True)
class GanttRow:
    schedule_index: int
    title: str


@dataclass(frozen=True)
class GanttBar:
    row: int
    start: int
    end: int
    trainer_ids: tuple[str, ...]


@dataclass(frozen=True)
class GanttBreak:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class GanttModel:
    rows: tuple[GanttRow, ...]
    bars: tuple[GanttBar, ...]
    breaks: tuple[GanttBreak, ...]
    axis_min: int
    axis_max: int
    overrun_at: int | None


def build_gantt_model(day: ScheduledDay) -> GanttModel:
    """Build the renderer-independent rows and intervals for one day."""
    titles: dict[int, str] = {}
    for entry in day.entries:
        if (
            entry.kind == "session"
            and entry.schedule_index is not None
            and not isinstance(entry.source, Break)
        ):
            titles.setdefault(entry.schedule_index, entry.source.title)
    indices = sorted(titles)
    row_by_index = {schedule_index: row for row, schedule_index in enumerate(indices)}
    rows = tuple(
        GanttRow(schedule_index, _truncate(titles[schedule_index], 36))
        for schedule_index in indices
    )
    bars = tuple(
        GanttBar(
            row=row_by_index[entry.schedule_index],
            start=entry.start_offset_minutes,
            end=entry.end_offset_minutes,
            trainer_ids=entry.trainer_ids,
        )
        for entry in day.entries
        if entry.kind == "session" and entry.schedule_index is not None
    )
    breaks = tuple(
        GanttBreak(
            entry.start_offset_minutes,
            entry.end_offset_minutes,
            entry.break_kind or "coffee",
        )
        for entry in day.entries
        if entry.kind == "break"
    )
    start_minute = minutes_since_midnight(day.start_local)
    axis_min = -(start_minute % 60)
    last_end = max(
        day.available_minutes,
        max((entry.end_offset_minutes for entry in day.entries), default=0),
    )
    axis_max = ceil((start_minute + last_end) / 60) * 60 - start_minute
    overrun_at = (
        day.available_minutes
        if day.entries and day.entries[-1].end_offset_minutes > day.available_minutes
        else None
    )
    return GanttModel(rows, bars, breaks, axis_min, axis_max, overrun_at)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _tick_label(day: ScheduledDay, offset: int) -> str:
    clock = (minutes_since_midnight(day.start_local) + offset) % (24 * 60)
    hour, minute = divmod(clock, 60)
    if day.date is not None:
        tick_date = day.date.toordinal() + (
            minutes_since_midnight(day.start_local) + offset
        ) // (24 * 60)
        if tick_date != day.date.toordinal():
            return f"{day.date.fromordinal(tick_date):%Y-%m-%d} {hour:02}:{minute:02}"
    elif offset >= 24 * 60:
        return f"+{offset // (24 * 60)}d {hour:02}:{minute:02}"
    return f"{hour:02}:{minute:02}"


def _draw_stripes(
    axes,
    rectangle: Rectangle,
    colors: tuple[str, ...],
    axis_min: int,
    axis_max: int,
    row_count: int,
) -> None:
    if len(colors) < 2:
        return
    bbox = axes.get_window_extent()
    x_scale = bbox.width / (axis_max - axis_min)
    y_scale = bbox.height / row_count
    slope = y_scale / x_scale
    x0, y0 = rectangle.get_xy()
    x1 = x0 + rectangle.get_width()
    y1 = y0 + rectangle.get_height()
    q_values = [x + slope * y for x in (x0, x1) for y in (y0, y1)]
    stripe_width = 8 / x_scale
    first = floor(min(q_values) / stripe_width)
    last = ceil(max(q_values) / stripe_width)
    for index in range(first, last):
        lower = index * stripe_width
        upper = lower + stripe_width
        vertices = [
            (lower - slope * y0, y0),
            (upper - slope * y0, y0),
            (upper - slope * y1, y1),
            (lower - slope * y1, y1),
        ]
        stripe = Polygon(
            vertices,
            closed=True,
            facecolor=colors[index % len(colors)],
            edgecolor="none",
            transform=axes.transData,
            zorder=4,
        )
        axes.add_patch(stripe)
        stripe.set_clip_path(rectangle)


def _day_heading(day: ScheduledDay) -> str:
    day_date = day.date.isoformat() if day.date is not None else "floating local time"
    parts = [day.label, day_date]
    if day.location:
        parts.append(day.location)
    parts.append(f"{day.start_local:%H:%M}-{day.end_local:%H:%M} {day.timezone}")
    return " · ".join(parts)


def _render_chart(
    day: ScheduledDay,
    trainer_colors: dict[str, str],
    trainers: tuple[Trainer, ...],
    course_title: str,
    *,
    width_inches: float,
    dpi: int,
    include_heading: bool,
    include_legend: bool,
    logo: bytes | None,
) -> bytes:
    model = build_gantt_model(day)
    legend_handles = []
    if include_legend:
        appearing = {trainer_id for bar in model.bars for trainer_id in bar.trainer_ids}
        legend_handles = [
            Patch(facecolor=trainer_colors[trainer.id], label=trainer.name)
            for trainer in trainers
            if trainer.id in appearing
        ]
    legend_rows = ceil(len(legend_handles) / 4)
    row_count = max(1, len(model.rows))
    chart_height = max(0.5, len(model.rows) * (0.34 if include_heading else 0.22))
    top_space = 0.82 if include_heading else 0.1 if include_legend else 0.0
    bottom_space = 0.65 + 0.25 * legend_rows if legend_handles else 0.45
    height_inches = max(2.1, chart_height + top_space + bottom_space)
    figure = Figure(
        figsize=(width_inches, height_inches),
        dpi=dpi,
        facecolor=(1, 1, 1, 0) if include_heading else "white",
    )
    canvas = FigureCanvasAgg(figure)
    if include_heading:
        axes_rect = (
            0.20,
            bottom_space / height_inches,
            0.77,
            chart_height / height_inches,
        )
    elif include_legend:
        axes_rect = (
            0.25,
            bottom_space / height_inches,
            0.72,
            (height_inches - bottom_space - top_space) / height_inches,
        )
    else:
        axes_rect = (0.25, 0.24, 0.72, 0.72)
    axes = figure.add_axes(axes_rect)
    if include_heading:
        axes.set_facecolor((1, 1, 1, 0))
    else:
        axes.set_facecolor("white")

    y_positions = [len(model.rows) - index - 1 for index in range(len(model.rows))]
    axes.set_ylim(-0.5, len(model.rows) - 0.5 if model.rows else 0.5)
    axes.set_xlim(model.axis_min, model.axis_max)
    axes.set_yticks(y_positions, labels=[row.title for row in model.rows])
    axes.tick_params(axis="y", length=0, pad=8, labelsize=8, colors="#333333")
    axes.tick_params(axis="x", labelsize=8, colors="#333333")
    axes.spines[["top", "right", "left"]].set_visible(False)
    axes.spines["bottom"].set_color("#777777")

    major_start = model.axis_min
    major_ticks = list(range(major_start, model.axis_max + 1, 60))
    minor_ticks = list(range(model.axis_min, model.axis_max + 1, 15))
    axes.set_xticks(major_ticks)
    axes.set_xticklabels([_tick_label(day, tick) for tick in major_ticks])
    axes.set_xticks(minor_ticks, minor=True)
    axes.grid(axis="x", which="major", color="#C9CDD2", linewidth=0.65, zorder=0)
    axes.grid(axis="x", which="minor", color="#E5E7EB", linewidth=0.35, zorder=0)
    axes.set_axisbelow(True)
    axes.set_xlabel(f"Local time · {day.timezone}", fontsize=8, color="#333333")
    canvas.draw()

    for break_item in model.breaks:
        axes.axvspan(
            break_item.start,
            break_item.end,
            color="#E4E7EB",
            alpha=0.9,
            zorder=1,
        )
        if model.rows:
            axes.text(
                (break_item.start + break_item.end) / 2,
                0.5,
                "Coffee" if break_item.kind == "coffee" else "Lunch",
                transform=axes.get_xaxis_transform(),
                rotation=90,
                ha="center",
                va="center",
                fontsize=8,
                color="#555B63",
                clip_on=True,
                zorder=2,
            )

    for bar in model.bars:
        y = len(model.rows) - bar.row - 1
        colors = tuple(trainer_colors[item] for item in bar.trainer_ids)
        fill = colors[0] if colors else "#7A8793"
        rectangle = Rectangle(
            (bar.start, y - 0.28),
            bar.end - bar.start,
            0.56,
            facecolor=fill,
            edgecolor="#404850",
            linewidth=0.45,
            zorder=3,
        )
        axes.add_patch(rectangle)
        _draw_stripes(
            axes,
            rectangle,
            colors,
            model.axis_min,
            model.axis_max,
            row_count,
        )

    if model.overrun_at is not None:
        axes.axvline(
            model.overrun_at,
            color="#B42318",
            linestyle="--",
            linewidth=1.0,
            zorder=5,
        )
    if include_heading:
        figure.text(
            0.02,
            0.985,
            course_title,
            fontsize=11,
            fontweight="bold",
            color="#333333",
            va="top",
        )
        figure.text(
            0.02,
            0.925,
            _day_heading(day),
            fontsize=8,
            color="#555B63",
            va="top",
        )
        if logo is not None:
            image = validate_logo(logo)
            logo_axes = figure.add_axes((0.82, 0.88, 0.16, 0.10))
            logo_axes.imshow(image)
            logo_axes.axis("off")

    if legend_handles:
        figure.legend(
            handles=legend_handles,
            labels=[handle.get_label() for handle in legend_handles],
            loc="lower center",
            bbox_to_anchor=(0.5, 0.05 / height_inches),
            ncol=min(4, len(legend_handles)),
            frameon=False,
            fontsize=8,
        )

    buffer = BytesIO()
    canvas.print_png(buffer, metadata={"Software": None})
    return buffer.getvalue()


def render_gantt_png(
    result: ScheduleResult,
    *,
    day_index: int,
    logo: bytes | None,
) -> bytes:
    """Render one day's Gantt PNG at 1920 pixels wide."""
    if not 0 <= day_index < len(result.days):
        raise ValueError(f"Unknown day index: {day_index}")
    day = result.days[day_index]
    if logo is not None:
        validate_logo(logo)
    try:
        return _render_chart(
            day,
            assign_trainer_colors(result.course),
            tuple(result.course.trainers),
            result.course.course.title,
            width_inches=9.6,
            dpi=200,
            include_heading=False,
            include_legend=True,
            logo=logo,
        )
    except RenderError:
        raise
    except (ValueError, OSError) as exc:
        raise raise_render_error(
            DiagnosticCode.RENDER_FAILURE,
            f"Cannot render Gantt chart: {exc}",
            ("days", day_index),
        ) from exc


def render_gantt_pdf_chart(
    result: ScheduleResult,
    *,
    day_index: int,
    width_inches: float,
) -> bytes:
    """Render the chart-only variant used in a PDF page."""
    day = result.days[day_index]
    return _render_chart(
        day,
        assign_trainer_colors(result.course),
        tuple(result.course.trainers),
        result.course.course.title,
        width_inches=width_inches,
        dpi=300,
        include_heading=False,
        include_legend=False,
        logo=None,
    )
