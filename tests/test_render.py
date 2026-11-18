import subprocess
import sys
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image
from pypdf import PdfReader
from typer.testing import CliRunner

import plannings_tool.render.output as output_module
from plannings_tool import load_course, parse_course, schedule_course
from plannings_tool.cli import app
from plannings_tool.diagnostics import DiagnosticCode, RenderError
from plannings_tool.formatting import format_entry_clock
from plannings_tool.render import (
    load_default_logo,
    load_logo,
    load_markdown_template,
    render_gantt_png,
    render_markdown,
    render_pdf,
)
from plannings_tool.render.colors import OKABE_ITO_PALETTE, assign_trainer_colors
from plannings_tool.render.gantt import build_gantt_model, render_gantt_pdf_chart
from plannings_tool.render.output import (
    RenderFormat,
    render_outputs,
    write_outputs_atomically,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def _result(definition):
    return schedule_course(parse_course(yaml.safe_dump(definition, sort_keys=False)))


def _small_definition(definition):
    data = deepcopy(definition)
    data["course"].update(start="09:00", end="09:30", description="Course overview")
    data["course"]["project_id"] = "P-42"
    data["days"][0].update(
        label="Day 1",
        date="2027-01-15",
        location="Room 1",
        schedule=[
            {
                "title": "Intro | hands\\on\nblock",
                "type": "Presentation",
                "duration": "30m",
                "trainers": ["alex"],
            }
        ],
    )
    return data


def test_trainer_colors_preserve_explicit_skip_and_cycle(definition):
    definition["trainers"][0]["color"] = OKABE_ITO_PALETTE[0]
    for index in range(2, 9):
        definition["trainers"].append(
            {
                "id": f"trainer-{index}",
                "name": f"Trainer {index}",
                "email": f"trainer{index}@example.org",
            }
        )
    course = parse_course(yaml.safe_dump(definition))
    colors = assign_trainer_colors(course)
    assert colors["alex"] == OKABE_ITO_PALETTE[0]
    assert colors["sam"] == OKABE_ITO_PALETTE[1]
    assert colors["trainer-7"] == colors["sam"]
    assert colors == assign_trainer_colors(course)


def test_gantt_model_tracks_split_rows_breaks_and_overrun(definition):
    definition["course"].update(start="09:00", end="12:00")
    definition["defaults"] = {"break_mode": "automatic"}
    definition["days"][0].update(
        start="09:00",
        end="12:00",
        schedule=[
            {
                "title": "Long session",
                "type": "Presentation",
                "duration": "2h 30m",
                "trainers": ["alex"],
            }
        ],
    )
    result = _result(definition)
    model = build_gantt_model(result.days[0])
    assert [row.schedule_index for row in model.rows] == [0]
    assert [bar.row for bar in model.bars] == [0, 0]
    assert len(model.breaks) == 1
    assert model.breaks[0].kind == "coffee"
    assert model.axis_min == 0
    assert model.axis_max == 180
    assert model.overrun_at is None

    definition["defaults"] = {"break_mode": "manual"}
    definition["course"]["end"] = "10:00"
    definition["days"][0].update(
        end="10:00",
        schedule=[
            {
                "title": "Overrun",
                "type": "Presentation",
                "duration": "2h",
                "trainers": ["alex"],
            }
        ],
    )
    overrun = build_gantt_model(_result(definition).days[0])
    assert overrun.overrun_at == 60
    assert overrun.axis_max == 120


def test_gantt_png_is_fixed_width_and_deterministic(definition):
    result = _result(_small_definition(definition))
    first = render_gantt_png(result, day_index=0, logo=None)
    second = render_gantt_png(result, day_index=0, logo=None)
    with Image.open(BytesIO(first)) as image:
        assert image.size[0] == 1920
        assert image.mode == "RGBA"
        assert "Software" not in image.info
    assert first == second

    data = _small_definition(definition)
    data["days"][0]["schedule"].extend(
        [
            {
                "title": f"Session {index}",
                "type": "Presentation",
                "duration": "15m",
                "trainers": ["alex", "sam"],
            }
            for index in range(7)
        ]
    )
    taller = render_gantt_png(_result(data), day_index=0, logo=None)
    with (
        Image.open(BytesIO(first)) as one_row,
        Image.open(BytesIO(taller)) as eight_rows,
    ):
        assert eight_rows.height > one_row.height


@pytest.mark.parametrize(("session_count", "trainer_count"), [(1, 2), (3, 5), (8, 13)])
def test_gantt_png_legend_stays_below_axis_title(
    definition, monkeypatch, session_count, trainer_count
):
    data = _small_definition(definition)
    data["trainers"] = [
        {
            "id": f"trainer-{index}",
            "name": f"Trainer {index}",
            "email": f"trainer{index}@example.org",
        }
        for index in range(trainer_count)
    ]
    data["days"][0]["schedule"] = [
        {
            "title": f"Session {index}",
            "type": "Presentation",
            "duration": "15m",
            "trainers": [trainer["id"] for trainer in data["trainers"]],
        }
        for index in range(session_count)
    ]
    print_png = FigureCanvasAgg.print_png

    def check_layout(canvas, *args, **kwargs):
        print_png(canvas, *args, **kwargs)
        renderer = canvas.get_renderer()
        legend_bounds = canvas.figure.legends[0].get_window_extent(renderer)
        title_bounds = canvas.figure.axes[0].xaxis.label.get_window_extent(renderer)
        assert legend_bounds.y0 >= 0
        assert legend_bounds.y1 < title_bounds.y0

    monkeypatch.setattr(FigureCanvasAgg, "print_png", check_layout)
    render_gantt_png(_result(data), day_index=0, logo=None)


@pytest.mark.parametrize("variant", ["png", "pdf"])
@pytest.mark.parametrize(
    ("session_count", "trainer_count"), [(1, 1), (1, 13), (3, 2), (8, 2)]
)
def test_gantt_break_labels_stay_inside_plot_area(
    definition, monkeypatch, variant, session_count, trainer_count
):
    data = deepcopy(definition)
    data["trainers"] = [
        {
            "id": f"trainer-{index}",
            "name": f"Trainer {index}",
            "email": f"trainer{index}@example.org",
        }
        for index in range(trainer_count)
    ]
    sessions = [
        {
            "title": f"Session {index}",
            "type": "Presentation",
            "duration": "30m",
            "trainers": [trainer["id"] for trainer in data["trainers"]],
        }
        for index in range(session_count)
    ]
    data["days"][0]["schedule"] = [
        sessions[0],
        {"break": "coffee"},
        {"break": "lunch"},
        *sessions[1:],
    ]
    print_png = FigureCanvasAgg.print_png
    labels = []

    def check_labels(canvas, *args, **kwargs):
        print_png(canvas, *args, **kwargs)
        renderer = canvas.get_renderer()
        axes = canvas.figure.axes[0]
        labels.extend(text.get_text() for text in axes.texts)
        for text in axes.texts:
            bounds = text.get_window_extent(renderer)
            assert bounds.y0 >= axes.bbox.y0
            assert bounds.y1 <= axes.bbox.y1

    monkeypatch.setattr(FigureCanvasAgg, "print_png", check_labels)
    result = _result(data)
    if variant == "png":
        render_gantt_png(result, day_index=0, logo=None)
    else:
        render_gantt_pdf_chart(result, day_index=0, width_inches=7.5)
    assert labels == ["Coffee", "Lunch"]


def test_gantt_rejects_negative_or_out_of_range_day(definition):
    result = _result(_small_definition(definition))
    with pytest.raises(ValueError, match="Unknown day index"):
        render_gantt_png(result, day_index=-1, logo=None)
    with pytest.raises(ValueError, match="Unknown day index"):
        render_gantt_png(result, day_index=1, logo=None)


def test_markdown_exact_output_escapes_cells_and_marks_floating_days(definition):
    data = _small_definition(definition)
    del data["days"][0]["date"]
    result = render_markdown(_result(data))
    assert result == (
        "# Training\n"
        "\n"
        "Project P-42 · Timezone Europe/Amsterdam\n"
        "\n"
        "Course overview\n"
        "\n"
        "## Day 1\n"
        "\n"
        "floating local time · Room 1 · 09:00-09:30\n"
        "\n"
        "| Start | End | Duration | Activity | Type | Trainers |\n"
        "|---|---|---|---|---|---|\n"
        "| 09:00 | 09:30 | 30m | Intro \\| hands\\\\on block | Presentation | Alex Example |\n"
    )


def test_formatting_shows_past_midnight_times(definition):
    definition["course"].update(start="23:30", end="23:59")
    definition["days"][0].update(
        date="2027-01-15",
        schedule=[
            {
                "title": "Overnight activity",
                "type": "Presentation",
                "duration": "1h",
                "trainers": ["alex"],
            }
        ],
    )
    result = _result(definition)
    entry = result.days[0].entries[0]
    assert format_entry_clock(result.days[0], entry, end=True) == "2027-01-16 00:30"

    del definition["days"][0]["date"]
    result = _result(definition)
    entry = result.days[0].entries[0]
    assert format_entry_clock(result.days[0], entry, end=True) == "+1d 00:30"


def test_markdown_includes_fragments_and_scheduling_warnings():
    result = schedule_course(
        load_course(ROOT / "testdata" / "Kleppen_auto_breaks.yaml")
    )
    markdown = render_markdown(result)
    assert "(part 1/2)" in markdown
    assert "Interactive needs its 15-minute minimum" in markdown
    assert "### Warnings" in markdown


def test_markdown_custom_template_receives_view_model(definition, tmp_path):
    template = tmp_path / "custom.md.j2"
    template.write_text(
        "{{ course.title }} ({{ course.project_id }})\n"
        "{% for day in days %}{% for entry in day.entries %}"
        "{{ day.date }} {{ entry.kind }} {{ entry.start }}-{{ entry.end }} "
        "{{ entry.title | md_cell }} [{{ entry.trainers | join('+') }}]\n"
        "{% endfor %}{% endfor %}\n\n",
        encoding="utf-8",
    )
    markdown = render_markdown(
        _result(_small_definition(definition)),
        template=load_markdown_template(template),
    )
    assert markdown == (
        "Training (P-42)\n"
        "2027-01-15 session 09:00-09:30 Intro \\| hands\\\\on block [Alex Example]\n"
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("ok\n{% for day in days %}", "line 2: Unexpected end of template"),
        ("{{ course.missing }}", "has no attribute 'missing'"),
        ("{{ course.__class__ }}", "is unsafe"),
    ],
)
def test_markdown_template_errors_are_render_errors(
    definition, tmp_path, source, message
):
    template = tmp_path / "broken.md.j2"
    template.write_text(source, encoding="utf-8")
    with pytest.raises(RenderError) as exc_info:
        render_markdown(_result(definition), template=load_markdown_template(template))
    diagnostic = exc_info.value.diagnostics[0]
    assert diagnostic.code == DiagnosticCode.TEMPLATE_ERROR
    assert diagnostic.location == ("template",)
    assert str(template) in diagnostic.message
    assert message in diagnostic.message


def test_markdown_template_unreadable(tmp_path):
    invalid = tmp_path / "latin1.md.j2"
    invalid.write_bytes(b"caf\xe9")
    for path in (tmp_path / "missing.md.j2", invalid):
        with pytest.raises(RenderError) as exc_info:
            load_markdown_template(path)
        diagnostic = exc_info.value.diagnostics[0]
        assert diagnostic.code == DiagnosticCode.TEMPLATE_UNREADABLE
        assert f"Cannot read template {path}" in diagnostic.message


def test_pdf_has_one_page_per_day_content_and_metadata(definition):
    data = _small_definition(definition)
    data["days"].append(
        {
            "label": "Day 2",
            "schedule": [
                {
                    "title": "Second session",
                    "type": "Presentation",
                    "duration": "30m",
                    "trainers": ["sam"],
                }
            ],
        }
    )
    result = _result(data)
    reader = PdfReader(BytesIO(render_pdf(result)))
    assert len(reader.pages) == 2
    first_text = reader.pages[0].extract_text()
    second_text = reader.pages[1].extract_text()
    assert "Course overview" in first_text
    assert "Course overview" not in second_text
    assert "Alex Example" in first_text
    assert "Second session" in second_text
    assert reader.metadata is not None
    assert reader.metadata.title == "Training"
    assert reader.metadata.subject == "P-42"
    assert reader.metadata.creator == "Deltares"


def test_pdf_type_column_fits_every_session_type(definition):
    session_types = ("Presentation", "Exercise", "Excursion", "Interactive")
    definition["days"][0]["schedule"] = [
        {
            "title": f"{session_type} session",
            "type": session_type,
            "duration": "remaining" if session_type == "Interactive" else "30m",
            "trainers": ["alex"],
        }
        for session_type in session_types
    ]
    reader = PdfReader(BytesIO(render_pdf(_result(definition))))
    lines = reader.pages[0].extract_text().splitlines()
    for session_type in session_types:
        type_cell = lines.index(f"{session_type} session") + 1
        assert lines[type_cell : type_cell + 2] == [session_type, "Alex Example"]


def test_pdf_includes_scheduling_warnings():
    result = schedule_course(
        load_course(ROOT / "testdata" / "Kleppen_auto_breaks.yaml")
    )
    reader = PdfReader(BytesIO(render_pdf(result)))
    assert len(reader.pages) == len(result.days)
    assert "Interactive needs its 15-minute minimum" in reader.pages[1].extract_text()


def test_pdf_raises_layout_error_for_oversized_day(definition):
    definition["days"][0]["schedule"] = [
        {
            "title": f"Session {index}",
            "type": "Presentation",
            "duration": "15m",
            "trainers": ["alex"],
        }
        for index in range(30)
    ]
    with pytest.raises(RenderError) as error:
        render_pdf(_result(definition))
    assert error.value.diagnostics[0].code == DiagnosticCode.LAYOUT_OVERFLOW
    assert "Day 1" in error.value.diagnostics[0].message


def test_logo_default_override_and_invalid_file(tmp_path):
    default_logo = load_default_logo()
    with Image.open(BytesIO(default_logo)) as image:
        assert image.format == "PNG"
    override = tmp_path / "logo.png"
    override.write_bytes(default_logo)
    assert load_logo(override) == default_logo
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(RenderError) as error:
        load_logo(invalid)
    assert error.value.diagnostics[0].code == DiagnosticCode.LOGO_UNREADABLE


def test_render_outputs_zero_pads_png_names(definition, tmp_path, monkeypatch):
    data = deepcopy(definition)
    data["course"].update(start="09:00", end="09:15")
    data["days"] = [
        {
            "label": f"Day {index}",
            "schedule": [
                {
                    "title": "Session",
                    "type": "Presentation",
                    "duration": "15m",
                    "trainers": ["alex"],
                }
            ],
        }
        for index in range(1, 13)
    ]
    result = _result(data)
    monkeypatch.setattr(
        output_module, "render_gantt_png", lambda *args, **kwargs: b"png"
    )
    outputs = render_outputs(
        result,
        Path("course.yaml"),
        tmp_path,
        (RenderFormat.PNG,),
        None,
    )
    assert tmp_path / "course-day-01.png" in outputs
    assert tmp_path / "course-day-12.png" in outputs
    assert len(outputs) == 12


def test_atomic_write_reports_partial_success_and_cleans_temporary_files(
    tmp_path, monkeypatch
):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    original_replace = output_module.os.replace

    def fail_second(source, destination):
        if Path(destination) == second:
            raise OSError("write failed")
        original_replace(source, destination)

    monkeypatch.setattr(output_module.os, "replace", fail_second)
    with pytest.raises(RenderError) as error:
        write_outputs_atomically({first: b"first", second: b"second"})
    assert first.read_bytes() == b"first"
    assert not second.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert "first.md" in error.value.diagnostics[0].message


def test_render_cli_default_formats_and_format_subset(tmp_path, definition):
    course = tmp_path / "course.yaml"
    course.write_text(
        yaml.safe_dump(_small_definition(definition), sort_keys=False),
        encoding="utf-8",
    )
    output_dir = tmp_path / "all"
    result = runner.invoke(
        app, ["render", str(course), "--output-dir", str(output_dir)]
    )
    assert result.exit_code == 0, result.output
    assert {path.name for path in output_dir.iterdir()} == {
        "course.pdf",
        "course-day-1.png",
        "course.md",
    }
    assert result.stdout.count("[INFO] - ") == 3

    markdown_dir = tmp_path / "markdown"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(markdown_dir),
            "--format",
            "md",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [path.name for path in markdown_dir.iterdir()] == ["course.md"]

    png_dir = tmp_path / "png"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(png_dir),
            "--format",
            "png",
            "--no-logo",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [path.name for path in png_dir.iterdir()] == ["course-day-1.png"]

    override = tmp_path / "override.png"
    override.write_bytes(load_default_logo())
    override_dir = tmp_path / "override"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(override_dir),
            "--format",
            "png",
            "--logo",
            str(override),
        ],
    )
    assert result.exit_code == 0, result.output
    assert [path.name for path in override_dir.iterdir()] == ["course-day-1.png"]


def test_render_cli_strict_and_invalid_logo_write_nothing(tmp_path, definition):
    course = tmp_path / "course.yaml"
    course.write_text(yaml.safe_dump(definition), encoding="utf-8")
    strict_dir = tmp_path / "strict"
    result = runner.invoke(
        app,
        ["render", str(course), "--output-dir", str(strict_dir), "--strict"],
    )
    assert result.exit_code == 1
    assert not strict_dir.exists()

    invalid_logo = tmp_path / "invalid.png"
    invalid_logo.write_bytes(b"invalid")
    logo_dir = tmp_path / "logo"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(logo_dir),
            "--format",
            "png",
            "--logo",
            str(invalid_logo),
        ],
    )
    assert result.exit_code == 1
    assert "Logo is not a readable PNG or JPEG" in result.stderr
    assert not logo_dir.exists()


def test_render_cli_logo_options_are_mutually_exclusive(tmp_path, definition):
    course = tmp_path / "course.yaml"
    course.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(
        app,
        ["render", str(course), "--logo", "logo.png", "--no-logo"],
    )
    assert result.exit_code == 2
    assert "--logo and --no-logo are mutually exclusive" in result.output


def test_render_cli_markdown_template(tmp_path, definition):
    course = tmp_path / "course.yaml"
    course.write_text(yaml.safe_dump(definition), encoding="utf-8")
    template = tmp_path / "custom.md.j2"
    template.write_text("Custom {{ course.title }}\n", encoding="utf-8")
    output_dir = tmp_path / "output"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(output_dir),
            "--format",
            "md",
            "--template",
            str(template),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output_dir / "course.md").read_text(encoding="utf-8") == (
        "Custom Training\n"
    )

    template.write_text("{{ missing }}", encoding="utf-8")
    failed_dir = tmp_path / "failed"
    result = runner.invoke(
        app,
        [
            "render",
            str(course),
            "--output-dir",
            str(failed_dir),
            "--template",
            str(template),
        ],
    )
    assert result.exit_code == 1
    assert "course.yaml:template - Template" in result.stderr
    assert "'missing' is undefined" in result.stderr
    assert not failed_dir.exists()

    result = runner.invoke(
        app,
        ["render", str(course), "--format", "pdf", "--template", str(template)],
    )
    assert result.exit_code == 2
    assert "--template requires --format md" in result.output


def test_render_cli_layout_failure_writes_nothing(tmp_path, definition):
    definition["days"][0]["schedule"] = [
        {
            "title": f"Session {index}",
            "type": "Presentation",
            "duration": "15m",
            "trainers": ["alex"],
        }
        for index in range(30)
    ]
    course = tmp_path / "course.yaml"
    course.write_text(yaml.safe_dump(definition), encoding="utf-8")
    output_dir = tmp_path / "output"
    result = runner.invoke(
        app, ["render", str(course), "--output-dir", str(output_dir)]
    )
    assert result.exit_code == 1
    assert "cannot fit on one portrait A4 page" in result.stderr
    assert not output_dir.exists()


def test_root_import_does_not_load_rendering_libraries():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, plannings_tool; "
            "assert 'matplotlib' not in sys.modules; "
            "assert 'reportlab' not in sys.modules; "
            "assert 'jinja2' not in sys.modules",
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
