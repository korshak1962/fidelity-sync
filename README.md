# Fidelity → Google Sheets trade sync

 If you want it to run right after you download (rather than waiting for Saturday), your options are:
  1. Manual trigger — just tell me "run it" (or run python main.py yourself) after downloading.
  2. Task Scheduler "Run" button — open Task Scheduler and click Run on the "Fidelity Trade Sync" task on demand.
  3. A real file-watch trigger — Task Scheduler supports "on event"/folder-change triggers; I could set one up so it fires automatically whenever
  Accounts_History.csv changes, instead of only on the weekly schedule.


Parses your weekly Fidelity `Accounts_History.csv` export and syncs it into
three tabs of your "Trades" Google Sheet: `shares`, `option`, and `DIVIDEND`.

## What it does

- **shares**: combines buy fills into a weighted-average lot, matches sells
  against them, tracks open positions, updates them in place when later
  sold. Cash-sweep instruments (SHV, OBIL, FDRXX, SPAXX) are excluded.
- **option**: same idea, grouped by underlying+type+strike+expiration.
  Handles expiration (closes at $0) and assignment (closes the option leg
  at $0; the resulting stock position shows up separately in `shares`).
- **DIVIDEND**: every dividend and reinvestment row, as separate line
  items, across all 5 accounts.
- Your manual note columns in `shares` (columns I/J) are never touched.
- A local ledger (`state/ledger.json`) prevents double-counting when the
  same transaction appears in more than one weekly CSV export.

## One-time setup

### 1. Install dependencies

```
pip install -r requirements.txt --break-system-packages
```

### 2. Create a Google Cloud service account

1. Go to https://console.cloud.google.com/ (create a project if you don't
   have one -- any name is fine, e.g. "fidelity-sync")
2. **APIs & Services → Library** → search "Google Sheets API" → Enable
3. **APIs & Services → Credentials** → **Create Credentials** → **Service account**
   → give it any name (e.g. "sheet-sync") → Create and continue → skip the
   optional role/access steps → Done
4. Click into the new service account → **Keys** tab → **Add key** →
   **Create new key** → JSON → this downloads a `.json` file
5. Move that file to `D:\projects\fidelity_sync\credentials\service_account.json`
   (create the folder). **Do not commit or share this file** -- it's a
   private key with write access to whatever you share with it.
6. Open the JSON file and copy the `client_email` value (looks like
   `sheet-sync@your-project.iam.gserviceaccount.com`)

### 3. Share the sheet with the service account

In your "Trades" Google Sheet: **Share** → paste the `client_email` from
above → set it to **Editor** → Share.

(You can now also set "General access" back to **Restricted** -- the
service account doesn't need the public link, it has its own direct
access.)

### 4. Fill in config.py

Open `fidelity_sync/config.py` and confirm:
- `SPREADSHEET_ID` (already filled in from your sheet's URL)
- `CREDENTIALS_PATH` points to the JSON key file from step 2
- `CSV_PATH` matches where Fidelity's export lands

### 5. Bootstrap

Before turning on weekly automation, run once:

```
cd fidelity_sync
python main.py --bootstrap
```

This marks everything in today's CSV as already-accounted-for, **without
writing anything to the sheet**. This establishes a clean baseline so
positions already reflected in your sheet's current rows don't get
double-counted when the automation starts.

### 6. Dry run

```
python main.py --dry-run
```

Prints exactly what would be inserted/updated in each tab, without writing
anything. Check this against your sheet by eye before going live.

### 7. Go live

```
python main.py
```

This writes to the sheet and updates the ledger. Run it manually once to
confirm it looks right, then wire up the weekly trigger below.

## Weekly automation (Windows Task Scheduler)

The script reads whatever `Accounts_History.csv` is currently in your
Downloads folder -- so you still manually download it from Fidelity each
week, then the processing/upload is automatic.

1. Open **Task Scheduler** → **Create Task**
2. **General** tab: name it "Fidelity Trade Sync", check "Run whether user
   is logged on or not"
3. **Triggers** tab → **New** → Weekly, pick a day/time (e.g. Sunday 6pm)
4. **Actions** tab → **New** → Program: `python` (or full path to
   python.exe) → Arguments: `main.py` → Start in:
   `D:\projects\fidelity_sync\fidelity_sync`
5. Save. Enter your Windows password if prompted.

Check `state/run_log.txt` after each run to see what happened, and watch
for WARNING lines -- those flag sells that couldn't be matched to any open
position (in the CSV window or the sheet) and were skipped.

## Re-running safely

Running `main.py` again on the same CSV is safe -- the ledger ensures
already-applied transactions are skipped, so you won't get duplicate rows
or inflated positions.
