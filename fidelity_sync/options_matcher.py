"""
Builds `option` tab rows from classified CSV transactions.

Groups by contract (underlying, type, strike, expiration). Within a
contract, opening legs (OPTION_OPEN) are paired against closing legs
(OPTION_CLOSE, OPTION_EXPIRED, OPTION_ASSIGNED) the same way shares are:
weighted-avg the open side, close what closes, keep the remainder open.

EXPIRED contracts close at $0 (worthless). ASSIGNED contracts also close at
$0 in the option's own P&L (the assignment's economic effect shows up in
the underlying's SHARE_BUY/SHARE_SELL row instead -- e.g. an assigned put
becomes a stock buy, which the shares matcher already captures separately).

Same bootstrapping/ledger/exclusion pattern as shares_matcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from state import Ledger, transaction_id


@dataclass
class _OpenLeg:
    open_date: pd.Timestamp
    qty: float = 0.0  # always positive; direction tracked separately
    cost: float = 0.0  # signed dollar amount (net of fees): + = net debit paid, - = net credit received
    notional: float = 0.0  # raw quoted price * qty * 100 (unsigned), for display-price purposes
    direction: str = ""  # "LONG" (bought to open) or "SHORT" (sold to open)
    from_sheet: bool = False
    dirty: bool = False

    @property
    def avg_price(self) -> float:
        # Raw weighted-average quoted premium per contract (matches how the
        # sheet already records prices -- not netted for commission/fees;
        # fees still flow into P&L via `cost`, just not into the price shown).
        return self.notional / (self.qty * 100) if self.qty else 0.0


def _contract_key(row) -> tuple:
    opt = row["option"]
    return (opt.underlying, opt.option_type, opt.strike, opt.expiration.date())


def _seed_legs(existing_open_rows: Optional[pd.DataFrame]) -> dict:
    legs = {}
    if existing_open_rows is None or existing_open_rows.empty:
        return legs
    for _, r in existing_open_rows.iterrows():
        key = (r["underlying"], r["option_type"], float(r["strike"]), pd.to_datetime(r["expiration"], dayfirst=True).date())
        open_date = pd.to_datetime(r["open_date"], dayfirst=True)
        qty = abs(float(r["open_qty"]))
        direction = "SHORT" if float(r["open_qty"]) < 0 else "LONG"
        notional = float(r["open_price"]) * qty * 100
        cost = notional * (-1 if direction == "SHORT" else 1)
        existing = legs.get(key)
        if existing is None:
            legs[key] = _OpenLeg(open_date=open_date, qty=qty, cost=cost, notional=notional, direction=direction, from_sheet=True)
        else:
            # Two open rows for the same contract in the sheet -- merge
            # (same direction assumed; mixed-direction duplicates on the
            # same contract would be unusual and aren't handled specially).
            existing.qty += qty
            existing.cost += cost
            existing.notional += notional
            existing.open_date = min(existing.open_date, open_date)
    return legs


def build_option_rows(
    df: pd.DataFrame,
    ledger: Ledger,
    existing_open_rows: Optional[pd.DataFrame] = None,
):
    """Returns (rows_df, warnings, new_txn_ids_to_mark_processed).

    rows_df columns: underlying, option_type, strike, expiration, open_date,
    open_qty (signed), open_price, close_date, close_qty (signed),
    close_price, pnl, status (OPEN/CLOSED), row_key
    """
    opt_rows = df[df["row_type"].isin(
        ["OPTION_OPEN", "OPTION_CLOSE", "OPTION_EXPIRED", "OPTION_ASSIGNED"]
    )].copy()
    # Opens before closes within a day: Fidelity's row order inside a day is
    # not chronological, and a same-day close seen first would be skipped.
    opt_rows["_rank"] = (opt_rows["row_type"] != "OPTION_OPEN").astype(int)
    opt_rows = opt_rows.sort_values(["run_date", "_rank"], kind="stable")

    legs = _seed_legs(existing_open_rows)
    rows = []
    warnings = []
    new_ids = []

    for _, r in opt_rows.iterrows():
        txn_id = transaction_id(r)
        if ledger.is_processed(txn_id):
            continue
        new_ids.append(txn_id)

        key = _contract_key(r)
        qty = r["quantity"]  # signed: negative = sold, positive = bought
        price = r["price"]
        amount = r["amount"]
        date = r["run_date"]
        opt = r["option"]

        if r["row_type"] == "OPTION_OPEN":
            direction = "SHORT" if qty < 0 else "LONG"
            signed_cost = -amount  # amount is cash flow; cost basis is the opposite sign
            leg_notional = price * abs(qty) * 100
            leg = legs.get(key)
            if leg is None or leg.qty == 0:
                legs[key] = _OpenLeg(
                    open_date=date, qty=abs(qty), cost=signed_cost, notional=leg_notional,
                    direction=direction, dirty=True,
                )
            else:
                leg.qty += abs(qty)
                leg.cost += signed_cost
                leg.notional += leg_notional
                leg.dirty = True
            continue

        # CLOSE / EXPIRED / ASSIGNED: closing side
        close_qty = abs(qty)
        leg = legs.get(key)
        if leg is None or leg.qty <= 0:
            warnings.append(
                f"{r['row_type']} of {close_qty} {opt.underlying} {opt.option_type} "
                f"{opt.strike} exp {opt.expiration.date()} on {date.date()}: no open "
                f"leg tracked in this CSV window and no existing OPEN row in the "
                f"sheet. Skipped."
            )
            continue

        if close_qty > leg.qty + 1e-9:
            warnings.append(
                f"{r['row_type']} of {close_qty} {opt.underlying} {opt.option_type} "
                f"{opt.strike} exp {opt.expiration.date()} on {date.date()} exceeds "
                f"the {leg.qty} tracked as open. Closing the {leg.qty} we can "
                f"account for -- P&L on this row may be understated."
            )
            close_qty = leg.qty

        portion = close_qty / leg.qty
        open_cost_portion = leg.cost * portion  # signed
        open_price = leg.avg_price

        if r["row_type"] in ("OPTION_EXPIRED",):
            close_price = 0.0
            # expired worthless: SHORT (sold to open) keeps the full credit
            # as profit; LONG (bought to open) loses the full debit
            pnl = -open_cost_portion
        elif r["row_type"] == "OPTION_ASSIGNED":
            close_price = 0.0
            # Assignment's economic effect is captured in the underlying's
            # share buy/sell row; the option leg itself is recorded at $0
            # close (consistent with EXPIRED) so the option P&L here isn't
            # double-counted against the shares tab.
            pnl = -open_cost_portion
        else:  # OPTION_CLOSE
            close_price = price
            close_cash = amount  # signed cash flow of the closing trade
            pnl = close_cash - open_cost_portion

        row_key = f"{opt.underlying}|{opt.option_type}|{opt.strike}|{opt.expiration.date()}|{leg.open_date.date()}|{date.date()}"
        # Same rule as shares: only a close that fully exhausts a seeded leg
        # updates the existing sheet row in place; a partial close always
        # inserts, leaving the update-in-place for the final open remainder.
        fully_closes_seeded_leg = leg.from_sheet and (leg.qty - close_qty) <= 1e-9
        rows.append(
            {
                "underlying": opt.underlying,
                "option_type": opt.option_type,
                "strike": opt.strike,
                "expiration": opt.expiration.strftime("%d.%m.%Y"),
                "open_date": leg.open_date.strftime("%d.%m.%Y"),
                "open_qty": close_qty if leg.direction == "LONG" else -close_qty,
                "open_price": round(open_price, 4),
                "close_date": date.strftime("%d.%m.%Y"),
                "close_qty": -close_qty if leg.direction == "LONG" else close_qty,
                "close_price": round(close_price, 4),
                "pnl": round(pnl, 2),
                "status": "CLOSED",
                "close_reason": r["row_type"],
                "row_key": row_key,
                "matches_existing_open_row": fully_closes_seeded_leg,
            }
        )

        leg.notional -= leg.notional * portion  # reduce proportionally, matching cost
        leg.qty -= close_qty
        leg.cost -= open_cost_portion
        leg.dirty = True
        if leg.qty <= 1e-9:
            legs.pop(key, None)

    for key, leg in legs.items():
        if leg.qty <= 1e-9:
            continue
        if leg.from_sheet and not leg.dirty:
            continue
        underlying, option_type, strike, expiration = key
        rows.append(
            {
                "underlying": underlying,
                "option_type": option_type,
                "strike": strike,
                "expiration": expiration.strftime("%d.%m.%Y"),
                "open_date": leg.open_date.strftime("%d.%m.%Y"),
                "open_qty": leg.qty if leg.direction == "LONG" else -leg.qty,
                "open_price": round(leg.avg_price, 4),
                "close_date": None,
                "close_qty": None,
                "close_price": None,
                "pnl": None,
                "status": "OPEN",
                "close_reason": None,
                "row_key": f"{underlying}|{option_type}|{strike}|{expiration}|{leg.open_date.date()}|OPEN",
                "matches_existing_open_row": leg.from_sheet,
            }
        )

    return pd.DataFrame(rows), warnings, new_ids
