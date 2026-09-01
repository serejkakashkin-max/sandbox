# Incident Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the TA card dashboard with the approved compact table dashboard, Friday-based periods, five-protocol repeat review, and global ID/executor search while preserving the deterministic audit, manual AI, protocols, export, and security behavior.

**Architecture:** Keep Flask and the audit engine intact. Add focused pure-Python modules for period/presenter logic and protocol-history selection, then make `app.py` coordinate them and render a server-side table. Existing AI and protocol routes remain authoritative; JavaScript only handles presentation behavior and never starts AI without a click.

**Tech Stack:** Python 3, Flask, pandas/openpyxl, SQLite, Jinja2, Bootstrap 5, vanilla JavaScript, `unittest`.

**Spec:** `sandbox/TA/docs/superpowers/specs/2026-08-31-incident-dashboard-redesign-design.md`

## Global Constraints

- Modify only `sandbox/TA`; do not modify `sandbox/CA`, `sandbox/GD`, or shared stand files.
- Do not change the AI prompt, response schema, model selection, or manual-only request boundary.
- Do not add mobile-specific UI, pagination, vector storage, or ideal-example features.
- Preserve all CSRF, upload archive, row/column, path, atomic-write, cookie, and mTLS protections.
- Use `Создан` for period grouping; unparseable dates remain auditable and appear in `Дата не определена`.
- Friday periods use Europe/Moscow and `[start, end)` boundaries.
- Repeat review uses five unique report weeks, manual protocol entries only, and ignores automatic `В работе` lines.
- The current workspace is not a Git repository. Before edits, create a rollback snapshot of the exact TA files being changed. Git commit commands below are used only if execution later occurs inside the real repository.

---

### Task 1: Establish a rollback point and clean baseline

**Files:**
- Read: `sandbox/TA/audit_engine.py`
- Read: `sandbox/TA/app.py`
- Read: `sandbox/TA/db.py`
- Read: `sandbox/TA/templates/base.html`
- Read: `sandbox/TA/templates/index.html`
- Read: `sandbox/TA/static/css/app.css`
- Read: `sandbox/TA/static/js/ai-analysis.js`
- Create outside TA: `backups/TA-dashboard-20260831/`

**Interfaces:**
- Consumes: current TA working copy.
- Produces: restorable copies of every file modified by later tasks and a recorded passing baseline.

- [ ] **Step 1: Copy only the files that later tasks will modify**

```powershell
$backup = 'C:\Users\dim13\incident_manager\backups\TA-dashboard-20260831'
New-Item -ItemType Directory -Force "$backup\templates", "$backup\static\css", "$backup\static\js" | Out-Null
Copy-Item -LiteralPath 'sandbox\TA\audit_engine.py','sandbox\TA\app.py','sandbox\TA\db.py' -Destination $backup
Copy-Item -LiteralPath 'sandbox\TA\templates\base.html','sandbox\TA\templates\index.html' -Destination "$backup\templates"
Copy-Item -LiteralPath 'sandbox\TA\static\css\app.css' -Destination "$backup\static\css"
Copy-Item -LiteralPath 'sandbox\TA\static\js\ai-analysis.js' -Destination "$backup\static\js"
```

- [ ] **Step 2: Run the current business-rule suite**

```powershell
$env:PYTHONPATH = 'C:\Users\dim13\incident_manager\sandbox'
$taPython = 'C:\Users\dim13\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taPython -m unittest TA.tests.test_business_rules -v
```

Expected: six current tests pass with zero failures.

- [ ] **Step 3: Compile the current modules**

```powershell
& $taPython -m py_compile sandbox\TA\app.py sandbox\TA\db.py sandbox\TA\audit_engine.py sandbox\TA\ai_analysis.py sandbox\TA\gigachat_helper.py
```

Expected: exit code `0` and no output.

- [ ] **Step 4: Record the no-Git checkpoint**

```powershell
git status --short
```

Expected in this copy: `fatal: not a git repository`. Do not initialize Git or modify another repository without user authorization.

---

### Task 2: Downgrade missing chronology heading to a warning

**Files:**
- Modify: `sandbox/TA/tests/test_business_rules.py`
- Modify: `sandbox/TA/audit_engine.py:850-860`

**Interfaces:**
- Consumes: `audit_incident(incident: Mapping[str, Any]) -> dict[str, Any]`.
- Produces: `CHRONOLOGY_REQUIRED` with `severity == "warning"` when the heading is absent.

- [ ] **Step 1: Write the failing test**

Add to `test_business_rules.py`:

```python
class ChronologySeverityTests(TestCase):
    def test_missing_chronology_heading_is_warning(self):
        incident = WorkaroundTaskRuleTests()._incident(
            "создана задача TEAMX-771"
        )
        incident["Код закрытия"] = "Выполнено"
        incident["Решение"] = incident["Решение"].replace(
            "Краткая хронология:\n", ""
        )

        result = audit_incident(incident)
        check = check_by_id(result, "CHRONOLOGY_REQUIRED")

        self.assertIsNotNone(check)
        self.assertEqual(check["status"], "remark")
        self.assertEqual(check["severity"], "warning")
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_business_rules.ChronologySeverityTests -v
```

Expected: FAIL because current severity is `error`.

- [ ] **Step 3: Implement the minimal change**

Pass the existing `_required_check` argument:

```python
_required_check(
    parsed.chronology_present,
    "CHRONOLOGY_REQUIRED",
    "Хронология",
    "Заголовок хронологии отсутствует",
    "Добавьте раздел «Краткая хронология».",
    missing_severity="warning",
)
```

- [ ] **Step 4: Run the focused and existing tests**

```powershell
& $taPython -m unittest TA.tests.test_business_rules -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/audit_engine.py sandbox/TA/tests/test_business_rules.py
git commit -m "fix: treat missing chronology heading as warning"
```

Skip only in the current non-Git copy.

---

### Task 3: Add pure Friday-period calculations

**Files:**
- Create: `sandbox/TA/dashboard_view.py`
- Create: `sandbox/TA/tests/test_dashboard_periods.py`

**Interfaces:**
- Consumes: incident mappings and the upload timestamp.
- Produces:
  - `parse_created(value: Any) -> datetime | None`
  - `friday_period_start(value: datetime) -> datetime`
  - `build_periods(incidents: Sequence[Mapping[str, Any]], loaded_at: datetime) -> PeriodCollection`
  - `PeriodView(key, label, start, end, incidents, is_current, is_unknown)`

- [ ] **Step 1: Write failing parsing and bucket tests**

Create `test_dashboard_periods.py` with:

```python
from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from TA.dashboard_view import build_periods, friday_period_start, parse_created

MSK = ZoneInfo("Europe/Moscow")


class CreatedDateTests(TestCase):
    def test_parses_supported_day_first_and_iso_values(self):
        self.assertEqual(
            parse_created("28.08.2026 08:17"),
            datetime(2026, 8, 28, 8, 17, tzinfo=MSK),
        )
        self.assertEqual(
            parse_created("2026-08-28 08:17:00"),
            datetime(2026, 8, 28, 8, 17, tzinfo=MSK),
        )

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(parse_created("нет даты"))

    def test_friday_boundary_is_inclusive(self):
        moment = datetime(2026, 8, 28, 0, 0, tzinfo=MSK)
        self.assertEqual(friday_period_start(moment), moment)


class PeriodBuildTests(TestCase):
    def test_friday_upload_builds_current_from_previous_friday(self):
        incidents = [
            {"ID инцидента": "A", "Создан": "21.08.2026 00:00"},
            {"ID инцидента": "B", "Создан": "28.08.2026 08:17"},
            {"ID инцидента": "C", "Создан": "20.08.2026 23:59"},
        ]
        result = build_periods(
            incidents,
            datetime(2026, 8, 28, 9, 0, tzinfo=MSK),
        )
        self.assertEqual([row["ID инцидента"] for row in result.current.incidents], ["A", "B"])
        self.assertEqual(result.current.start, datetime(2026, 8, 21, 0, 0, tzinfo=MSK))
        self.assertEqual(result.current.end, datetime(2026, 8, 28, 8, 17, tzinfo=MSK))

    def test_non_friday_upload_has_no_current_period(self):
        result = build_periods(
            [{"ID инцидента": "A", "Создан": "10.06.2026 12:00"}],
            datetime(2026, 8, 26, 9, 0, tzinfo=MSK),
        )
        self.assertIsNone(result.current)
        self.assertEqual(len(result.weeks), 1)

    def test_invalid_date_stays_in_unknown_period(self):
        result = build_periods(
            [{"ID инцидента": "A", "Создан": "нет даты"}],
            datetime(2026, 8, 26, 9, 0, tzinfo=MSK),
        )
        self.assertEqual(result.unknown.label, "Дата не определена")
        self.assertEqual(result.unknown.incidents[0]["ID инцидента"], "A")
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_periods -v
```

Expected: import failure because `TA.dashboard_view` does not exist.

- [ ] **Step 3: Implement minimal period types and functions**

Use dataclasses and standard-library parsing:

```python
@dataclass(frozen=True)
class PeriodView:
    key: str
    label: str
    start: datetime | None
    end: datetime | None
    incidents: tuple[Mapping[str, Any], ...]
    is_current: bool = False
    is_unknown: bool = False


@dataclass(frozen=True)
class PeriodCollection:
    current: PeriodView | None
    weeks: tuple[PeriodView, ...]
    unknown: PeriodView | None
```

`parse_created` must accept `datetime`, pandas-like objects exposing `to_pydatetime`, and the exact string formats covered by tests. It must return a Europe/Moscow-aware value.

`build_periods` must keep original incident mappings, preserve input order within each period, and sort weekly periods by start descending.

- [ ] **Step 4: Run period tests**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_periods -v
```

Expected: all period tests pass.

- [ ] **Step 5: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/dashboard_view.py sandbox/TA/tests/test_dashboard_periods.py
git commit -m "feat: add friday reporting periods"
```

---

### Task 4: Select five unique protocol weeks and build repeat groups

**Files:**
- Create: `sandbox/TA/protocol_history.py`
- Create: `sandbox/TA/tests/test_repeat_protocols.py`
- Modify: `sandbox/TA/db.py`

**Interfaces:**
- Consumes: existing protocol `.txt` files, `incident_history` rows, current incidents.
- Produces:
  - `select_recent_protocols(files: Sequence[Path], limit: int = 5) -> tuple[ProtocolRef, ...]`
  - `parse_manual_protocol_entries(text: str, protocol_file: str) -> tuple[dict, ...]`
  - `build_repeat_groups(current_incidents, protocol_refs, rows_by_protocol) -> tuple[RepeatProtocolGroup, ...]`
  - `get_history_for_protocols(protocol_files: Sequence[str]) -> dict[str, list[dict]]`

- [ ] **Step 1: Write failing protocol-selection tests**

Create `test_repeat_protocols.py`:

```python
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from TA.protocol_history import (
    ProtocolRef,
    build_repeat_groups,
    parse_manual_protocol_entries,
    select_recent_protocols,
)

MSK = ZoneInfo("Europe/Moscow")


class ProtocolSelectionTests(TestCase):
    def test_newest_file_wins_inside_same_friday_week(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "28.08.2026.txt"
            newer = root / "29.08.2026.txt"
            previous = root / "21.08.2026.txt"
            for path in (older, newer, previous):
                path.write_text("Протокол", encoding="utf-8")
            os.utime(previous, (1_000, 1_000))
            os.utime(older, (2_000, 2_000))
            os.utime(newer, (3_000, 3_000))
            refs = select_recent_protocols((newer, older, previous), limit=5)
            self.assertEqual([ref.path.name for ref in refs], ["29.08.2026.txt", "21.08.2026.txt"])

    def test_text_fallback_stops_before_in_work_section(self):
        text = """1. INC0001 — Иванов И.И. — WARNING1
   Комментарий: уточнить причину

В работе — переносятся на следующую неделю:
2. INC0002 — Петров П.П. — В РАБОТЕ"""
        rows = parse_manual_protocol_entries(text, "28.08.2026.txt")
        self.assertEqual([row["incident_id"] for row in rows], ["INC0001"])
        self.assertEqual(rows[0]["tag"], "WARNING1")
        self.assertEqual(rows[0]["comment"], "уточнить причину")


class RepeatGroupingTests(TestCase):
    @staticmethod
    def protocol_refs(*names):
        return tuple(
            ProtocolRef(
                path=Path(name),
                report_week=datetime.strptime(name[:10], "%d.%m.%Y").replace(tzinfo=MSK),
                sort_time=float(len(names) - index),
            )
            for index, name in enumerate(names)
        )

    def test_incident_appears_under_newest_protocol_only(self):
        current = [{"ID инцидента": "inc0001"}]
        refs = self.protocol_refs("28.08.2026.txt", "21.08.2026.txt")
        rows = {
            "28.08.2026.txt": [{"incident_id": "INC0001", "comment": "новый"}],
            "21.08.2026.txt": [{"incident_id": "INC0001", "comment": "старый"}],
        }
        groups = build_repeat_groups(current, refs, rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].protocol.filename, "28.08.2026.txt")
        self.assertEqual(len(groups[0].incidents[0].history), 2)
```

The production change that makes the test pass is the new public `ProtocolRef` dataclass.

- [ ] **Step 2: Run tests and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_repeat_protocols -v
```

Expected: import failure because `TA.protocol_history` does not exist.

- [ ] **Step 3: Implement protocol parsing and grouping**

Use immutable dataclasses:

```python
@dataclass(frozen=True)
class ProtocolRef:
    path: Path
    report_week: datetime
    sort_time: float

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class RepeatIncident:
    incident: Mapping[str, Any]
    history: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RepeatProtocolGroup:
    protocol: ProtocolRef
    incidents: tuple[RepeatIncident, ...]
```

Parse dates from `DD.MM.YYYY.txt` and `DD.MM.YYYY (N).txt`. Use file modification time only as a fallback and as the within-week newest selector. Normalize incident IDs with `strip().upper()`.

- [ ] **Step 4: Add the batched SQLite query**

Add to `db.py`:

```python
def get_history_for_protocols(protocol_files):
    names = tuple(dict.fromkeys(str(name) for name in protocol_files if name))
    if not names:
        return {}
    placeholders = ",".join("?" for _ in names)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM incident_history WHERE protocol_file IN ({placeholders}) "
            "ORDER BY created_at DESC, id DESC",
            names,
        ).fetchall()
        grouped = {name: [] for name in names}
        for row in rows:
            grouped.setdefault(row["protocol_file"], []).append(dict(row))
        return grouped
    finally:
        conn.close()
```

- [ ] **Step 5: Run repeat tests**

```powershell
& $taPython -m unittest TA.tests.test_repeat_protocols -v
```

Expected: all repeat tests pass and automatic `В работе` entry is absent.

- [ ] **Step 6: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/protocol_history.py sandbox/TA/db.py sandbox/TA/tests/test_repeat_protocols.py
git commit -m "feat: add five-week repeat review"
```

---

### Task 5: Build status groups, defaults, metrics, and global search

**Files:**
- Modify: `sandbox/TA/dashboard_view.py`
- Create: `sandbox/TA/tests/test_dashboard_presenter.py`

**Interfaces:**
- Consumes: analyzed incident rows, `PeriodCollection`, repeat groups, requested query values.
- Produces:
  - `status_group_key(profile: str, outcome: str) -> str`
  - `choose_default_status(groups: Mapping[str, Sequence]) -> str`
  - `choose_default_section(repeat_groups, periods) -> str`
  - `global_search(rows, query: str) -> tuple`
  - `build_metrics(rows, repeated_ids) -> dict[str, int]`

- [ ] **Step 1: Write failing presenter tests**

```python
from unittest import TestCase

from TA.dashboard_view import (
    build_metrics,
    choose_default_section,
    choose_default_status,
    global_search,
    status_group_key,
)


class StatusGroupingTests(TestCase):
    def test_in_work_is_grouped_with_skipped(self):
        self.assertEqual(status_group_key("in_work", "skipped"), "skipped")

    def test_default_status_prefers_errors_then_warnings_then_all(self):
        self.assertEqual(choose_default_status({"errors": [1], "warnings": [2], "all": [1, 2]}), "errors")
        self.assertEqual(choose_default_status({"errors": [], "warnings": [2], "all": [2]}), "warnings")
        self.assertEqual(choose_default_status({"errors": [], "warnings": [], "all": [3]}), "all")


class GlobalSearchTests(TestCase):
    def test_search_ignores_active_period_and_matches_only_id_or_executor(self):
        rows = (
            {"ID инцидента": "INC001", "Исполнитель": "Иванов И.И.", "Описание": "другая строка"},
            {"ID инцидента": "INC002", "Исполнитель": "Петров П.П.", "Описание": "Иванов"},
        )
        self.assertEqual([row["ID инцидента"] for row in global_search(rows, "иванов")], ["INC001"])


class MetricsTests(TestCase):
    @staticmethod
    def rows_for_all_outcomes():
        return (
            {"incident_id": "INC001", "profile": "manual", "analysis": {"outcome": "error"}},
            {"incident_id": "INC002", "profile": "manual", "analysis": {"outcome": "warning"}},
            {"incident_id": "INC003", "profile": "manual", "analysis": {"outcome": "passed"}},
            {"incident_id": "INC004", "profile": "in_work", "analysis": {"outcome": "skipped"}},
        )

    def test_primary_categories_sum_to_total(self):
        rows = self.rows_for_all_outcomes()
        metrics = build_metrics(rows, {"INC001"})
        self.assertEqual(metrics["total"], metrics["errors"] + metrics["warnings"] + metrics["correct"] + metrics["skipped"])
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_presenter -v
```

Expected: missing functions.

- [ ] **Step 3: Implement the pure presenter helpers**

Keep these helpers independent of Flask request/session globals. Search with `casefold()`. Count `in_work` exactly once inside `skipped`. `repeated` is an overlapping counter and is not included in the primary-category sum.

- [ ] **Step 4: Run presenter and period tests**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_presenter TA.tests.test_dashboard_periods -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/dashboard_view.py sandbox/TA/tests/test_dashboard_presenter.py
git commit -m "feat: add dashboard presenter logic"
```

---

### Task 6: Integrate the presenter into Flask without changing AI behavior

**Files:**
- Modify: `sandbox/TA/app.py:1-780`
- Modify: `sandbox/TA/db.py` imports/use
- Create: `sandbox/TA/tests/test_dashboard_route.py`

**Interfaces:**
- Consumes: Tasks 3-5 public functions and dataclasses.
- Produces: `GET /` context for normal, repeat, and search modes; upload metadata; stable query parameters.

- [ ] **Step 1: Write a failing route-context test**

Use Flask's test client with patched, read-only dependencies:

```python
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from TA import app as app_module


class DashboardRouteTests(TestCase):
    def setUp(self):
        self.previous = list(app_module.incidents)
        self.previous_upload = dict(app_module.upload_state)
        app_module.app.config.update(TESTING=True)

    def tearDown(self):
        app_module.incidents[:] = self.previous
        app_module.upload_state.clear()
        app_module.upload_state.update(self.previous_upload)

    @staticmethod
    def incident(incident_id, executor, created):
        return {
            "ID инцидента": incident_id,
            "Статус": "Закрыт",
            "Код закрытия": "Выполнено",
            "Тип стенда": "ПРОМ",
            "Фактическое время возникновения": created,
            "Создан": created,
            "Фактическое время окончания": created,
            "Влияние на клиентский сервисе": "Нет",
            "Причина": "Техническая причина",
            "Тема инцидента": "Проверка",
            "Описание": "Кратковременная недоступность сервиса",
            "Исполнитель": executor,
            "Решение": (
                "Время начала инцидента: 10:00 28.08.2026\n"
                "Время окончания инцидента: 10:10 28.08.2026\n"
                "Причина: ошибка конфигурации\n"
                "Влияние отсутствует\n"
                "Краткая хронология:\n"
                "10:00 — обнаружено\n10:10 — восстановлено"
            ),
        }

    def test_global_search_returns_match_from_old_period(self):
        app_module.incidents[:] = [
            self.incident("INC001", "Иванов И.И.", "28.08.2026 08:00"),
            self.incident("INC002", "Петров П.П.", "10.07.2026 08:00"),
        ]
        app_module.upload_state.update({
            "filename": "test.xlsx",
            "loaded_at": datetime(2026, 8, 28, 9, 0, tzinfo=ZoneInfo("Europe/Moscow")),
        })
        with patch.object(app_module, "_list_protocol_files", return_value=[]), patch.object(
            app_module, "get_history_for_protocols", return_value={}
        ):
            response = app_module.app.test_client().get("/?search=Петров")
        self.assertEqual(response.status_code, 200)
        self.assertIn("INC002", response.get_data(as_text=True))
```

- [ ] **Step 2: Run the route test and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_route -v
```

Expected: failure because `upload_state` and the new context do not exist.

- [ ] **Step 3: Add upload metadata under the existing lock**

Add:

```python
upload_state = {"filename": "", "loaded_at": None}
```

On successful upload, atomically replace `incidents` and update `upload_state` with the safe filename and `datetime.now(ZoneInfo("Europe/Moscow"))`. On clear, reset both. Do not update state until XLSX validation and atomic file replacement succeed.

- [ ] **Step 4: Replace legacy filters with presenter inputs**

Accepted query parameters:

```text
section=repeat|current|period|all
period=YYYY-MM-DD|unknown
tab=errors|warnings|all|correct|skipped
search=<ID or executor substring>
```

Ignore legacy `date_from`, `date_to`, `executor`, and `only_repeated` safely. Preserve `remarks -> warnings` and `test -> skipped` aliases.

- [ ] **Step 5: Load repeat history in one batch**

Use `_list_protocol_files()` to resolve existing files, `select_recent_protocols`, `get_history_for_protocols`, and the text fallback only when a selected file has no SQLite rows. Never include parsed lines after the `В работе` marker.

- [ ] **Step 6: Keep AI state lookup request-free**

Build AI cache keys for prepared rows using `build_ai_context` and call only `get_ai_states`. Do not call `GigaChatHelper` from `index` or presenter code.

- [ ] **Step 7: Run route and all unit tests**

```powershell
& $taPython -m unittest discover -s sandbox\TA\tests -t sandbox -v
```

Expected: all tests pass, and no GigaChat request occurs.

- [ ] **Step 8: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/app.py sandbox/TA/db.py sandbox/TA/tests/test_dashboard_route.py
git commit -m "feat: serve period and repeat dashboard data"
```

---

### Task 7: Replace the card grid with the approved server-rendered table

**Files:**
- Modify: `sandbox/TA/templates/base.html`
- Replace: `sandbox/TA/templates/index.html`
- Create: `sandbox/TA/tests/test_dashboard_template.py`

**Interfaces:**
- Consumes: the context produced by Task 6.
- Produces: approved normal, repeat, and search HTML states.

- [ ] **Step 1: Write failing template assertions**

```python
class DashboardTemplateTests(TestCase):
    def test_dashboard_has_short_status_labels_and_no_legacy_filter(self):
        html = self.get_dashboard_html()
        self.assertIn("На доработку", html)
        self.assertIn("Замечания", html)
        self.assertIn("Не проверяются", html)
        self.assertNotIn("Только проверявшиеся ранее", html)
        self.assertNotIn("tab=inwork", html)
        self.assertNotIn("Дата с", html)

    def test_ai_control_remains_explicit(self):
        html = self.get_dashboard_html()
        self.assertIn("ai-action", html)
        self.assertIn("data-ai-url", html)
        self.assertNotIn("data-ai-autostart", html)
```

`get_dashboard_html` uses the Flask test client and the same safe fixture as Task 6.

- [ ] **Step 2: Run the template tests and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_template -v
```

Expected: FAIL because the legacy filter and card markup remain.

- [ ] **Step 3: Implement compact top actions and metrics**

Use existing POST routes and CSRF fields. The upload button may wrap a hidden `.xlsx` input with explicit submit behavior. Place export, protocol history, frequent signs, current selection, copy link, and destructive actions in accessible Bootstrap dropdowns/modals.

- [ ] **Step 4: Implement period and status navigation**

Render links with `section`, `period`, `tab`, and `search` as appropriate. Repeat mode renders no status tabs. Mark active links with `aria-current="page"`.

- [ ] **Step 5: Implement normal and search tables**

Use semantic `<table>`, `<thead>`, and `<tbody>`. Keep ID and chevron as real detail links. Render one primary issue. Render existing AI states and data attributes unchanged so `ai-analysis.js` continues to work.

- [ ] **Step 6: Implement repeat groups and inline history**

Use semantic `<details>` for each protocol occurrence so disclosure works without JavaScript. The summary is the protocol date/tag. The body contains comment and protocol link.

- [ ] **Step 7: Run template and unit tests**

```powershell
& $taPython -m unittest discover -s sandbox\TA\tests -t sandbox -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/templates/base.html sandbox/TA/templates/index.html sandbox/TA/tests/test_dashboard_template.py
git commit -m "feat: render compact incident dashboard"
```

---

### Task 8: Add dashboard styling and progressive enhancement

**Files:**
- Modify: `sandbox/TA/static/css/app.css`
- Create: `sandbox/TA/static/js/dashboard.js`
- Modify: `sandbox/TA/templates/base.html`
- Modify: `sandbox/TA/templates/index.html`

**Interfaces:**
- Consumes: semantic HTML from Task 7.
- Produces: dark/light approved layout, pinned active period, no horizontal period scrolling, row click behavior, and existing bulk selection behavior.

- [ ] **Step 1: Add a static script contract test before production JavaScript**

Add to `test_dashboard_template.py`:

```python
def test_dashboard_loads_progressive_enhancement_script(self):
    html = self.get_dashboard_html()
    self.assertIn("js/dashboard.js", html)
    self.assertIn("data-period-navigation", html)
    self.assertIn("data-incident-row", html)
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
& $taPython -m unittest TA.tests.test_dashboard_template.DashboardTemplateTests.test_dashboard_loads_progressive_enhancement_script -v
```

Expected: FAIL because the script and data hooks do not exist.

- [ ] **Step 3: Add semantic data hooks and load `dashboard.js` with `defer`**

The script must:

- keep active period visible;
- move only overflowing week links into «Ещё»;
- preserve repeat/current/all controls;
- never perform network requests for AI;
- ignore clicks originating from `a`, `button`, `input`, `select`, `textarea`, `summary`, or `details` when applying row navigation;
- rebuild the existing bulk bar from checked values.

- [ ] **Step 4: Replace card CSS with scoped dashboard CSS**

Keep shared detail/protocol/AI classes. Remove only selectors exclusively used by the deleted main card grid after confirming no other template references them with:

```powershell
rg -n "incident-card|kpi-card|filter-surface|upload-surface" sandbox\TA\templates sandbox\TA\static\js
```

Use CSS variables for both themes, visible focus rings, sticky table headers only inside the page flow, and no horizontal scrollbar on the period navigation.

- [ ] **Step 5: Run template tests and compile Python**

```powershell
& $taPython -m unittest discover -s sandbox\TA\tests -t sandbox -v
& $taPython -m py_compile sandbox\TA\app.py sandbox\TA\dashboard_view.py sandbox\TA\protocol_history.py
```

Expected: tests pass and compilation succeeds.

- [ ] **Step 6: Commit when inside the real Git repository**

```powershell
git add sandbox/TA/static/css/app.css sandbox/TA/static/js/dashboard.js sandbox/TA/templates/base.html sandbox/TA/templates/index.html
git commit -m "style: polish compact incident dashboard"
```

---

### Task 9: Full regression, visual review, and deployment handoff

**Files:**
- Verify: all changed TA files
- Update if implementation differs: `sandbox/TA/docs/SDD.md`
- Update if implementation differs: `sandbox/TA/docs/superpowers/specs/2026-08-31-incident-dashboard-redesign-design.md`

**Interfaces:**
- Consumes: completed Tasks 1-8.
- Produces: verified TA-only deployment package and rollback instructions.

- [ ] **Step 1: Run the complete automated suite**

```powershell
& $taPython -m unittest discover -s sandbox\TA\tests -t sandbox -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Compile all first-party Python modules**

```powershell
Get-ChildItem sandbox\TA -Filter *.py | ForEach-Object { & $taPython -m py_compile $_.FullName }
```

Expected: exit code `0` for every file.

- [ ] **Step 3: Run read-only Flask route smoke checks**

```powershell
$env:PYTHONPATH = 'C:\Users\dim13\incident_manager\sandbox'
& $taPython -c "from TA.app import app; c=app.test_client(); print(c.get('/').status_code); print(c.get('/protocols_history').status_code)"
```

Expected: `200` for both routes.

- [ ] **Step 4: Verify manual AI boundary in source**

```powershell
rg -n "GigaChatHelper|\.chat\(|run_ai_analysis" sandbox\TA\app.py sandbox\TA\static\js
```

Expected: GigaChat execution remains in the explicit AI POST route; index rendering contains no client call.

- [ ] **Step 5: Verify project boundary**

```powershell
Get-ChildItem 'C:\Users\dim13\incident_manager\backups\TA-dashboard-20260831' -Recurse | Select-Object FullName
```

Review changed paths and confirm all production edits are below `sandbox/TA`.

- [ ] **Step 6: Perform visual checks on desktop**

Check both themes at desktop widths:

1. current period and all five status tabs;
2. «Ещё» with active hidden week promoted;
3. repeat groups and inline protocol comment;
4. global ID and executor search;
5. selection bar and current protocol;
6. AI idle/running/completed/failed states;
7. incident detail and both back buttons;
8. protocol history and export.

- [ ] **Step 7: Produce deployment instructions**

Copy only `sandbox/TA` code files to the TA application folder on PROM. Do not replace `sandbox/cache/ta_incident_auditor`, certificates unless intentionally updated, CA, GD, or shared stand configuration. Restart the TA process and repeat the route smoke and one control Excel upload.

- [ ] **Step 8: Commit final documentation when inside the real Git repository**

```powershell
git add sandbox/TA/docs sandbox/TA
git commit -m "docs: finalize incident dashboard rollout"
```

Skip only in the current non-Git copy.
