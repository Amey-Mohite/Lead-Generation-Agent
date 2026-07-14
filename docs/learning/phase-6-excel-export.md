# Phase 6 — Excel Export (Learning Guide)

> **Goal of this phase:** produce the first *durable* output this project makes. Every `Lead`
> before this phase existed only as an in-memory object, printed to a terminal, then gone. This
> phase turns `list[Lead]` into a real, shareable `.xlsx` file.

---

## 1. What & why

Everything built so far (research, qualify, draft, discover) produces genuinely useful data — but
none of it survives past the process that generated it. A business user can't hand a terminal
printout to a colleague. This phase closes that gap with the simplest durable format available: a
spreadsheet.

**Why `Exporter` is a `Protocol` with exactly one implementation.** This mirrors `LeadSource` before
`RegistrySource` existed: the interface is defined *now*, even though `ExcelExporter` is the only
implementation this phase, so `SlackExporter`/`EmailExporter`/`GmailExporter` (already named in the
original design spec's `EXPORTERS` comma-list) can be added later without touching
`build_exporters()`'s callers at all.

**Why a fresh timestamped file per export call, not append-in-place.** Appending safely means
reading the existing file, checking for duplicate leads, and merging rows — real complexity with no
clear requirement yet. Writing a fresh file every time is simpler, can never corrupt a previous
export, and is easy to reason about. Once there's an actual need to accumulate leads across many
runs into one running file, that's a well-scoped future enhancement — not something to build
speculatively now.

---

## 2. The flow

```
  list[Lead]                              (from run_discovery_pipeline() / LeadOrchestratorAgent.run())
     │
     ▼
  build_exporters(settings)
     parses EXPORTERS (comma list, e.g. "excel") -> [ExcelExporter(export_dir=...)]
     unknown names (slack, email, gmail) silently skipped -- reserved for later
     │
     ▼
  for each exporter: exporter.export(leads)
     │
     ▼
  ExcelExporter.export(leads):
     for each Lead, flatten into ONE row:
       Company Name, Domain, Industry, Status, Score, Qualification Reasoning,
       Summary, Key Facts (joined), Contacts (joined), Sources (joined),
       Outreach Subject, Outreach Body, Generated At
     write leads_<timestamp>.xlsx into EXPORT_DIR
     │
     ▼
  return path   (printed by the calling script)
```

---

## 3. File-by-file walkthrough

### `app/exporters/base.py` — the `Exporter` Protocol
One method: `export(self, leads: list[Lead]) -> str`. Returns the file path written, so callers
(the demo scripts, and later the API layer) can report or link to exactly what was produced.

### `app/exporters/excel.py` — `ExcelExporter`
- **Multi-value fields are joined into one cell, not exploded into extra rows.** A lead can have
  several `key_facts`, several `contacts`, several `sources`. Turning each into its own row (or its
  own set of numbered columns) would break the "one row = one lead" mental model a lead list is
  supposed to have — a salesperson scanning the sheet wants one line per company, not a company
  split across three rows because it happened to have three contacts. `"; ".join(...)` keeps it to
  one cell, one row, full information retained.
- **Disqualified leads are included, with blank Outreach columns** (`lead.outreach.subject if
  lead.outreach else None`) — not filtered out. Per the explicit choice made before building this:
  the sheet is meant for audit/transparency (see everything that was considered), not just a
  polished "ready to send" list. A `None` in the Outreach columns is self-explanatory in context —
  the Status column right next to it already says `disqualified`.
- **`Generated At` is captured once per `export()` call**, not per lead — every row in one export
  gets the same timestamp, since it answers "when was this batch produced," not "when was this
  specific company researched" (which `Lead` doesn't currently track — see Phase 7).
- **Filenames use `time.time_ns()`, not a second-precision timestamp.** Two exports triggered in
  the same second (easily possible in a test, or in real automated use) would otherwise collide on
  the same filename and silently overwrite each other. Nanosecond precision makes that collision
  astronomically unlikely.

### `app/exporters/factory.py` — `build_exporters(settings)`
Splits `Settings.exporters` on commas, trims and lowercases each name, and maps only `"excel"` to a
real `ExcelExporter` today. Unrecognized names (`"slack"`, `"unknown"`, etc.) are silently
skipped rather than raising — this keeps `EXPORTERS=excel,slack,email` configurable *now*, ready for
those other exporters to be dropped in later, without the config breaking in the meantime.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Protocol with one implementation | Define the seam before you need the second implementation | Anywhere you can already name a future variant, even if you're only building one now |
| Flattening nested data for tabular output | Join multi-value fields into one cell; keep one row per entity | Any time structured data needs to become a spreadsheet/CSV |
| Fresh-file-per-call as the simple default | Don't build append/merge logic before there's a real need for it | Any output artifact, until accumulation is an actual requirement |
| Config-driven multi-select | A comma list selects zero or more implementations, not just one | Any feature where multiple outputs could be active simultaneously |

---

## 5. How to run & test it

```bash
# All Phase 6 tests — no network (writes only to pytest's tmp_path, never the real EXPORT_DIR)
./.venv/Scripts/python.exe -m pytest tests/exporters -v
```

### What the tests prove
- `test_excel.py` — the header row and column order match exactly what was agreed; a qualified
  lead's Outreach columns are populated and a disqualified lead's are blank; the export directory
  is created automatically if it doesn't exist; two `export()` calls in a row produce two distinct
  files (proving the nanosecond-precision filename actually prevents collisions).
- `test_factory.py` — `build_exporters()` returns an `ExcelExporter` for the default `EXPORTERS`
  setting, ignores unrecognized names instead of erroring, and returns an empty list for an empty
  string.

### Trying it for real
```bash
python scripts/try_lead.py --demo
python scripts/try_discovery.py --demo
```
Both now print an `Exported to: ...` / `Exported N lead(s) to: ...` line after the JSON output,
and a real `.xlsx` file lands in `EXPORT_DIR` (default `./out/leads/`) — open it in Excel or any
spreadsheet app to see the real result.

---

## 6. What's next

Phase 7 — **Persistence**: storing `Lead`, `agent_runs`, and `request_logs` in Postgres, using the
user's own locally-installed PostgreSQL server rather than the docker-compose container from Phase
1. This is also where a per-lead (not just per-export) timestamp naturally starts to matter —
worth revisiting once leads are tracked in a database across many runs over time.
