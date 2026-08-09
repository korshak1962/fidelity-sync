# TASK: Full rebuild dry-run after ledger/sheet reset

## Current state (as of this task being written)

- `state/ledger.json` has been reset to empty (`{"processed_ids": []}`) --
  every transaction in the CSV is "new" again as far as the script is
  concerned.
- The user has manually deleted the old data rows from both `shares` and
  `option` tabs in the Google Sheet (this was "Option A": reset the 9
  previously-recorded rows and let automation rebuild everything fresh
  from the CSV, rather than surgically excluding just the already-recorded
  transactions -- see conversation history / CLAUDE.md context if you need
  the reasoning).
- This means the next `--dry-run` / live run will treat the ENTIRE current
  `Accounts_History.csv` as new, and should reconstruct:
  - `shares`: ITA and RBC as CLOSED rows, AA/BOTJ/WEEL/SOXS as OPEN rows
    (WEEL in particular should combine ALL of its buy fills across the CSV
    into one weighted-average open position, per the "combine consecutive
    buys" rule already implemented in shares_matcher.py)
  - `option`: QQQ 716 and QQQ 717 as CLOSED, AA 43 as OPEN, plus several
    other contracts (VALE, EEM, SPCX, SPY, PFE, DUOL, etc.) that were never
    manually recorded before -- these are all legitimate new inserts
  - `DIVIDEND`: should show 0 new rows (already synced correctly in an
    earlier run; dedup is content-based against the sheet, independent of
    the ledger reset)

## Steps

### 1. Verify the shares tab's header row survived the manual deletion
Read row 1 of the `shares` tab. It should still contain headers (ticker /
buy date / price / qty / price sell / date sell / p&L / p&l% / note / note
-- exact wording may vary slightly, that's fine). **If the header row looks
like it's missing or was accidentally deleted along with the data (e.g.
row 1 contains what looks like ticker data, or is blank), STOP and ask the
user what the original header text was rather than guessing/fabricating
one.** Do NOT invent header text.

Also check the `option` tab -- it never had a header row to begin with
(that's expected, not an error), so nothing to verify there.

### 2. Run the dry-run
```
cd fidelity_sync
python main.py --dry-run
```
Capture the full output.

### 3. Sanity-check the output against the expected reconstruction above
- Do ITA and RBC come back as CLOSED with plausible prices/P&L?
- Does WEEL come back as ONE open row with a combined quantity (not
  several separate rows for each buy fill)?
- Are the previously-recorded contracts (QQQ 716, QQQ 717, AA 43) present
  with the right status?
- Are the new option contracts (VALE, EEM, SPCX, SPY, PFE, DUOL) present
  as sensible inserts?
- Are cash-sweep tickers (SHV, OBIL, FDRXX, SPAXX) correctly absent from
  the shares output?
- Any WARNING lines? Read them and make sure they're expected (e.g. a
  sell with genuinely no traceable buy) rather than a bug.

### 4. Do NOT go live yet
Per CLAUDE.md permissions, a live run (`python main.py` without
`--dry-run`) requires the user's explicit confirmation. **Stop here and
report the full dry-run output back**, plus your assessment of whether it
looks correct, so the user (or Claude, in the chat conversation this task
came from) can review it before anything is actually written to the real
sheet.

### 5. Do not re-run `--bootstrap`
Bootstrap should not be run again in this flow -- it was the reason
everything needed resetting in the first place (it incorrectly assumed
the CSV matched the sheet's state, which wasn't true here). The plain
dry-run / live commands are what's needed now.

## Report back

Paste the full dry-run output and your read on whether it matches the
expected reconstruction above. If anything looks off (unexpected warnings,
missing tickers, wrong quantities), flag it clearly rather than proceeding.
