"""
Fill in the two placeholders below after you've done the one-time Google
Cloud setup (see README.md). Everything else has a sensible default.
"""

# The long ID in your sheet's URL:
# https://docs.google.com/spreadsheets/d/THIS_PART/edit
SPREADSHEET_ID = "11rjdjIi380Owes30yZpnRnQoI21IVuqk6o3DofaM0a8"

# Path to the service account JSON key file you downloaded from Google Cloud
CREDENTIALS_PATH = r"D:\projects\fidelity_sync\credentials\service_account.json"

# Tab names in the spreadsheet
SHARES_TAB = "shares"
OPTION_TAB = "option"
DIVIDEND_TAB = "DIVIDEND"

# Where the weekly Fidelity export lands
CSV_PATH = r"C:\Users\korsh\Downloads\Accounts_History.csv"

# Local state (not in the sheet) -- tracks which CSV transactions have
# already been applied, so reruns don't double-count.
LEDGER_PATH = r"D:\projects\fidelity_sync\state\ledger.json"
LOG_PATH = r"D:\projects\fidelity_sync\state\run_log.txt"

# Tickers to exclude entirely from the shares tab (cash-sweep / money-market
# instruments, not real discretionary trades)
CASH_SWEEP_TICKERS = {"SHV", "OBIL", "FDRXX", "SPAXX"}
