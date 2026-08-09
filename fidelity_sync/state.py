"""
A small local JSON ledger recording which CSV transaction rows have already
been applied to the Google Sheet. Fidelity's CSV export is a rolling window
(re-downloading it weekly will re-include recent weeks' rows), so without
this, reruns would double-count buys/sells already reflected in the sheet.

Each transaction gets a stable id derived from the fields that uniquely
identify a raw CSV row. The ledger is just: {"processed_ids": [...]}.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable

import pandas as pd


def transaction_id(row: pd.Series) -> str:
    parts = [
        str(row["run_date"].date()),
        str(row["account"]),
        str(row["action"]),
        str(row["symbol"]),
        str(row["quantity"]),
        str(row["amount"]),
        str(row.get("settlement_date", "")),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.processed_ids: set[str] = set()
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
                self.processed_ids = set(data.get("processed_ids", []))

    def is_processed(self, txn_id: str) -> bool:
        return txn_id in self.processed_ids

    def mark_processed(self, txn_ids: Iterable[str]) -> None:
        self.processed_ids.update(txn_ids)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"processed_ids": sorted(self.processed_ids)}, f, indent=2)
