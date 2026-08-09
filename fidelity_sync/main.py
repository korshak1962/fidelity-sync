"""
Usage:
    python main.py --bootstrap    # one-time: mark today's CSV as baseline, write nothing
    python main.py --dry-run      # show what WOULD be written, write nothing
    python main.py                # live run: write to the sheet, update the ledger

Reads config.py for the CSV path, sheet ID, credentials path, etc.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import config
from csv_parser import load_fidelity_csv
from dividends import build_dividend_rows
from options_matcher import build_option_rows
from shares_matcher import build_shares_rows
from state import Ledger


def log(msg: str, log_path: str = None):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    if log_path:
        import os
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true",
                         help="Mark everything in today's CSV as already-accounted-for. Writes nothing.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be written. Writes nothing, doesn't update the ledger.")
    args = parser.parse_args()

    log_path = config.LOG_PATH if not args.dry_run else None

    log(f"Loading CSV from {config.CSV_PATH}", log_path)
    df = load_fidelity_csv(config.CSV_PATH)
    log(f"Loaded {len(df)} rows", log_path)

    ledger = Ledger(config.LEDGER_PATH)

    if args.bootstrap:
        all_ids = [__import__("state").transaction_id(r) for _, r in df.iterrows()]
        ledger.mark_processed(all_ids)
        ledger.save()
        log(f"Bootstrap complete: marked {len(all_ids)} transactions as already-accounted-for. "
            f"Nothing written to the sheet. Run without --bootstrap next week onward.", log_path)
        return

    # Need the sheet connection for both dry-run (to seed matchers correctly)
    # and live mode.
    from sheets_sync import SheetsClient
    client = SheetsClient(config.SPREADSHEET_ID, config.CREDENTIALS_PATH)

    existing_shares_open = client.get_shares_open_rows(config.SHARES_TAB)
    existing_options_open = client.get_option_open_rows(config.OPTION_TAB)

    shares_rows, shares_warnings, shares_new_ids = build_shares_rows(
        df, ledger, existing_open_rows=existing_shares_open, exclude_tickers=config.CASH_SWEEP_TICKERS,
    )
    option_rows, option_warnings, option_new_ids = build_option_rows(
        df, ledger, existing_open_rows=existing_options_open,
    )
    dividend_rows = build_dividend_rows(df)

    log(f"shares: {len(shares_rows)} rows to write ({len(shares_warnings)} warnings)", log_path)
    log(f"option: {len(option_rows)} rows to write ({len(option_warnings)} warnings)", log_path)
    log(f"dividend: {len(dividend_rows)} candidate rows (dedup happens against sheet)", log_path)

    for w in shares_warnings + option_warnings:
        log(f"WARNING: {w}", log_path)

    if args.dry_run:
        print("\n=== DRY RUN: shares ===")
        print(shares_rows.to_string() if not shares_rows.empty else "(none)")
        print("\n=== DRY RUN: option ===")
        print(option_rows.to_string() if not option_rows.empty else "(none)")
        print("\n=== DRY RUN: dividend (before sheet-side dedup) ===")
        print(dividend_rows.drop(columns="dedup_key").to_string() if not dividend_rows.empty else "(none)")
        print("\nNothing written. Ledger not updated.")
        return

    # Live writes -- each tab is independent: a failure writing one (e.g. a
    # transient API error) shouldn't prevent the others from writing, and
    # shouldn't cause us to mark transactions as processed that never
    # actually made it into the sheet.
    shares_ok = option_ok = True

    if not shares_rows.empty:
        try:
            shares_result = client.upsert_shares_rows(config.SHARES_TAB, shares_rows)
            log(f"shares: {shares_result['inserted']} inserted, {shares_result['updated']} updated", log_path)
        except Exception as e:
            shares_ok = False
            log(f"ERROR writing shares tab: {e!r}. shares transactions this run will be retried next run.", log_path)
    else:
        log("shares: nothing to write", log_path)

    if not option_rows.empty:
        try:
            option_result = client.upsert_options_rows(config.OPTION_TAB, option_rows)
            log(f"option: {option_result['inserted']} inserted, {option_result['updated']} updated", log_path)
        except Exception as e:
            option_ok = False
            log(f"ERROR writing option tab: {e!r}. option transactions this run will be retried next run.", log_path)
    else:
        log("option: nothing to write", log_path)

    if not dividend_rows.empty:
        try:
            dividend_count = client.append_dividend_rows(config.DIVIDEND_TAB, dividend_rows)
            log(f"dividend: {dividend_count} new rows appended", log_path)
        except Exception as e:
            # dividends aren't ledger-tracked (dedup is content-based against
            # the sheet each run), so a failure here is safe to just retry
            # next run with no special handling needed.
            log(f"ERROR writing dividend tab: {e!r}. Will retry next run.", log_path)
    else:
        log("dividend: nothing to write", log_path)

    # Only mark a tab's transactions as processed if that tab's write
    # actually succeeded -- otherwise a crash mid-run would cause those
    # transactions to be silently skipped next run despite never having
    # been written.
    if shares_ok:
        ledger.mark_processed(shares_new_ids)
    if option_ok:
        ledger.mark_processed(option_new_ids)
    ledger.save()
    log(f"Ledger updated. Run complete{' (with errors above)' if not (shares_ok and option_ok) else ''}.", log_path)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback

        try:
            err_log_path = config.LOG_PATH
        except Exception:
            err_log_path = None
        log(f"FATAL: run failed with an uncaught error: {e!r}", err_log_path)
        log(traceback.format_exc(), err_log_path)
        sys.exit(1)
