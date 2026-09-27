"""
Parses a Fidelity Accounts_History.csv export and classifies each row into
one of: SHARE_BUY, SHARE_SELL, OPTION_OPEN, OPTION_CLOSE, OPTION_EXPIRED,
OPTION_ASSIGNED, DIVIDEND, REINVESTMENT, OTHER.

Fidelity's export has a few quirks this module works around:
  - A BOM + blank lines before the real header row
  - A multi-paragraph legal disclaimer + "Date downloaded ..." footer after
    the last data row
  - Option rows encode the contract in the Symbol column as, e.g.,
    " -SPY260731C751" (leading space, dash, then TICKER+YYMMDD+C/P+STRIKE)
  - Quantity is signed: negative = sold/closed short side, positive = bought
  - The free-text Action column carries the real semantics (OPENING
    TRANSACTION, CLOSING TRANSACTION, EXPIRED, ASSIGNED, YOU BOUGHT,
    YOU SOLD, DIVIDEND RECEIVED, REINVESTMENT, ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

OPTION_SYMBOL_RE = re.compile(
    r"^\s*-(?P<underlying>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])(?P<strike>[\d.]+)\s*$"
)


@dataclass
class OptionContract:
    underlying: str
    option_type: str  # "CALL" or "PUT"
    strike: float
    expiration: datetime


def parse_option_symbol(symbol: str) -> Optional[OptionContract]:
    """Parse a Fidelity option symbol like ' -SPY260731C751' into its parts.
    Returns None if `symbol` doesn't match the option pattern (i.e. it's a
    plain equity/ETF symbol or blank)."""
    if not isinstance(symbol, str):
        return None
    m = OPTION_SYMBOL_RE.match(symbol)
    if not m:
        return None
    yy, mm, dd = m.group("yy"), m.group("mm"), m.group("dd")
    expiration = datetime.strptime(f"20{yy}-{mm}-{dd}", "%Y-%m-%d")
    return OptionContract(
        underlying=m.group("underlying"),
        option_type="CALL" if m.group("cp") == "C" else "PUT",
        strike=float(m.group("strike")),
        expiration=expiration,
    )


def load_fidelity_csv(path: str) -> pd.DataFrame:
    """Load and clean a raw Fidelity Accounts_History.csv export.

    Returns a DataFrame with the original columns (renamed to snake_case)
    plus derived columns: row_type, option (OptionContract or None).
    """
    # Fidelity's export has leading blank lines and a BOM; skip down to the
    # real header by finding the line that starts with "Run Date,".
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()

    header_idx = next(i for i, line in enumerate(lines) if line.startswith("Run Date,"))
    # The footer starts at the first blank line after the header, or a line
    # that begins with a quote (the disclaimer paragraphs are quoted CSV
    # cells). We find the last row that actually starts with a date.
    date_re = re.compile(r"^\d{2}/\d{2}/\d{4},")
    last_data_idx = header_idx
    for i in range(header_idx + 1, len(lines)):
        if date_re.match(lines[i]):
            last_data_idx = i
        elif lines[i].strip() == "":
            continue
        else:
            # Once we hit a non-date, non-blank line after data has started,
            # we've reached the disclaimer footer.
            if last_data_idx > header_idx:
                break

    csv_block = lines[header_idx : last_data_idx + 1]
    from io import StringIO

    df = pd.read_csv(StringIO("".join(csv_block)))

    df = df.rename(
        columns={
            "Run Date": "run_date",
            "Account": "account",
            "Account Number": "account_number",
            "Action": "action",
            "Symbol": "symbol",
            "Description": "description",
            "Type": "reg_type",  # Cash / Margin
            "Price ($)": "price",
            "Quantity": "quantity",
            "Commission ($)": "commission",
            "Fees ($)": "fees",
            "Accrued Interest ($)": "accrued_interest",
            "Amount ($)": "amount",
            "Settlement Date": "settlement_date",
        }
    )

    df["run_date"] = pd.to_datetime(df["run_date"], format="%m/%d/%Y")
    df["symbol"] = df["symbol"].fillna("").astype(str)
    df["action_upper"] = df["action"].str.upper()

    df["option"] = df["symbol"].apply(parse_option_symbol)
    df["is_option"] = df["option"].notna()

    df["row_type"] = df.apply(_classify_row, axis=1)

    return df


def _classify_row(row) -> str:
    action = row["action_upper"]
    is_option = row["is_option"]

    if "DIVIDEND" in action:
        return "DIVIDEND"
    if action.startswith("REINVESTMENT"):
        return "REINVESTMENT"
    if is_option:
        if "EXPIRED" in action:
            return "OPTION_EXPIRED"
        if "ASSIGNED" in action:
            return "OPTION_ASSIGNED"
        if "OPENING TRANSACTION" in action:
            return "OPTION_OPEN"
        if "CLOSING TRANSACTION" in action:
            return "OPTION_CLOSE"
        return "OPTION_OTHER"
    if action.startswith("YOU BOUGHT"):
        return "SHARE_BUY"
    if action.startswith("YOU SOLD"):
        return "SHARE_SELL"
    # Shares moved in from another broker: no price in the CSV, so the
    # matcher books them as a buy at a placeholder price (see shares_matcher).
    if action.startswith("RECEIVED FROM YOU") and row["symbol"] and row["quantity"] > 0:
        return "SHARE_TRANSFER_IN"
    return "OTHER"


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Accounts_History.csv"
    df = load_fidelity_csv(path)
    print(f"Loaded {len(df)} rows")
    print(df["row_type"].value_counts())
    print()
    print("Sample option rows:")
    print(
        df[df["is_option"]][["run_date", "account", "symbol", "row_type", "quantity", "price", "amount"]]
        .head(10)
        .to_string()
    )
