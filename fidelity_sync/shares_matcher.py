"""
Builds `shares` tab rows from classified CSV transactions.

Per ticker (account-agnostic -- positions in the same ticker across all 5
accounts are combined into one lot, per user instruction), maintains a
running position:
  - Consecutive BUYs before a full close are combined into one lot using a
    weighted-average price and summed quantity.
  - A SELL that fully closes the open quantity emits a CLOSED row and resets
    the lot.
  - A SELL that only partially reduces the open quantity emits a CLOSED row
    for the sold portion (at the lot's current weighted-avg buy price) and
    keeps the remainder open at the same weighted-avg price.
  - The still-open remainder (if any) is emitted as an OPEN row (buy info
    filled in, sell columns blank) so it shows up in the sheet immediately
    and gets updated in place when it's later sold.

Bootstrapping / avoiding double counting:
  - `existing_open_rows` seeds the lot tracker with whatever the sheet
    already shows as OPEN for each ticker -- so a sell whose matching buy
    predates this run's CSV window can still be resolved and close out that
    existing row, per your rule: "if there's a record of the buy in the
    sheet, update it; if not, skip."
  - `ledger` (see state.py) is consulted to skip CSV rows already applied
    in a prior run, since Fidelity's CSV window overlaps across weekly
    downloads. Only genuinely new rows are processed each run.
  - A sell that still can't be matched (no open lot tracked from the CSV
    *and* no existing OPEN row in the sheet) is skipped with a warning,
    per your instruction, rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from state import Ledger, transaction_id


@dataclass
class _OpenLot:
    buy_date: pd.Timestamp
    qty: float = 0.0
    cost: float = 0.0  # total dollar cost basis
    from_sheet: bool = False  # True if seeded from an existing sheet row
    dirty: bool = False  # True if modified by a new (unprocessed) transaction this run

    @property
    def avg_price(self) -> float:
        return self.cost / self.qty if self.qty else 0.0


def _seed_lots(existing_open_rows: Optional[pd.DataFrame]) -> dict:
    lots = {}
    if existing_open_rows is None or existing_open_rows.empty:
        return lots
    for _, r in existing_open_rows.iterrows():
        key = r["ticker"]
        buy_date = pd.to_datetime(r["buy_date"], dayfirst=True)
        qty = float(r["qty"])
        cost = qty * float(r["price"])
        existing = lots.get(key)
        if existing is None:
            lots[key] = _OpenLot(buy_date=buy_date, qty=qty, cost=cost, from_sheet=True)
        else:
            # Two open rows for the same ticker in the sheet -- merge via the
            # same weighted-average rule used elsewhere, keep the earlier
            # buy_date as the position's start.
            existing.qty += qty
            existing.cost += cost
            existing.buy_date = min(existing.buy_date, buy_date)
    return lots


def build_shares_rows(
    df: pd.DataFrame,
    ledger: Ledger,
    existing_open_rows: Optional[pd.DataFrame] = None,
    exclude_tickers: Optional[set] = None,
):
    """Returns (rows_df, warnings, new_txn_ids_to_mark_processed).

    rows_df columns: ticker, buy_date, price, qty, sell_price,
    sell_date, pnl, pnl_pct, status (OPEN/CLOSED), row_key
    """
    exclude_tickers = exclude_tickers or set()

    trades = df[df["row_type"].isin(["SHARE_BUY", "SHARE_SELL"])].copy()
    trades = trades[~trades["symbol"].isin(exclude_tickers)]
    trades = trades.sort_values("run_date")

    lots = _seed_lots(existing_open_rows)
    rows = []
    warnings = []
    new_ids = []

    for _, r in trades.iterrows():
        txn_id = transaction_id(r)
        if ledger.is_processed(txn_id):
            continue
        new_ids.append(txn_id)

        key = r["symbol"]
        qty = r["quantity"]
        price = r["price"]
        amount = r["amount"]
        date = r["run_date"]

        if r["row_type"] == "SHARE_BUY":
            lot = lots.get(key)
            if lot is None or lot.qty == 0:
                lots[key] = _OpenLot(buy_date=date, qty=qty, cost=qty * price, dirty=True)
            else:
                lot.qty += qty
                lot.cost += qty * price
                lot.dirty = True
            continue

        # SHARE_SELL: quantity is negative in the CSV
        sell_qty = abs(qty)
        proceeds = amount

        lot = lots.get(key)
        if lot is None or lot.qty <= 0:
            warnings.append(
                f"SELL of {sell_qty} {r['symbol']} (account: {r['account']}) on "
                f"{date.date()}: no open buy tracked in this CSV window and "
                f"no existing OPEN row in the sheet. Skipped."
            )
            continue

        if sell_qty > lot.qty + 1e-9:
            warnings.append(
                f"SELL of {sell_qty} {r['symbol']} (account: {r['account']}) on "
                f"{date.date()} exceeds the {lot.qty} tracked as open. "
                f"Closing the {lot.qty} we can account for -- P&L on this "
                f"row may be understated."
            )
            sell_qty = lot.qty

        closed_cost = lot.avg_price * sell_qty
        this_sale_proceeds = proceeds * (sell_qty / abs(qty))
        pnl = this_sale_proceeds - closed_cost
        pnl_pct = (pnl / closed_cost * 100) if closed_cost else None

        # Only a sell that fully exhausts a seeded (sheet-originating) lot
        # should overwrite that existing row in place -- a partial sell must
        # always insert a new row for the closed slice, leaving the reduced
        # remainder to update the original row separately (see the final
        # loop below). Otherwise a partial close would clobber the row
        # meant to still show the leftover open position.
        fully_closes_seeded_lot = lot.from_sheet and (lot.qty - sell_qty) <= 1e-9

        row_key = f"{r['symbol']}|{lot.buy_date.date()}|{date.date()}"
        rows.append(
            {
                "ticker": r["symbol"],
                "buy_date": lot.buy_date.strftime("%d.%m.%Y"),
                "price": round(lot.avg_price, 4),
                "qty": sell_qty,
                "sell_price": round(price, 4),
                "sell_date": date.strftime("%d.%m.%Y"),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
                "status": "CLOSED",
                "row_key": row_key,
                "matches_existing_open_row": fully_closes_seeded_lot,
            }
        )

        lot.qty -= sell_qty
        lot.cost -= closed_cost
        lot.dirty = True
        if lot.qty <= 1e-9:
            lots.pop(key, None)

    # Remaining open lots. Skip re-emitting a lot that came from the sheet
    # and wasn't touched this run (nothing changed, no need to rewrite it);
    # emit everything else (new lots, or seeded lots that got new buys /
    # partial sells this run).
    for ticker, lot in lots.items():
        if lot.qty <= 1e-9:
            continue
        if lot.from_sheet and not lot.dirty:
            continue
        rows.append(
            {
                "ticker": ticker,
                "buy_date": lot.buy_date.strftime("%d.%m.%Y"),
                "price": round(lot.avg_price, 4),
                "qty": round(lot.qty, 4),
                "sell_price": None,
                "sell_date": None,
                "pnl": None,
                "pnl_pct": None,
                "status": "OPEN",
                "row_key": f"{ticker}|{lot.buy_date.date()}|OPEN",
                "matches_existing_open_row": lot.from_sheet,
            }
        )

    return pd.DataFrame(rows), warnings, new_ids
