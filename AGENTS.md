# AGENTS.md

Context for coding agents (Codex etc.) working in this repo; kept in sync with `CLAUDE.md`. Read this first; `README.md`
has the one-time Google Cloud setup. (`TASK.md` describes an earlier,
completed task -- history only.)

## What this project is

A weekly sync from a Fidelity brokerage CSV export into a Google Sheet
("Trades"), live since Aug 2026:

| Tab | Written by | Content |
|-----|-----------|---------|
| `shares` | `main.py` | weighted-average lots per ticker, buys matched to sells |
| `option` | `main.py` | one row per underlying+type+strike+expiry+open date |
| `DIVIDEND` | `main.py` | dividend rows, content-deduped against the sheet |
| `analysis` | `analysis.py` | best/worst 5 trades by %, P&L and win rate by month, four charts |
| `buffer` | the user | never touched by any script |

Nothing is written to MySQL or any other store.

## Structure

```
fidelity_sync/
├── config.py           -- paths, sheet ID, tab names, exclusion list
├── csv_parser.py        -- parses Accounts_History.csv, classifies rows
├── dividends.py          -- builds DIVIDEND tab rows
├── shares_matcher.py     -- shares rows: lot matching, sheet seeding, placeholder transfers
├── options_matcher.py    -- option rows: contract matching
├── state.py               -- local ledger (state/ledger.json) preventing double-counting
├── sheets_sync.py          -- gspread wrapper, reads/writes the sheet
├── main.py                  -- import orchestrator: --bootstrap / --dry-run / live
└── analysis.py              -- rebuilds the `analysis` tab: --dry-run / --recreate-charts
credentials/service_account.json  -- Google service account key (never print it)
state/                        -- ledger.json, run_log.txt (git-ignored)
```

## How to run

```
cd fidelity_sync
python main.py --dry-run     # reads the sheet, shows what WOULD be written, writes nothing
python main.py               # live: writes to the sheet, updates the ledger
python analysis.py           # refresh the analysis tab (--dry-run prints it instead)
```

`main.py` reads `config.CSV_PATH`. To import a different file without editing
config, set `config.CSV_PATH` from a small wrapper before calling `main.main()`.

## Schedule

Windows Task Scheduler task **"Fidelity Trade Sync"**, Saturdays 07:00, runs as
the user (interactive), working directory `fidelity_sync/`:
1. `python main.py` -- imports `~/Downloads/Accounts_History.csv`
2. `python analysis.py` -- refreshes the `analysis` tab

Both log to `state/run_log.txt`.

## How the matching works (read before changing it)

- The ledger records every transaction ID the importer has *seen*, including
  sells it skipped for lack of a matching buy. A skipped close is therefore
  never retried. Consequence: **history must be imported oldest first.**
  Importing a newer CSV before an older one strands its closes permanently.
- A close whose open predates the CSV window is matched against OPEN rows
  already in the sheet; if there is none it is skipped with a warning.
- Within one day, buys/opens are processed before sells/closes (Fidelity's
  intra-day row order isn't chronological).
- Dividend reinvestments count as buys at their reinvestment price.
- Share transfers-in ("RECEIVED FROM YOU") have no price in the CSV: they are
  booked at a $100 placeholder, the price cell is coloured red and the P&L is
  written as formulas, so the user types the real cost basis in later.
- Every append is anchored at `A1` (`table_range="A1"`). The `option` header
  has a blank column B; without the anchor, rows appended to an emptied tab
  land two columns to the right.

### Full rebuild from CSV history

When the sheet has drifted (stranded closes, stale OPEN rows), rebuild rather
than patch: back up the three tabs and the ledger, clear rows 2+ of `shares`,
`option`, `DIVIDEND`, reset the ledger to `{"processed_ids": []}`, then import
every quarterly CSV **oldest first**. Overlapping days between files are
handled by the ledger. The Sep 2026 rebuild kept its backup and clearing
script in `state/backup_20260926/`.

## The analysis tab

- Rankings: shares by price-change %, options by P&L as % of premium --
  ranked separately because the two % are not comparable. Rows still at the
  $100 transfer placeholder are left out.
- Monthly P&L by closing date, plus dividends and a running total; next to it
  (columns H-M) the win rate per month -- shares, options, combined and
  cumulative. A win is a trade closed with P&L > 0; placeholder rows are left
  out. Months are
  written as real dates (shown `yyyy-mm`) so the chart axis is a time axis --
  Sheets offers axis gridlines only on a date axis.
- Four charts in a 2x2 grid from column O (P&L by month | win rate by month,
  cumulative P&L | cumulative win rate). Each is created once, matched by
  title, and afterwards only moved, never redrawn -- so formatting the user
  set by hand in the chart editor (gridlines, which the Sheets API can't set)
  survives a refresh. Their range is open-ended, so new months appear by
  themselves. `--recreate-charts` deletes and redraws them (losing that
  formatting).

## Permissions / guardrails for autonomous work

You have permission to, without asking first:
- Run `pip install -r requirements.txt`
- Run `python main.py --dry-run` and `python analysis.py --dry-run` (write nothing)
- Read/edit any `.py` file in `fidelity_sync/` to fix bugs you find
- Read `state/run_log.txt` and `state/ledger.json`

Ask for explicit confirmation before:
- Running `python main.py` **without** `--dry-run` (this writes real data to the user's Google Sheet)
- Clearing tab data or resetting the ledger (a full rebuild) -- back up first
- Modifying `config.py`'s `SPREADSHEET_ID`, `CREDENTIALS_PATH`, or `CASH_SWEEP_TICKERS` (these encode decisions the user made deliberately)
- Creating or modifying a Windows Task Scheduler entry
- Deleting or overwriting `credentials/service_account.json` or anything in `state/`

Never:
- Print, log, or echo the contents of `credentials/service_account.json` (it's a private key)
- Commit `credentials/` or `state/` to git (both are in `.gitignore`)
- Write to the `buffer` tab, or to cells the user filled in by hand beyond the
  columns the sync owns (shares A–H, option A–K, DIVIDEND A–F)

## Known context from prior design decisions (don't re-litigate these)

- Shares/options tabs are **account-agnostic** by user's explicit choice -- a ticker's position is combined across all 5 Fidelity accounts into one lot. Don't add an account column.
- Cash-sweep instruments (SHV, OBIL, FDRXX, SPAXX) are deliberately excluded from the `shares` tab -- they're parking vehicles, not real trades.
- Sells whose buys predate the oldest CSV (Jul 2025: e.g. VALE, T, PBR, LI, INTC, part of TLT and EPAM) stay unmatched until an older CSV is imported. Don't invent cost bases for them.
