# CLAUDE.md

Context for Claude Code working in this repo. Read this first, then
`README.md` for full setup detail and `TASK.md` for the current task.

## What this project is

A weekly sync from a Fidelity brokerage CSV export into a Google Sheet
("Trades") with three tabs: `shares`, `option`, `DIVIDEND`. Built and
tested (unit-level, against real sample data) in a prior session with
Claude (chat). The Google Sheets integration itself has NOT been
live-tested yet -- that's the current task, see TASK.md.

## Structure

```
fidelity_sync/
├── config.py           -- paths, sheet ID, tab names, exclusion list
├── csv_parser.py        -- parses Accounts_History.csv, classifies rows (tested)
├── dividends.py          -- builds DIVIDEND tab rows (tested)
├── shares_matcher.py     -- builds shares tab rows: lot matching, bootstrap seeding (tested)
├── options_matcher.py    -- builds option tab rows: contract matching (tested)
├── state.py               -- local ledger (state/ledger.json) preventing double-counting
├── sheets_sync.py          -- gspread wrapper, reads/writes the actual sheet (UNTESTED live)
└── main.py                  -- orchestrator: --bootstrap / --dry-run / live
credentials/
└── service_account.json    -- Google service account key (already created & sheet already shared with it)
state/                        -- created on first run: ledger.json, run_log.txt
```

## How to run

```
cd fidelity_sync
python main.py --bootstrap   # one-time, marks current CSV as baseline, writes nothing
python main.py --dry-run     # reads the sheet, shows what WOULD be written, writes nothing
python main.py               # live: writes to the sheet, updates the ledger
```

## Permissions / guardrails for autonomous work

You have permission to, without asking first:
- Run `pip install -r requirements.txt`
- Run `python main.py --bootstrap` and `python main.py --dry-run` (both are non-destructive -- bootstrap only writes the local ledger, dry-run writes nothing at all)
- Read/edit any `.py` file in `fidelity_sync/` to fix bugs you find
- Read `state/run_log.txt` and `state/ledger.json`

Ask for explicit confirmation before:
- Running `python main.py` **without** `--dry-run` (this writes real data to the user's Google Sheet)
- Modifying `config.py`'s `SPREADSHEET_ID`, `CREDENTIALS_PATH`, or `CASH_SWEEP_TICKERS` (these encode decisions the user made deliberately)
- Creating or modifying a Windows Task Scheduler entry
- Deleting or overwriting `credentials/service_account.json` or anything in `state/`

Never:
- Print, log, or echo the contents of `credentials/service_account.json` (it's a private key)
- Commit `credentials/` or `state/` to git if this ever becomes a git repo (add a `.gitignore` if you set one up)

## Known context from prior design decisions (don't re-litigate these)

- Shares/options tabs are **account-agnostic** by user's explicit choice -- a ticker's position is combined across all 5 Fidelity accounts into one lot. Don't add an account column.
- Cash-sweep instruments (SHV, OBIL, FDRXX, SPAXX) are deliberately excluded from the `shares` tab -- they're parking vehicles, not real trades.
- The `shares`/`option` tabs' note columns (I, J in shares) are user-curated judgment notes (e.g. "bought not in up hours channel") and must never be overwritten by the sync.
- Bootstrap must be run once, before the first real (non-dry-run) run, to avoid double-counting transactions already reflected in the sheet's current rows.
