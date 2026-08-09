"""
Builds rows for the DIVIDEND tab: every DIVIDEND row from the CSV, across
all 5 accounts, as separate line items (not netted). REINVESTMENT rows are
deliberately excluded (per user preference) -- they're the reinvestment
leg of the same cash dividend, not a separate event worth tracking here.
"""

from __future__ import annotations

import re

import pandas as pd

# Strip the boilerplate ("as of ...", the trailing "(Cash)"/"(Margin)" tag,
# and the repeated symbol-in-parens) to get a clean description.
_DATE_SUFFIX_RE = re.compile(r"\s+as of \d{4}-\d{2}-\d{2}", re.IGNORECASE)
_ACCOUNT_TAG_RE = re.compile(r"\s*\((Cash|Margin)\)\s*$")


def _clean_description(row) -> str:
    desc = row["description"]
    if not isinstance(desc, str) or not desc or desc == "No Description":
        # fall back to the action text, cleaned up
        desc = row["action"]
    desc = _DATE_SUFFIX_RE.sub("", desc)
    desc = _ACCOUNT_TAG_RE.sub("", desc)
    return desc.strip()


def build_dividend_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame with columns: date, account, symbol, description,
    amount, type — one row per DIVIDEND CSV row."""
    mask = df["row_type"] == "DIVIDEND"
    sub = df.loc[mask].copy()

    out = pd.DataFrame(
        {
            "date": sub["run_date"].dt.strftime("%m/%d/%Y"),
            "account": sub["account"],
            "symbol": sub["symbol"].replace("", pd.NA),
            "description": sub.apply(_clean_description, axis=1),
            "amount": sub["amount"],
            "type": sub["row_type"],
        }
    )

    # Natural key for de-duping against what's already in the sheet.
    out["dedup_key"] = (
        out["date"] + "|" + out["account"] + "|" + out["symbol"].fillna("") + "|"
        + out["type"] + "|" + out["amount"].astype(str)
    )

    return out.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    import sys

    from csv_parser import load_fidelity_csv

    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Accounts_History.csv"
    df = load_fidelity_csv(path)
    rows = build_dividend_rows(df)
    print(f"{len(rows)} dividend/reinvestment rows")
    print(rows.drop(columns="dedup_key").to_string())
