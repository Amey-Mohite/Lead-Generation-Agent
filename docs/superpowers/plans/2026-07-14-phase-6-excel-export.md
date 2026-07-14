# Phase 6: Excel Export — Implementation Plan

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `list[Lead]` (what the pipeline already produces, currently only printed to stdout)
into a real, shareable `.xlsx` file — the first durable output this project produces.

**Architecture:** An `Exporter` protocol (same interface-plus-factory pattern as `LLMProvider`,
`SearchBackend`, `LeadSource`) with one implementation this phase: `ExcelExporter`. It flattens
each `Lead` (nested `ResearchBrief` + `Qualification` + optional `OutreachDraft`) into one
spreadsheet row, writes a timestamped `.xlsx` file into `EXPORT_DIR`, and returns the path written.
`build_exporters(settings)` parses the existing `EXPORTERS` comma-list config and returns the
matching `Exporter` instances (only `"excel"` recognized this phase; unknown names are silently
skipped, ready for `slack`/`email`/`gmail` to be added later).

**Tech Stack:** Python 3.12, `openpyxl` (new dependency). Reuses Phase 4's `Lead`/`Qualification`/
`OutreachDraft` and Phase 3's `Contact`/`ResearchBrief` schemas.

## Global Constraints

- **Python:** 3.12+.
- **No network in tests:** every test writes to a `tmp_path` (pytest's built-in temp-directory
  fixture) — never the real configured `EXPORT_DIR`.
- **One row per `Lead`, both statuses included.** `disqualified` leads appear with blank Outreach
  columns, not omitted — per the user's explicit choice (audit/transparency over a filtered sheet).
- **Multi-value fields (`key_facts`, `contacts`, `sources`) are joined into a single cell** with
  `"; "` — not exploded into extra rows/columns.
- **Each `export()` call writes a fresh, timestamped file** (`leads_YYYYMMDD_HHMMSS.xlsx`) — never
  appends to or overwrites a previous export. Appending is a clearly-scoped future enhancement, not
  built now.
- **`Exporter` is a `Protocol` with one implementation** (`ExcelExporter`) — defined now so
  `SlackExporter`/`EmailExporter`/`GmailExporter` can be added later without touching call sites.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  exporters/
    __init__.py
    base.py          # Exporter protocol
    excel.py          # ExcelExporter
    factory.py        # build_exporters(settings) -> list[Exporter]
scripts/
  try_lead.py          # + export the single Lead after producing it
  try_discovery.py      # + export the list[Lead] after producing them
tests/
  exporters/
    test_excel.py
    test_factory.py
docs/
  learning/phase-6-excel-export.md
```

---

### Task 1: `Exporter` protocol + `ExcelExporter`

**Files:**
- Modify: `pyproject.toml` (add `openpyxl>=3.1` to `dependencies`)
- Create: `app/exporters/__init__.py` (empty), `app/exporters/base.py`, `app/exporters/excel.py`
- Test: `tests/exporters/__init__.py` (empty), `tests/exporters/test_excel.py`

**Interfaces:**
- Consumes: `Lead`, `Qualification`, `OutreachDraft` (`app.schemas.lead`); `Contact`,
  `ResearchBrief` (`app.schemas.research`).
- Produces:
  - `Exporter(Protocol)` — `export(self, leads: list[Lead]) -> str: ...` (returns the file path
    written).
  - `ExcelExporter(export_dir: str)` implementing `Exporter`. Column order: `Company Name, Domain,
    Industry, Status, Score, Qualification Reasoning, Summary, Key Facts, Contacts, Sources,
    Outreach Subject, Outreach Body, Generated At`.

- [ ] **Step 1: Add the dependency** — in `pyproject.toml`, add `"openpyxl>=3.1"` to
  `dependencies`.

- [ ] **Step 2: Install it**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `openpyxl`; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/exporters/test_excel.py`

```python
from pathlib import Path

import openpyxl

from app.exporters.excel import ExcelExporter
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import Contact, ResearchBrief

_HEADER = [
    "Company Name", "Domain", "Industry", "Status", "Score",
    "Qualification Reasoning", "Summary", "Key Facts", "Contacts",
    "Sources", "Outreach Subject", "Outreach Body", "Generated At",
]


def _qualified_lead() -> Lead:
    return Lead(
        research=ResearchBrief(
            company_name="Acme Credit Union",
            domain="acme-cu.com",
            industry="Financial Services",
            summary="A credit union.",
            key_facts=["Founded 1990", "10,000 members"],
            contacts=[Contact(name="Jane Doe", role="CTO", email="jane@acme-cu.com")],
            sources=["https://acme-cu.com"],
        ),
        qualification=Qualification(score=85, reasoning="Strong fit."),
        outreach=OutreachDraft(subject="Quick question", body="Hi Jane, ..."),
        status="qualified",
    )


def _disqualified_lead() -> Lead:
    return Lead(
        research=ResearchBrief(company_name="Beta Corp", summary="Not a fit."),
        qualification=Qualification(score=20, reasoning="Wrong industry."),
        outreach=None,
        status="disqualified",
    )


def test_export_writes_expected_header_and_rows(tmp_path):
    exporter = ExcelExporter(export_dir=str(tmp_path))
    leads = [_qualified_lead(), _disqualified_lead()]

    path = exporter.export(leads)

    assert path.endswith(".xlsx")
    wb = openpyxl.load_workbook(path)
    ws = wb.active

    header = [cell.value for cell in ws[1]]
    assert header == _HEADER

    row1 = [cell.value for cell in ws[2]]
    assert row1[0] == "Acme Credit Union"
    assert row1[3] == "qualified"
    assert row1[4] == 85
    assert "Founded 1990" in row1[7]
    assert "Jane Doe (CTO) <jane@acme-cu.com>" in row1[8]
    assert row1[10] == "Quick question"
    assert row1[12] is not None  # Generated At populated

    row2 = [cell.value for cell in ws[3]]
    assert row2[0] == "Beta Corp"
    assert row2[3] == "disqualified"
    assert row2[10] is None  # no outreach subject
    assert row2[11] is None  # no outreach body


def test_export_creates_export_dir_if_missing(tmp_path):
    export_dir = tmp_path / "nested" / "dir"
    exporter = ExcelExporter(export_dir=str(export_dir))

    path = exporter.export([_qualified_lead()])

    assert Path(path).exists()


def test_export_writes_a_fresh_timestamped_file_each_call(tmp_path):
    exporter = ExcelExporter(export_dir=str(tmp_path))

    path1 = exporter.export([_qualified_lead()])
    path2 = exporter.export([_qualified_lead()])

    assert path1 != path2
    assert Path(path1).exists()
    assert Path(path2).exists()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/exporters/test_excel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.exporters'`.

- [ ] **Step 5: Create the package files**

Create empty (0-byte): `app/exporters/__init__.py`, `tests/exporters/__init__.py`.

`app/exporters/base.py`:
```python
from typing import Protocol

from app.schemas.lead import Lead


class Exporter(Protocol):
    def export(self, leads: list[Lead]) -> str: ...
```

`app/exporters/excel.py`:
```python
import os
import time
from datetime import datetime, timezone

import openpyxl

from app.schemas.lead import Lead

_HEADER = [
    "Company Name", "Domain", "Industry", "Status", "Score",
    "Qualification Reasoning", "Summary", "Key Facts", "Contacts",
    "Sources", "Outreach Subject", "Outreach Body", "Generated At",
]


def _format_contacts(lead: Lead) -> str:
    parts = []
    for contact in lead.research.contacts:
        piece = contact.name
        if contact.role:
            piece += f" ({contact.role})"
        if contact.email:
            piece += f" <{contact.email}>"
        parts.append(piece)
    return "; ".join(parts)


class ExcelExporter:
    """Writes a list of Leads to a timestamped .xlsx file, one row per Lead."""

    def __init__(self, export_dir: str) -> None:
        self._export_dir = export_dir

    def export(self, leads: list[Lead]) -> str:
        os.makedirs(self._export_dir, exist_ok=True)
        generated_at = datetime.now(timezone.utc).isoformat()

        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Leads"
        worksheet.append(_HEADER)

        for lead in leads:
            worksheet.append(
                [
                    lead.research.company_name,
                    lead.research.domain,
                    lead.research.industry,
                    lead.status,
                    lead.qualification.score,
                    lead.qualification.reasoning,
                    lead.research.summary,
                    "; ".join(lead.research.key_facts),
                    _format_contacts(lead),
                    "; ".join(lead.research.sources),
                    lead.outreach.subject if lead.outreach else None,
                    lead.outreach.body if lead.outreach else None,
                    generated_at,
                ]
            )

        filename = f"leads_{time.time_ns()}.xlsx"
        path = os.path.join(self._export_dir, filename)
        workbook.save(path)
        return path
```

> Note: the filename uses `time.time_ns()` rather than a second-precision timestamp so that two
> exports called back-to-back in the same test (or the same second in real usage) still get
> distinct filenames — see `test_export_writes_a_fresh_timestamped_file_each_call`.

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/exporters/test_excel.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Report changes** to the user for review/commit.

---

### Task 2: `build_exporters(settings)` factory

**Files:**
- Create: `app/exporters/factory.py`
- Test: `tests/exporters/test_factory.py`

**Interfaces:**
- Consumes: `Exporter`, `ExcelExporter` (Task 1); `Settings.exporters`, `Settings.export_dir`.
- Produces: `build_exporters(settings) -> list[Exporter]` — splits `settings.exporters` on `,`,
  trims whitespace, lowercases; returns one `ExcelExporter` for `"excel"`; silently skips any other
  name (reserved for `slack`/`email`/`gmail` later); returns `[]` for an empty string.

- [ ] **Step 1: Write the failing test** — `tests/exporters/test_factory.py`

```python
from app.config import Settings
from app.exporters.excel import ExcelExporter
from app.exporters.factory import build_exporters


def test_build_exporters_returns_excel_exporter_by_default():
    s = Settings(_env_file=None)
    exporters = build_exporters(s)
    assert len(exporters) == 1
    assert isinstance(exporters[0], ExcelExporter)


def test_build_exporters_ignores_unknown_names():
    s = Settings(_env_file=None, exporters="excel,slack,unknown")
    exporters = build_exporters(s)
    assert len(exporters) == 1
    assert isinstance(exporters[0], ExcelExporter)


def test_build_exporters_empty_string_returns_empty_list():
    s = Settings(_env_file=None, exporters="")
    exporters = build_exporters(s)
    assert exporters == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/exporters/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.exporters.factory'`.

- [ ] **Step 3: Create `app/exporters/factory.py`**

```python
from app.exporters.base import Exporter
from app.exporters.excel import ExcelExporter


def build_exporters(settings) -> list[Exporter]:
    names = [n.strip().lower() for n in settings.exporters.split(",") if n.strip()]
    exporters: list[Exporter] = []
    for name in names:
        if name == "excel":
            exporters.append(ExcelExporter(export_dir=settings.export_dir))
        # slack / email / gmail: reserved for future phases
    return exporters
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/exporters/test_factory.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all Phase 1-6 tests green (84 prior + this phase's new tests).

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 3: Wire export into the existing demo scripts

**Files:**
- Modify: `scripts/try_lead.py`
- Modify: `scripts/try_discovery.py`

**Interfaces:** none new — uses `build_exporters` (Task 2).

- [ ] **Step 1: Update `scripts/try_lead.py`** — after printing the `LEAD` JSON block, add:

```python
    from app.exporters.factory import build_exporters

    for exporter in build_exporters(settings):
        path = exporter.export([lead])
        print(f"\nExported to: {path}")
```

(Placed right after the existing `print(lead.model_dump_json(indent=2))` line, before the
`if __name__ == "__main__":` guard at the bottom of `main()`.)

- [ ] **Step 2: Update `scripts/try_discovery.py`** — after the loop that prints each lead, add:

```python
    from app.exporters.factory import build_exporters

    for exporter in build_exporters(settings):
        path = exporter.export(leads)
        print(f"\nExported {len(leads)} lead(s) to: {path}")
```

- [ ] **Step 3: Verify in offline demo mode**

Run: `./.venv/Scripts/python.exe scripts/try_lead.py --demo`
Expected: prints the `LEAD` JSON block, then `Exported to: ./out/leads/leads_....xlsx`. Confirm the
file exists: `ls out/leads/`.

Run: `./.venv/Scripts/python.exe scripts/try_discovery.py --demo`
Expected: prints `2 LEAD(S) FOUND`, then `Exported 2 lead(s) to: ./out/leads/leads_....xlsx`.

- [ ] **Step 4: Report changes** to the user for review/commit.

---

### Task 4: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-6-excel-export.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md` (Status section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-6-excel-export.md`** — same structure as the Phase 1-5
  guides. Must cover:
  - **What & why** — the first durable output the project produces; why `Exporter` is a Protocol
    with one implementation now (same reasoning as `LeadSource` before `RegistrySource` existed);
    why a fresh timestamped file per export rather than append-in-place (simplicity first, an
    append/merge mode is a clear future enhancement once there's a reason to need it).
  - **The flow** — `list[Lead] -> build_exporters(settings) -> [ExcelExporter] -> .export(leads) ->
    flatten each Lead into one row -> write leads_<timestamp>.xlsx -> return path`.
  - **File-by-file walkthrough** — `app/exporters/base.py` (the Protocol); `app/exporters/excel.py`
    (why multi-value fields are joined into one cell rather than exploded into extra rows -- one
    row per Lead keeps the "one lead, one line" mental model a salesperson expects from a lead
    list; why disqualified leads are included with blank Outreach columns, per the audit/
    transparency choice); `app/exporters/factory.py` (parses the existing `EXPORTERS` comma-list
    config -- unknown names are silently skipped, reserved for Slack/Email/Gmail exporters later).
  - **Key concepts table** — protocol-with-one-implementation (define the seam before you need the
    second implementation), flattening nested data for tabular output, fresh-file-per-call as the
    simple default, config-driven multi-exporter selection (a comma list, not a single value).
  - **How to run & test** — `pytest tests/exporters -v`, explaining what each test proves (correct
    header/column order, disqualified rows have blank Outreach cells, the export dir is created if
    missing, two calls in a row produce two distinct files); `scripts/try_lead.py --demo` /
    `scripts/try_discovery.py --demo`, showing the exported file path.
  - **What's next** — Phase 7: Persistence (Postgres storage of Leads/agent runs/request logs),
    noting the user's own local PostgreSQL server will be used instead of a docker-compose
    container.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table and
  update the "mental model" diagram's Phase 6 line from `Exporters ... (the "handoff")` (already
  present) to confirm it's now built, no wording change needed beyond the new table row:

```markdown
| [Phase 6 — Excel Export](phase-6-excel-export.md) | The first durable output: `list[Lead]` becomes a real, shareable `.xlsx` file via a pluggable `Exporter` protocol -- one row per lead, multi-value fields joined into a cell, disqualified leads included for audit visibility. |
```

- [ ] **Step 3: Update `README.md`** — change the Phase 6 status line:

```markdown
- [x] Phase 6 — Exporters (Excel first, via a pluggable `Exporter` protocol; Slack/Email/Gmail later)
```

and update the "Current" status line at the top of the Status section to Phase 6.

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 6 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-6), no network required.
- `ExcelExporter.export(leads)` produces a real `.xlsx` file with the agreed 13-column layout,
  correct for both qualified (with outreach) and disqualified (blank outreach) leads.
- `scripts/try_lead.py --demo` and `scripts/try_discovery.py --demo` both produce a real `.xlsx`
  file in `EXPORT_DIR`, proving the whole path end-to-end with zero keys.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 7 — Persistence: storing `Lead`,
`agent_runs`, and `request_logs` in Postgres, using the user's own locally-installed PostgreSQL
server (connection details to be gathered when this phase starts) rather than the docker-compose
container from Phase 1.
