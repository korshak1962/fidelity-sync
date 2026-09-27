"""
Thin wrapper around gspread for the three tabs: shares, option, DIVIDEND.

Reading:
  - get_shares_open_rows() / get_option_open_rows() return the currently
    OPEN positions (sell/close columns blank) as DataFrames, used to seed
    the matchers (see shares_matcher.py / options_matcher.py).
  - get_dividend_keys() returns the dedup keys already present, so we don't
    re-append the same dividend/reinvestment row twice.

Writing:
  - upsert_shares_rows() / upsert_options_rows(): for each row, if it's
    flagged matches_existing_open_row, find that row in the sheet (by
    ticker+buy_date, or contract+open_date) and overwrite its close-side
    columns in place. Otherwise, append a new row. Note columns (shares
    tab) are never touched.
  - append_dividend_rows(): straight append, after filtering out any whose
    dedup key already exists in the sheet.

Column layout (1-indexed, matches your actual sheet):
  shares: A ticker | B buy date | C price | D qnty | E price (sell)
          | F date (sell) | G p&L | H p&l % | I note | J note
  option: A underlying | B type | C strike | D expiration | E open date
          | F open qty | G open price | H close date | I close qty
          | J close price | K P&L
  DIVIDEND (new tab, headers written on first setup):
          A Date | B Account | C Symbol | D Description | E Amount | F Type
"""

from __future__ import annotations

import re
from typing import Optional

import gspread
from gspread.http_client import BackOffHTTPClient
import pandas as pd
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _clean(value):
    """Convert missing values to '' for JSON-safe writes to the Sheets API.

    Building a DataFrame from row dicts where some rows have a real float
    and others have None (e.g. an OPEN row's sell_price) makes pandas
    upcast the whole column to float64 and silently turn None into NaN.
    NaN is a float, so `is not None` and `or ""` both fail to catch it,
    and NaN isn't valid JSON -- gspread's request then errors out.
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return value

SHARES_COLS = ["ticker", "buy_date", "price", "qty", "sell_price", "sell_date", "pnl", "pnl_pct"]
OPTION_COLS = [
    "underlying", "option_type", "strike", "expiration", "open_date",
    "open_qty", "open_price", "close_date", "close_qty", "close_price", "pnl",
]
DIVIDEND_HEADER = ["Date", "Account", "Symbol", "Description", "Amount", "Type"]


class SheetsClient:
    def __init__(self, spreadsheet_id: str, credentials_path: str):
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        # BackOffHTTPClient retries 429 (60 writes/minute quota) instead of failing
        # mid-upsert, which would leave rows written but not in the ledger.
        self.gc = gspread.authorize(creds, http_client=BackOffHTTPClient)
        self.sh = self.gc.open_by_key(spreadsheet_id)

    # ---------- reading ----------

    def get_shares_open_rows(self, tab_name: str) -> pd.DataFrame:
        ws = self.sh.worksheet(tab_name)
        values = ws.get_all_values()
        rows = []
        for i, row in enumerate(values[1:], start=2):  # skip header row
            if len(row) < 4 or not row[0].strip():
                continue
            sell_price = row[4].strip() if len(row) > 4 else ""
            if sell_price:
                continue  # already closed, not an OPEN row
            rows.append({"row_num": i, "ticker": row[0].strip(), "buy_date": row[1].strip(),
                         "price": row[2].strip(), "qty": row[3].strip()})
        return pd.DataFrame(rows)

    def get_option_open_rows(self, tab_name: str) -> pd.DataFrame:
        ws = self.sh.worksheet(tab_name)
        values = ws.get_all_values()
        rows = []
        for i, row in enumerate(values, start=1):
            if len(row) < 7 or not row[0].strip():
                continue
            close_date = row[7].strip() if len(row) > 7 else ""
            if close_date:
                continue  # already closed
            rows.append({
                "row_num": i, "underlying": row[0].strip(), "option_type": row[1].strip(),
                "strike": row[2].strip(), "expiration": row[3].strip(),
                "open_date": row[4].strip(), "open_qty": row[5].strip(), "open_price": row[6].strip(),
            })
        return pd.DataFrame(rows)

    def get_dividend_keys(self, tab_name: str) -> set:
        try:
            ws = self.sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            return set()
        values = ws.get_all_values()
        keys = set()
        for row in values[1:]:
            if len(row) < 6:
                continue
            date, account, symbol, _desc, amount, type_ = row[0], row[1], row[2], row[3], row[4], row[5]
            keys.add(f"{date}|{account}|{symbol}|{type_}|{amount}")
        return keys

    # ---------- writing ----------

    def upsert_shares_rows(self, tab_name: str, rows: pd.DataFrame) -> dict:
        """Returns {'updated': n, 'inserted': n}."""
        ws = self.sh.worksheet(tab_name)
        updated = inserted = 0
        for _, r in rows.iterrows():
            if r.get("matches_existing_open_row"):
                row_num = self._find_shares_row(ws, r["ticker"], r["buy_date"])
                if row_num:
                    ws.update(
                        values=[[
                            r["price"], r["qty"], _clean(r.get("sell_price")),
                            _clean(r.get("sell_date")), _clean(r.get("pnl")),
                            _clean(r.get("pnl_pct")),
                        ]],
                        range_name=f"C{row_num}:H{row_num}",
                    )
                    if r.get("price_placeholder"):
                        self._mark_placeholder_price(ws, row_num, closed=bool(_clean(r.get("sell_price"))))
                    updated += 1
                    continue
            resp = ws.append_row([
                r["ticker"], r["buy_date"], r["price"], r["qty"],
                _clean(r.get("sell_price")), _clean(r.get("sell_date")),
                _clean(r.get("pnl")), _clean(r.get("pnl_pct")),
            ], value_input_option="USER_ENTERED", table_range="A1")
            if r.get("price_placeholder"):
                # updatedRange looks like "shares!A27:H27"
                row_num = int(re.search(r"(\d+):", resp["updates"]["updatedRange"]).group(1))
                self._mark_placeholder_price(ws, row_num, closed=bool(_clean(r.get("sell_price"))))
            inserted += 1
        return {"updated": updated, "inserted": inserted}

    def upsert_options_rows(self, tab_name: str, rows: pd.DataFrame) -> dict:
        ws = self.sh.worksheet(tab_name)
        updated = inserted = 0
        for _, r in rows.iterrows():
            if r.get("matches_existing_open_row"):
                row_num = self._find_option_row(
                    ws, r["underlying"], r["option_type"], r["strike"], r["expiration"], r["open_date"]
                )
                if row_num:
                    ws.update(
                        values=[[
                            _clean(r.get("close_date")), _clean(r.get("close_qty")),
                            _clean(r.get("close_price")),
                            _clean(r.get("pnl")),
                        ]],
                        range_name=f"H{row_num}:K{row_num}",
                    )
                    updated += 1
                    continue
            ws.append_row([
                r["underlying"], r["option_type"], r["strike"], r["expiration"],
                r["open_date"], r["open_qty"], r["open_price"],
                _clean(r.get("close_date")), _clean(r.get("close_qty")),
                _clean(r.get("close_price")),
                _clean(r.get("pnl")),
            ], value_input_option="USER_ENTERED", table_range="A1")
            inserted += 1
        return {"updated": updated, "inserted": inserted}

    def append_dividend_rows(self, tab_name: str, rows: pd.DataFrame) -> int:
        ws = self._get_or_create_dividend_tab(tab_name)
        existing_keys = self.get_dividend_keys(tab_name)
        new_rows = rows[~rows["dedup_key"].isin(existing_keys)]
        # One append_rows call, not append_row per row: a quarter of history is
        # ~60 dividends, which exceeds the 60 writes/minute Sheets API quota.
        values = [
            [r["date"], r["account"], r["symbol"] if pd.notna(r["symbol"]) else "",
             r["description"], r["amount"], r["type"]]
            for _, r in new_rows.iterrows()
        ]
        if values:
            ws.append_rows(values, value_input_option="USER_ENTERED", table_range="A1")
        return len(new_rows)

    # ---------- helpers ----------

    def _mark_placeholder_price(self, ws, row_num: int, closed: bool):
        """Red buy price = placeholder (transfer-in, cost basis unknown) to be
        corrected by hand. On a closed row P&L is written as formulas so it
        follows the corrected price."""
        ws.format(f"C{row_num}", {"textFormat": {"foregroundColor": {"red": 1, "green": 0, "blue": 0}}})
        if closed:
            n = row_num
            ws.update(
                values=[[f"=(E{n}-C{n})*D{n}", f"=(E{n}-C{n})/C{n}*100"]],
                range_name=f"G{n}:H{n}", value_input_option="USER_ENTERED",
            )

    def _get_or_create_dividend_tab(self, tab_name: str):
        try:
            return self.sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=tab_name, rows=1000, cols=len(DIVIDEND_HEADER))
            ws.append_row(DIVIDEND_HEADER)
            return ws

    def _find_shares_row(self, ws, ticker: str, buy_date: str) -> Optional[int]:
        values = ws.get_all_values()
        for i, row in enumerate(values, start=1):
            if len(row) >= 2 and row[0].strip() == ticker and row[1].strip() == buy_date:
                return i
        return None

    def _find_option_row(self, ws, underlying, option_type, strike, expiration, open_date) -> Optional[int]:
        values = ws.get_all_values()
        for i, row in enumerate(values, start=1):
            if len(row) < 5:
                continue
            # Strike compared as a number: the sheet displays "25.00", the
            # matcher produces 25.0 -- a string compare never matched, so every
            # close of a sheet-seeded leg was appended as a duplicate row.
            try:
                same_strike = abs(float(row[2]) - float(strike)) < 1e-9
            except (TypeError, ValueError):
                same_strike = False
            if (row[0].strip() == underlying and row[1].strip() == option_type
                    and same_strike and row[3].strip() == expiration
                    and row[4].strip() == open_date):
                return i
        return None
