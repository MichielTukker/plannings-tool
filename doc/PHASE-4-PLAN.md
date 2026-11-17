# Phase 4: Rendering — PDF, Gantt PNG, and Markdown timetable

## Outcome and boundaries

Render a resolved `ScheduleResult` into three output formats from one command:

| Output | Content | File name |
|---|---|---|
| PDF | One portrait A4 page per day: heading, logo, Gantt chart, timetable, trainer legend, warnings | `<stem>.pdf` |
| Gantt PNG | One transparent PNG per day: heading, logo, Gantt chart, trainer legend | `<stem>-day-<n>.png` |
| Markdown | One file: course heading, then a timetable per day with warnings | `<stem>.md` |

`<stem>` is the input YAML file name without its extension. `<n>` is the
1-based input day index, zero-padded to the number of digits of the day count
(`day-1` for 2 days, `day-01` for 12 days).

**Prerequisite:** phase 3 must be complete. Renderers depend on automatic
breaks, `part`/`parts` fragments, and `schedule_index` identity in the result.

Out of scope: email templates, action lists, requirement lists, the web
frontend, HTML/JSON export, themes, and localized labels (labels stay English).
Renderers never recalculate scheduling. They only group, format, and draw the
resolved entries.

## Agreed behavior

### Shared rendering rules

- Add `course: CourseDefinition` to `ScheduleResult`. This lets every renderer
  and future output read the title, description, project ID, and trainers from
  a single object.
- Show course-local 24-hour `HH:MM` times and the IANA timezone identifier.
  Undated days are labelled "floating local time". Times past midnight use the
  same notation as the CLI preview.
- Split sessions use `Title (part 1/2)` in tables. Automatic breaks look the same
  as manual breaks.
- Durations use the YAML notation (`15m`, `1h`, `1h 15m`).
- Outputs show warnings, not info diagnostics. Diagnostics whose location starts
  with `("days", i)` belong to day `i`; all other diagnostics are course-level.
- Move the CLI's duration and clock formatting into a pure
  `plannings_tool/formatting.py` module. The CLI preview, PDF, and Markdown share
  it. It must not import matplotlib or ReportLab.

### Trainer colors

- Use an explicit trainer `color` when it is supplied.
- Otherwise assign colors in YAML trainer order from the colorblind-safe
  Okabe–Ito palette (`#E69F00`, `#56B4E9`, `#009E73`, `#F0E442`, `#0072B2`,
  `#D55E00`, `#CC79A7`). Skip palette colors already used explicitly and cycle
  when the palette is exhausted.
- Colors are assigned once per course, so a trainer has the same color on
  every day and in every output.
- A session with several trainers is drawn with equal-width 45° diagonal
  stripes. The stripes cycle through the trainers' colors in the session's
  trainer order. Draw them as clipped polygons rather than matplotlib hatches,
  because hatches support only one color.
- The trainer legend lists the trainers appearing on that day, in YAML order,
  with a swatch and full name. The timetable always shows trainer names, so
  grayscale prints remain readable.

### Gantt chart

Use one matplotlib figure builder for both the PNG and the PDF chart. Use
`matplotlib.figure.Figure` with the Agg canvas directly, not `pyplot` or a GUI
backend.

- **Rows:** one row per original session (`schedule_index`), top to bottom in
  input order, labelled with the session title. Truncate long labels with an
  ellipsis; the timetable shows the full title.
- **Fragments:** all fragments of a split session are separate bars on the same
  row. The gap between bars is the break that split the session.
- **Breaks:** labelled ("Coffee", "Lunch") light-gray vertical bands spanning
  all rows at their scheduled times. Breaks do not get rows.
- **Time axis:** horizontal, at the bottom. It runs from the day start rounded
  down to the hour to the later of the day end and the last entry end, rounded
  up to the hour. Use hourly `HH:MM` major ticks, 15-minute minor ticks, and
  light vertical grid lines. Compute positions from elapsed offsets and the
  local day start, so dated and floating days render the same way.
- **Overrun:** when the schedule ends after the configured day end, draw a
  dashed vertical line at the day end. Draw the overrunning activities in full.
- **Bars:** filled in trainer colors or stripes, without text inside.

| Variant | PNG | PDF chart |
|---|---|---|
| Width | 1920 px (9.6 in at 200 DPI) | PDF frame width at 300 DPI |
| Height | Grows with the number of session rows | Grows with the number of session rows |
| Background | Transparent (RGBA, alpha 0) | White |
| Heading | Course title; day label, date, location, local hours, timezone | Omitted (drawn by the PDF page) |
| Trainer legend | Included below the chart | Omitted (drawn by the PDF page) |
| Logo | Top right | Omitted (drawn in the PDF page header) |
| Warnings | Not included | Omitted (listed on the PDF page) |

Text uses dark gray. Transparent PNGs are meant for light backgrounds;
dark-background variants are out of scope. Remove the matplotlib version
metadata from PNG files, so the same matplotlib version produces
byte-identical output.

### PDF

Use ReportLab Platypus for page layout.

- Use one portrait A4 page per day with 15 mm margins, in input day order.
- Page content, top to bottom:
  1. Header: logo top right (12 mm high, aspect ratio preserved); course title;
     day label; date or "floating local time"; location; local start-end;
     timezone; project ID when present.
  2. Course description, on the first page only.
  3. Gantt chart: the PDF chart variant as a 300 DPI PNG, scaled to the frame
     width.
  4. Timetable: Start, End, Duration, Activity, Type, and Trainers (full
     names). Breaks show `Break` as their type, have no trainers, and use a
     shaded row.
  5. Trainer legend for the day.
  6. Warnings: that day's warnings, plus course-level warnings on the first
     page. Omit the section when there are none.
- Embed DejaVu Sans, which ships with matplotlib, in ReportLab. This gives
  Unicode coverage and the same typography as the chart. Use fixed font sizes:
  title 16 pt, headings 11 pt, body and table 9 pt, and nothing below 8 pt.
- Never shrink content and never add an extra page. If a day does not fit on
  one page, raise a `layout_overflow` error naming the day. Size estimate: one
  page holds about 15 sessions with breaks.
- Set the title to the course title, the subject to the project ID, and the
  creator to `plannings-tool`. Use ReportLab invariant mode, so output is
  reproducible in tests.

### Markdown timetable

Write one UTF-8 file with LF line endings and a trailing newline:

```markdown
# Cursus regelkleppen & terugslagkleppen

Project 11213334-002 · Timezone Europe/Amsterdam

Course description.

## Day 2

2026-11-13 · Paviljoen 2&3 · 09:00-16:00

| Start | End | Duration | Activity | Type | Trainers |
|---|---|---|---|---|---|
| 09:00 | 09:30 | 30m | Opening terugslagkleppen | Presentation | Femke Verhaart |
| 09:30 | 10:30 | 1h | terugslagkleppen stationair | Presentation | Michiel Tukker |
| 10:30 | 10:45 | 15m | Coffee break | Break | |
| 10:45 | 12:15 | 1h 30m | terugslagkleppen dynamisch (part 1/2) | Presentation | Femke Verhaart |

### Warnings

- Interactive needs its 15-minute minimum, causing 15 minutes of overflow
```

- Write course-level warnings under the course heading, as a `## Warnings`
  section. Omit warning sections that would be empty.
- Escape `|` and `\` in cell text and replace line breaks with spaces.
- Undated days show "floating local time" instead of the date.

### Logo

- Use the provided `assets/Deltares_logo_D-blauw_RGB.png` as the default. It is
  an 800 × 260 RGBA PNG with transparency; the supplied SVG is not needed.
  Preserve the source asset and package a copy as
  `src/plannings_tool/assets/deltares-logo.png`. Load the packaged copy with
  `importlib.resources` and verify that the built wheel contains it. Do not
  download or recreate the logo.
- `--logo PATH` overrides it with a PNG or JPEG. `--no-logo` omits it. The two
  options are mutually exclusive and produce a Typer usage error (exit code 2)
  when used together.
- A missing or unreadable logo raises a `logo_unreadable` error, and nothing
  is written.

### CLI

```text
plan render course.yaml
plan render course.yaml --output-dir out --format pdf --format png
plan render course.yaml --logo other-logo.png
plan render course.yaml --no-logo --strict
```

- `--format` is repeatable with values `pdf`, `png`, and `md`. The default is
  all three.
- `--output-dir` defaults to the current working directory and is created if
  it is missing.
- Load and schedule first. Print warnings and info diagnostics to stderr, as
  `plan schedule` does. With `--strict`, any warning exits with code 1 before
  any output is rendered.
- Render every requested output in memory first. A render error such as
  `layout_overflow` or `logo_unreadable` exits with code 1, and no file is
  written.
- Then write each file atomically: create a temporary file in the target
  directory and replace the destination with `os.replace`. If writing fails
  partway, report the failed file with `output_write`, name the files already
  replaced, remove temporary files, and exit with code 1.
- Do not delete PNGs left by earlier runs for days that no longer exist.
- On success, print one `output_written` info line per file on stdout.
- Keep the existing `0`/`1`/`2` exit-code convention.

### Python API

Add a `plannings_tool.render` subpackage. `import plannings_tool` must not import
it or load matplotlib or ReportLab.

```python
from plannings_tool.render import (
    RenderError,
    load_default_logo,
    render_gantt_png,
    render_markdown,
    render_pdf,
)

pdf_bytes = render_pdf(result, logo=load_default_logo())
png_bytes = render_gantt_png(result, day_index=0, logo=None)
markdown_text = render_markdown(result)
```

- `logo` is image bytes, or `None` to omit the logo.
- `RenderError` derives from `PlanningError` and carries diagnostics. Define it
  in `diagnostics.py`, next to the existing error types.
- New `DiagnosticCode` values: `layout_overflow`, `logo_unreadable`,
  `output_write`, and `output_written`.

Module layout:

```text
src/plannings_tool/formatting.py      durations, clocks, part labels (pure)
src/plannings_tool/render/__init__.py public render API
src/plannings_tool/render/colors.py   trainer color assignment
src/plannings_tool/render/gantt.py    matplotlib figure builder (PNG + PDF variants)
src/plannings_tool/render/pdf.py      ReportLab page assembly
src/plannings_tool/render/markdown.py Markdown timetable
src/plannings_tool/render/output.py   file naming and atomic multi-file writing
src/plannings_tool/assets/deltares-logo.png
```

### Dependencies

- Runtime: `matplotlib` and `reportlab`. Add `pillow` explicitly if code imports
  it directly, for example for logo decoding.
- Dev: `pypdf`, for page count and text extraction in tests.

## Implementation sequence

### 1. Foundations

- Confirm that phase 3 is complete.
- Add the dependencies, `ScheduleResult.course`, `formatting.py` (switch the
  CLI preview to it), `RenderError`, and the new diagnostic codes.
- Implement trainer color assignment.

### 2. Gantt prototype (highest risk; do first)

- Build the chart for both Kleppen examples: rows, fragments, break bands,
  stripes, time axis, and overrun line.
- Settle row height, label truncation, and legend placement visually before
  starting the PDF.
- Add the PNG variant: transparent background, heading, legend, logo, 1920 px
  width, and stripped metadata.

### 3. Markdown renderer

- Implement `render_markdown` with escaping, fragments, and warning sections.

### 4. PDF renderer

- Assemble the page: header, logo, description, chart, timetable, legend, and
  warnings.
- Register the font, detect `layout_overflow`, and set metadata and invariant
  mode.

### 5. Logo

- Package the supplied PNG from `assets/Deltares_logo_D-blauw_RGB.png` as
  `src/plannings_tool/assets/deltares-logo.png`. Implement
  `load_default_logo`, `--logo`, `--no-logo`, and `logo_unreadable`.
- Verify that the wheel contains the asset.

### 6. CLI

- Add `plan render`, format and output-directory handling, file naming, strict
  mode, in-memory rendering, and atomic writing.

### 7. Documentation

- Document `plan render`, outputs, file naming, logo options, and the render
  API in the README.
- Mark milestone 4 complete in `IMPLEMENTATION-PLAN.md`.

## Acceptance tests

Use pytest, Ruff, and `ty check`. Use deterministic fixtures, no network access,
and a generated test logo. Do not compare pixels.

| Area | Acceptance cases |
|---|---|
| Colors | Explicit colors kept; palette order; explicit colors skipped; cycling; same color across days |
| Formatting | Shared durations, clocks, past-midnight times, and part labels; CLI preview unchanged |
| Gantt PNG | Valid RGBA PNG, 1920 px wide; corner pixel alpha 0; height grows with rows; one file per day; byte-identical on re-render; no matplotlib version metadata |
| Gantt content | Unit tests on the figure model: one row per `schedule_index`; split session has 2 bars on 1 row; break bands; axis range and overrun line |
| Markdown | Exact output for both Kleppen examples, with fragments, automatic breaks, warnings, escaping, and undated days |
| PDF | Page count equals day count; extracted text contains titles, times, trainer names, and warnings; description only on page 1; `layout_overflow` for an oversized day; metadata set |
| Logo | Bundled asset loads; `--logo` override; `--no-logo`; using both is a usage error; an unreadable logo writes nothing |
| CLI | Default formats and output directory; `--format` subset; zero-padded PNG names; `--strict` writes nothing on warnings; render error writes nothing; temporary files cleaned; `output_written` lines; exit codes |
| Decoupling | `import plannings_tool` does not load matplotlib or ReportLab |
| Regression | All phase 1-3 tests pass |

Visually inspect the PDF and PNGs for both Kleppen examples, a three-trainer
session, an overrunning day, and an undated day.

## Definition of done

1. `plan render` writes the PDF, per-day transparent Gantt PNGs, and the
   Markdown timetable, as specified, from the phase-3 result.
2. The PDF and PNG charts come from one figure builder and show the same
   content.
3. Trainer colors and stripes are consistent across days and outputs.
4. Strict mode and render errors write nothing. Each destination is replaced
   atomically; a write failure reports any files already replaced and cleans up
   temporary files.
5. The bundled logo is present and packaged, with working override and opt-out.
6. The README and implementation plan are updated, and the full test, lint,
   format, and type checks pass.
