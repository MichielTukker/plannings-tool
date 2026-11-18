# plannings-tool

A Python course-planning tool. Phases 1 and 2 provide YAML validation, JSON
Schema export, and a sequential schedule preview. Phase 3 adds automatic
breaks and session splitting. Phase 4 renders schedules as a PDF, transparent
per-day Gantt PNGs, and a Markdown timetable.

## Install and run

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run plan validate testdata\Kleppen-cursus-2026.yaml
uv run plan schedule testdata\Kleppen-cursus-2026.yaml
uv run plan schema --output course.schema.json
uv run plan --help
```

The CLI uses Typer for commands and Rich for terminal formatting. In
diagnostics, the severity is red for errors, orange for warnings, and green for
info; the file location is cyan and the message uses the default terminal
color. Redirected output remains readable without color. Validation prints a
short confirmation on success. Diagnostics use
`[ERROR] - filename:source - message` or
`[WARNING] - filename:source - message` on stderr. The source is the YAML line
for syntax errors. Other diagnostics do not have line numbers yet, so they show
a 1-based location such as `day 1|entry 3|duration`, `trainer 2|email`, or
`course|timezone`; entries count sessions and breaks. `~` marks a file-level
problem without a location.

Exit codes are `0` for success, `1` for input/file errors, and `2` for invalid
command-line arguments. Expected failures do not print tracebacks. Schema
exports replace their destination atomically.

`plan schedule` previews sequential timings with Rich tables. It supports both
manual and automatic break modes; automatic breaks are placed at nearby session
boundaries or split eligible sessions. Warnings and informational split/omission
diagnostics are printed on stderr. Warnings do not change the successful exit
status; `--strict` treats warnings as failures and suppresses the preview.

Manual-mode days longer than four hours with no breaks produce a
`missing_manual_breaks` warning on stderr, using inherited or overridden day
times. Warnings do not reject the course or change the successful exit code.

## Rendering

Render all formats into the current directory or choose an output directory:

```powershell
uv run plan render testdata\Kleppen-cursus-2026.yaml --output-dir output
```

The command writes `<stem>.pdf`, `<stem>.md`, and one transparent
`<stem>-day-<n>.png` per course day. PNG day numbers are zero-padded when there
are ten or more days. Select formats by repeating `--format` with `pdf`, `png`,
or `md`; omitting it writes all formats. The bundled Deltares logo is used for
PDF and PNG output. Use `--logo path\to\logo.png` to override it or `--no-logo`
to omit it. Scheduling warnings are printed to stderr and included in the PDF
and Markdown; `--strict` treats warnings as failures and writes no files.
Rendering completes before any files are written, and each output is replaced
atomically.

### Markdown templates

The Markdown timetable is rendered from the bundled Jinja2 template
[src/plannings_tool/templates/timetable.md.j2](src/plannings_tool/templates/timetable.md.j2).
Pass `--template path\to\custom.md.j2` with Markdown output selected to use a
UTF-8 template of your own; it is an error to combine `--template` with only
`pdf` or `png` formats. Templates run in a Jinja2 sandbox, undefined variables
are errors, and output is not HTML-escaped. Trailing whitespace is trimmed and
the file ends with one newline. Templates receive:

| Variable | Fields |
|---|---|
| `course` | `title`, `description`, `project_id`, `timezone` |
| `warnings` | Course-level warning messages |
| `days` | Each has `label`, `date` (ISO date or none for floating days), `location`, `start`, `end` (`HH:MM`), `entries`, and `warnings` |
| `day.entries` | Each has `kind` (`session` or `break`), `start`, `end`, `duration`, `title` (with part label), `type` (none for breaks), and `trainers` (names) |

The `md_cell` filter escapes `\` and `|` and replaces line breaks with spaces,
for use in table cells and list items.

## Course YAML

See [testdata/Kleppen-cursus-2026.yaml](testdata/Kleppen-cursus-2026.yaml) for a
complete two-day example.
A minimal course is:

```yaml
schema_version: 1
course:
  title: Hydraulics course
  start: "09:00"
  end: "17:00"
trainers:
  - id: alex
    name: Alex Example
    email: alex@example.org
days:
  - label: Day 1
    schedule:
      - title: Introduction
        type: Presentation
        duration: 30m
        trainers: [alex]
```

The original single-day input format is not supported.

### Fields and defaults

- `schema_version` must be integer `1`.
- `course` requires `title`, `start`, and `end`. Optional `description` and
  `project_id` must be nonblank strings when supplied.
  Optional `timezone` is an IANA timezone name and defaults to
  `Europe/Amsterdam`; it applies to all dated days in the course.
- YAML date and time values remain quoted strings, but the Python model parses
  dates and clock times to `datetime.date` and `datetime.time`.
- `trainers` is a nonempty list. Each trainer requires `id`, `name` (full display
  name), and `email`; `color` is an optional quoted `"#RRGGBB"` value. IDs match
  `[a-z][a-z0-9_-]*` and must be unique. Email syntax is checked offline, without
  checking deliverability; the input address is preserved.
- `defaults` is optional and may contain `coffee_break` (default `15m`), `lunch`
  (default `45m`), and `break_mode` (default `manual`).
- `days` is a nonempty ordered list. Each day requires a nonblank unique `label`
  and nonempty `schedule`. Optional `location` must be a nonblank string when
  supplied. Optional `start` and `end` independently override course times.
  Optional `break_mode` overrides the shared default.
- Day `date` is optional: use a quoted real ISO date such as `"2027-03-01"`.
  Supplied dates must be unique and strictly increasing, ignoring undated days.
- Unknown fields and explicit `null` values are rejected. Omit optional fields
  instead. Supplied text is not trimmed; whitespace-only text is invalid.

Use quoted `HH:MM` times from `"00:00"` to `"23:59"`. Course and effective day
end times must be later than start times; overnight days are not supported.
Quote dates as well: YAML-native date values are rejected.

### Sessions and breaks

Each session requires `title`, `type`, `duration`, and a nonempty `trainers` list.
Trainer references must exist and must not repeat within one session. Trainers
may teach multiple sessions; multiple trainers may share one session. Unused
trainers are allowed.

| Type | Duration | Minimum | Automatic-break behavior |
|---|---|---:|---|
| `Presentation` | Fixed | 15 min | Splittable |
| `Exercise` | Fixed | 15 min | Splittable |
| `Excursion` | Fixed | 30 min | Not splittable |
| `Interactive` | `remaining` | Allocated later, minimum 15 min | Not splittable |

Types are case-sensitive and built into the tool. At most one Interactive
session is allowed per day, anywhere in the sequence. Its duration must be the
literal `remaining`; fixed types cannot use that value.

Durations accept positive integer minutes/hours: `15m`, `90m`, `1h`, or
`1h 15m`. Combined minutes must be 1-59 with exactly one separating space.
Numbers, fractions, negatives, zero, leading zeros, uppercase units, surrounding
whitespace, `1h15m`, and `1h 0m` are rejected.

In manual mode, insert breaks in sequence:

```yaml
- break: coffee
- break: lunch
  duration: 30m
```

Only coffee and lunch are supported. Omitted durations use the corresponding
default; overrides need only be positive. Breaks have no trainers. Repeated
breaks, breaks at the start/end, and all-break days are valid.

`automatic` break mode rejects all manual break entries. It inserts morning
coffee at 10:30 (±30 minutes), lunch at 12:15 (±15 minutes), and afternoon
coffee at 15:00 (±30 minutes), using the configured default break durations.
The nearest boundary between sessions is preferred. A `Presentation` or
`Exercise` may be split when no boundary fits; each fragment is at least
15 minutes. A break is omitted with an info diagnostic when provisional
activities end by its preferred start; omitted breaks stay omitted after
Interactive time is reallocated. An impossible placement is a scheduling
error. See [PHASE-3-PLAN.md](doc/PHASE-3-PLAN.md) for the full rules.

Automatic scheduling does not move or resize fixed sessions. It reports
overflow and unused time as warnings.

Dated days resolve local wall-clock times in the course timezone. Nonexistent
and ambiguous daylight-saving times, and days crossing an offset transition,
are rejected. Undated days remain floating local-clock schedules without
timezone-aware instants. Activity and break lengths are elapsed minutes.

YAML must contain exactly one mapping document. Duplicate keys, including
collisions introduced by merge mappings, and unsafe object tags are rejected.

## Editor schema

`course.schema.json` is generated from the Pydantic models. For YAML Language
Server-compatible editors, add a directive with a path relative to your YAML:

```yaml
# yaml-language-server: $schema=../course.schema.json
```

Regenerate after model changes:

```powershell
uv run plan schema --output course.schema.json
```

The schema describes structure, scalar syntax, and session variants. Python
validation additionally checks duration minimums, calendar validity, trainer
references, uniqueness, date/time ordering, the Interactive limit, and effective
break mode. Editor email-format checks depend on editor support. Always run
`plan validate`; editor validation alone is not sufficient.

## Python API

```python
from plannings_tool import (
    CourseValidationError,
    PlanningError,
    SchedulingError,
    generate_schema,
    load_course,
    parse_course,
    schedule_course,
)

course = load_course("testdata\\Kleppen-cursus-2026.yaml")
day = course.days[0]
start = day.effective_start(course.course)
start_minutes = start.hour * 60 + start.minute
schema = generate_schema()
schedule = schedule_course(course)
```

`parse_course(text)` validates YAML without file I/O. Both loading functions
raise `CourseValidationError` with a `diagnostics` tuple.

All reported problems share one `Diagnostic` type. It has:

- `severity`: a `Severity` value (`error`, `warning`, or `info`);
- `code`: a stable code;
- `location`: a structured path such as `("days", 0, "schedule", 2)`;
- `message`: the readable text;
- `line` and `column`: set for YAML syntax and duplicate-key errors.

`CourseValidationError` and `SchedulingError` both derive from `PlanningError`.
Catch `PlanningError` to handle either one through its `diagnostics`. Schedule
results expose warning-only `warnings` and informational `infos`. These
types live in `plannings_tool.diagnostics`, which also provides
`display_location()`. It renders a location such as `day 1|entry 3`, as the
CLI does.

`ScheduleResult` also carries the validated course definition for renderers.
The rendering API is in `plannings_tool.render`:

```python
from plannings_tool.render import (
    RenderError,
    load_default_logo,
    load_markdown_template,
    render_gantt_png,
    render_markdown,
    render_pdf,
)

pdf_bytes = render_pdf(schedule, logo=load_default_logo())
png_bytes = render_gantt_png(schedule, day_index=0, logo=None)
markdown_text = render_markdown(schedule)
custom_text = render_markdown(
    schedule, template=load_markdown_template(Path("custom.md.j2"))
)
```

`RenderError` derives from `PlanningError` and reports layout, logo, template,
rendering, or output diagnostics.

Use `check_missing_manual_breaks(course)` to obtain advisory warning
diagnostics for long manual-mode days without breaks, separately after loading
or parsing. The loading functions do not emit warnings themselves.

`SessionEntry` is the exported type union for the four session variants;
breaks are schedule entries, but are not session entries.

## Development

```powershell
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

`schedule_course` returns a `ScheduleResult`. Scheduled fragments retain their
source session and schedule index and expose `part`, `parts`, and `part_label`;
automatically inserted breaks have `schedule_index is None` and
`is_automatic=True`. It raises `SchedulingError` for impossible break
placements and timezone resolution failures.

Tests check that the committed schema matches model generation. See
[IMPLEMENTATION-PLAN.md](doc/IMPLEMENTATION-PLAN.md),
[PHASE-1-PLAN.md](doc/PHASE-1-PLAN.md),
[PHASE-2-PLAN.md](doc/PHASE-2-PLAN.md),
[PHASE-3-PLAN.md](doc/PHASE-3-PLAN.md) and
[PHASE-4-PLAN.md](doc/PHASE-4-PLAN.md) for scope and acceptance criteria.
