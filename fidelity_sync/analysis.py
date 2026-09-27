"""
Rebuilds the `analysis` tab from the shares / option / DIVIDEND tabs:
  - top 5 and bottom 5 closed trades by % profit, shares and options listed
    separately (shares: price change %; options: P&L as % of the premium
    paid or received -- the two aren't comparable in one ranking);
  - realized P&L (USD) by month of closing, since the first trade, plus
    dividends and a running total.

Values are static; rerun after an import to refresh:
    python analysis.py
Rows still carrying the transfer-in placeholder price (see shares_matcher)
are left out of the rankings -- their % isn't real yet.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import gspread

import config
from shares_matcher import TRANSFER_IN_PLACEHOLDER_PRICE
from sheets_sync import SheetsClient

ANALYSIS_TAB = "analysis"
TOP_N = 5


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month(date_value, fmt: str) -> str:
    # Cells the sheet parsed as dates come back as serial day numbers.
    if isinstance(date_value, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=date_value)).strftime("%Y-%m")
    return datetime.strptime(date_value, fmt).strftime("%Y-%m")


def _closed_shares(values):
    out = []
    for r in values[1:]:
        r = list(r) + [""] * (8 - len(r))
        pnl, pct, price = _num(r[6]), _num(r[7]), _num(r[2])
        if not r[0] or not r[5] or pnl is None:
            continue
        out.append({"ticker": r[0], "open": r[1], "close": r[5], "qty": r[3],
                    "pnl": pnl, "pct": pct,
                    "placeholder": price is not None and abs(price - TRANSFER_IN_PLACEHOLDER_PRICE) < 1e-9})
    return out


def _closed_options(values):
    out = []
    for r in values[1:]:
        r = list(r) + [""] * (11 - len(r))
        pnl, qty, premium = _num(r[10]), _num(r[5]), _num(r[6])
        if not r[0] or not r[7] or pnl is None:
            continue
        basis = abs(qty or 0) * (premium or 0) * 100
        out.append({"ticker": f"{r[0]} {r[1]} {r[2]} exp {r[3]}", "open": r[4], "close": r[7],
                    "qty": r[5], "pnl": pnl, "pct": pnl / basis * 100 if basis else None,
                    "placeholder": False})
    return out


def _ranking(title, trades):
    ranked = sorted((t for t in trades if t["pct"] is not None and not t["placeholder"]),
                    key=lambda t: t["pct"], reverse=True)
    header = ["", "Trade", "Opened", "Closed", "Qty", "P&L $", "P&L %"]
    row = lambda i, t: [i, t["ticker"], t["open"], t["close"], t["qty"], round(t["pnl"], 2), round(t["pct"], 2)]
    block = [[f"{title} — best {TOP_N} by %"], header]
    block += [row(i, t) for i, t in enumerate(ranked[:TOP_N], 1)]
    block += [[], [f"{title} — worst {TOP_N} by %"], header]
    block += [row(i, t) for i, t in enumerate(ranked[::-1][:TOP_N], 1)]
    return block


def build(client: SheetsClient) -> list:
    shares = _closed_shares(client.sh.worksheet(config.SHARES_TAB).get_all_values(value_render_option="UNFORMATTED_VALUE"))
    options = _closed_options(client.sh.worksheet(config.OPTION_TAB).get_all_values(value_render_option="UNFORMATTED_VALUE"))
    dividends = client.sh.worksheet(config.DIVIDEND_TAB).get_all_values(value_render_option="UNFORMATTED_VALUE")[1:]

    monthly = {}
    def add(month, col, amount):
        monthly.setdefault(month, [0.0, 0.0, 0.0])[col] += amount
    for t in shares:
        add(_month(t["close"], "%d.%m.%Y"), 0, t["pnl"])
    for t in options:
        add(_month(t["close"], "%d.%m.%Y"), 1, t["pnl"])
    for r in dividends:
        if r and r[0] and _num(r[4]) is not None:
            add(_month(r[0], "%m/%d/%Y"), 2, _num(r[4]))

    out = [[f"Updated {datetime.now():%d.%m.%Y %H:%M}. Closed trades only; the $100 transfer-in placeholder rows are excluded from the rankings."], []]
    out += _ranking("Shares", shares) + [[]]
    out += _ranking("Options", options) + [[]]
    # The month rows come last: the charts read an open-ended range below the
    # header, so the all-time total sits above it rather than below.
    totals = [sum(v[i] for v in monthly.values()) for i in range(3)]
    out += [["P&L by month (USD, by closing date)"],
            ["All months", *(round(x, 2) for x in totals), round(sum(totals), 2), ""],
            ["Month", "Shares", "Options", "Dividends", "Total", "Cumulative"]]
    cum = 0.0
    for m in sorted(monthly):
        s, o, d = monthly[m]
        cum += s + o + d
        out.append([m, round(s, 2), round(o, 2), round(d, 2), round(s + o + d, 2), round(cum, 2)])
    return out


def write(client: SheetsClient, rows: list, recreate_charts: bool = False) -> None:
    try:
        ws = client.sh.worksheet(ANALYSIS_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = client.sh.add_worksheet(title=ANALYSIS_TAB, rows=400, cols=20)
    width = max(len(r) for r in rows)
    # Months go in as real dates (serial day numbers shown as yyyy-mm): a text
    # axis is categorical, and Sheets only offers gridlines on a date axis.
    header = next(i for i, r in enumerate(rows) if r and r[0] == "Month")
    cells = [list(r) + [""] * (width - len(r)) for r in rows]
    for r in cells[header + 1:]:
        r[0] = (datetime.strptime(r[0], "%Y-%m") - datetime(1899, 12, 30)).days
    ws.update(values=cells, range_name="A1", value_input_option="RAW")
    ws.format(f"A{header + 2}:A{len(cells)}", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm"},
                                                 "horizontalAlignment": "LEFT"})
    # Bold the section titles and column headers.
    bold = [f"A{i}:G{i}" for i, r in enumerate(rows, 1)
            if r and isinstance(r[0], str) and (len(r) == 1 or r[0] in ("", "Month") and r[1] in ("Trade", "Shares"))]
    if bold:
        ws.batch_format([{"range": a, "format": {"textFormat": {"bold": True}}} for a in bold])
    _write_charts(client, ws, rows, recreate=recreate_charts)


# Categorical slots 1-3 (blue, orange, aqua) -- validated for CVD separation on
# a light surface. The cumulative line is a different entity, so it gets a
# neutral ink rather than a series hue.
SERIES_COLORS = {"Shares": "#2a78d6", "Options": "#eb6834", "Dividends": "#1baf7a"}
CUMULATIVE_COLOR = "#52514e"
CHART_ROWS = 240  # months of room below the header: 20 years


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def _write_charts(client: SheetsClient, ws, rows: list, recreate: bool = False) -> None:
    """Two charts beside the monthly table: P&L per month stacked by source,
    and the cumulative total. Separate charts, not one dual-axis chart --
    the two are on very different scales.

    Created once and then left alone, so formatting done by hand in the chart
    editor (e.g. axis gridlines, which the Sheets API can't set) survives a
    refresh. Their range runs to CHART_ROWS below the header, so new months
    show up without touching the charts. Pass --recreate-charts to rebuild."""
    header = next(i for i, r in enumerate(rows) if r and r[0] == "Month")  # 0-based
    end = header + CHART_ROWS
    sid = ws.id

    def col(c):
        return {"sourceRange": {"sources": [{"sheetId": sid, "startRowIndex": header, "endRowIndex": end,
                                             "startColumnIndex": c, "endColumnIndex": c + 1}]}}

    meta = client.sh.fetch_sheet_metadata({"fields": "sheets(properties(sheetId),charts(chartId))"})
    existing = [ch["chartId"] for s in meta["sheets"] if s["properties"]["sheetId"] == sid
                for ch in s.get("charts", [])]
    if existing and not recreate:
        return
    requests = [{"deleteEmbeddedObject": {"objectId": cid}} for cid in existing]
    # The anchor column and the open-ended chart range must exist in the grid.
    if ws.col_count < 20 or ws.row_count < end:
        grid = {"columnCount": max(ws.col_count, 20), "rowCount": max(ws.row_count, end)}
        requests.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": grid},
                                                   "fields": "gridProperties.columnCount,gridProperties.rowCount"}})

    def chart(title, chart_type, series, legend, anchor_row, stacked=False):
        spec = {"title": title, "basicChart": {
            "chartType": chart_type, "legendPosition": legend, "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS", "title": "USD"}],
            "domains": [{"domain": col(0)}],
            "series": series,
        }}
        if stacked:
            spec["basicChart"]["stackedType"] = "STACKED"
        return {"addChart": {"chart": {"spec": spec, "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sid, "rowIndex": anchor_row, "columnIndex": 8},
            "widthPixels": 720, "heightPixels": 360}}}}}

    monthly = [{"series": col(i), "targetAxis": "LEFT_AXIS", "colorStyle": {"rgbColor": _rgb(SERIES_COLORS[name])}}
               for i, name in ((1, "Shares"), (2, "Options"), (3, "Dividends"))]
    cumulative = [{"series": col(5), "targetAxis": "LEFT_AXIS", "lineStyle": {"width": 2},
                   "colorStyle": {"rgbColor": _rgb(CUMULATIVE_COLOR)}}]
    requests += [
        chart("Realized P&L by month (USD)", "COLUMN", monthly, "BOTTOM_LEGEND", 1, stacked=True),
        chart("Cumulative realized P&L (USD)", "LINE", cumulative, "NO_LEGEND", 21),
    ]
    client.sh.batch_update({"requests": requests})


if __name__ == "__main__":
    import sys
    from main import log

    if "--dry-run" in sys.argv:
        for r in build(SheetsClient(config.SPREADSHEET_ID, config.CREDENTIALS_PATH)):
            print(r)
        sys.exit()
    # Runs unattended after the weekly import, so outcomes go to the run log.
    try:
        c = SheetsClient(config.SPREADSHEET_ID, config.CREDENTIALS_PATH)
        rows = build(c)
        write(c, rows, recreate_charts="--recreate-charts" in sys.argv)
        log(f"{ANALYSIS_TAB}: {len(rows)} rows written", config.LOG_PATH)
    except Exception as e:
        log(f"ERROR refreshing {ANALYSIS_TAB} tab: {e!r}", config.LOG_PATH)
        raise
