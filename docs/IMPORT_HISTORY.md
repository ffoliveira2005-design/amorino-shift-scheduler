# import_history.py — Importing past Amorino schedules

A standalone script that parses your weekly Amorino schedule PDFs and inserts
them into the `amorino.db` database, so the historical-affinity scoring and
"Sugerir alvos do histórico" features actually have data to work with.

## Prerequisites

```bash
pip install pdfplumber
```

`pdfplumber` is the only extra dependency. It reads tabular PDF structure
and gives back rows with explicit column positions.

## Usage

The script lives in `scripts/` and is run from the project root (it locates
`app.py` automatically). Then:

```bash
# Dry run — parse and report, but don't write anything:
python scripts/import_history.py --dry-run /path/to/your/*.pdf

# Real run — write to the database:
python scripts/import_history.py /path/to/your/*.pdf

# Specific DB path:
python scripts/import_history.py --db /var/data/amorino.db /path/to/your/*.pdf

# Force a specific year (if filenames don't include one):
python scripts/import_history.py --year 2025 /path/to/old/*.pdf
```

The bundled example PDFs live in `docs/samples/`, e.g.:

```bash
python scripts/import_history.py --dry-run docs/samples/*.pdf
```

You can run it on dozens of PDFs at once — they're processed one by one.

## What it does

For each PDF:

1. Parses the table on page 1 (the schedule grid)
2. Extracts the week's date range from the day headers ("Segunda-feira 04/05" etc.)
3. Year is inferred from the filename (looks for a 4-digit number); use `--year` to override
4. For each `(loja, time-band, day, sigla)` cell, prepares a `Turno` row
5. Creates the `SemanaConfig` for that week if it doesn't already exist
6. Inserts turnos, **skipping**:
   - Duplicates (same `(funcionario, loja, horario, dia, semana)` already in DB)
   - Siglas not present in your current `Funcionario` table
   - Non-shift rows like `Provas Praticas`, `* Troca de Loja`, etc.

The script is **idempotent**: running it twice on the same PDF produces no
new rows the second time.

### PDF interpretation

Store titles can share an extracted cell with an annotation (for example a
HACCP visit). The parser recognizes the store title on its own line, so the
following shifts stay in the correct store. Clock bands accept underscore,
slash, or hyphen separators. A clock range written beside an employee, such
as `JG 15-17`, overrides that employee's band with `15:00-17:00`; an inherited
paid-hours modifier is not added to that shorter range.

`parse_pdf()` also returns structured `descargas` from delivery footnotes,
including the store, day, employee, stated start and description. These notes
do not specify end times. The standard CLI's `import_to_db()` imports regular
shifts; delivery records are available to a reviewed full-schedule import.
For the two October 2026 schedules, the owner confirmed the usual four-hour
delivery window: an 08:00 start ends at 12:00, and the Norte Shopping 09:00
start ends at 13:00. Those ends follow the confirmed application rule rather
than an end time printed in the PDF. Source hour balances are not reliable
enough to infer delivery duration or pay independently.

## Reading the output

```
→ Amorino__Horários_2026_-_Porto_e_Gaia_4_a_10_de_maio.pdf
  Week: 2026-05-04 (139 turnos parsed)
  Codigo: 4-10mai (new)
  created: 139 turnos, skipped 0 duplicates, 0 unknown sigla
```

- `new` / `existing` after the codigo tells you whether the SemanaConfig was
  created or already existed
- `unknown sigla` count = how many cells referenced siglas not in your
  `Funcionario` table; those are skipped. If you see this, the summary at the
  bottom lists all unknown siglas — add them to Funcionario and re-run.

## After importing

Once you've imported a few weeks (4+ is the sweet spot):

1. Open `/supervisor/escala` as supervisor
2. Click **Atribuir Turnos** → **⚙ Alvos** → **Sugerir do histórico**
3. Review the proposals — each shows `current → proposed` with the underlying
   weekly observations on hover
4. Accept all (or click each one individually) → Save

The "Sugerir:" chips on each loja card will also now tag people with `HIST`
when they were picked primarily because of past-week patterns.

## Troubleshooting

**"Could not determine week start date"** — the PDF's day-header row didn't
have the `DD/MM` format the parser expects. Open the PDF, check the top row
of the table is intact, and try again. If you don't have a fix, you can
manually set the year with `--year` or move the file aside.

**Many unknown siglas reported** — your `Funcionario` roster may have changed
since those PDFs were made. Either add the missing siglas, or accept the loss
(those turnos will just be skipped).

**`PdfminerException: No /Root object`** — the file isn't a real PDF. If you
got it from Claude's project knowledge, that strips the PDF — re-upload it
directly to the chat or use the original file from your computer.

## Backup before running

Back up `amorino.db` with SQLite's online backup API, which includes committed
WAL data while the app is running:

```bash
python scripts/backup_db.py --keep 0
```

## Verified batch import

For a reviewed batch that includes staff identities and deliveries, use
`scripts/import_schedule_batch.py` with verified JSON records, oldest week
first. This importer never loads Flask, and the default only reads the database:

```bash
python scripts/import_schedule_batch.py current-week.json next-week.json --db amorino.db
```

Each record contains `source`, `week_start`, `week_codigo`, `week_label`, seven
`days`, `shifts`, `descargas`, `roster`, and an empty `warnings` list. Resolve
unidentified initials, conflicting records and missing delivery durations
before applying. The batch importer uses the confirmed four-hour delivery rule;
the PDF parser itself records only the stated start time.

`--apply` creates a verified retained online backup, rechecks the whole batch
under a write lock, and writes it in one transaction. Existing staff details,
credentials, manual shifts and headcount targets are preserved. Explicit
`contract_updates` passed to `import_batch()` update only approved contract
hours/types, recording previous values in the import audit. Missing staff
must have confirmed names; unconfirmed responsibilities default to Formação.
Exact repeats are skipped, and conflicting assignments stop the whole import.
`--activate-week CODE` explicitly activates an imported week.

The older `import_history.py --dry-run` loads the app, whose startup may write
migrations or seed data. Run it against a scratch copy when inspecting an
existing database; use the batch importer's default for a strictly read-only plan.
