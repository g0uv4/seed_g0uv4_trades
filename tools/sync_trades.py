#!/usr/bin/env python3
"""Daily IBKR trade tracker.

Pulls executions from an IBKR Flex query, appends new rows to journal.csv,
and rebuilds data/<SYMBOL>_T1.csv seed files for the chart reader.

Setup:
  export IBKR_FLEX_TOKEN=...
  export IBKR_FLEX_QUERY_ID=...
  python tools/sync_trades.py

Schedule on Windows:
  schtasks /Create /SC DAILY /ST 21:10 /TN ibkr-trades /TR "python C:\\path\\sync_trades.py"
"""

from __future__ import annotations

import csv
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "journal.csv"
DATA = ROOT / "data"
FLEX_SEND = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
FLEX_GET = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
FIELDS = [
    "trade_id", "symbol", "sec_type", "time_utc", "side",
    "qty", "price", "realized_pnl", "commission",
]


def flex_statement(token: str, query_id: str) -> str:
    send_url = FLEX_SEND + "?" + urlencode({"t": token, "q": query_id, "v": "3"})
    send_xml = urlopen(send_url, timeout=60).read()
    send = ET.fromstring(send_xml)
    if send.findtext("Status") != "Success":
        raise SystemExit(send.findtext("ErrorMessage") or "Flex send failed")
    ref = send.findtext("ReferenceCode")
    base = send.findtext("Url") or FLEX_GET
    for _ in range(8):
        time.sleep(3)
        get_url = base + "?" + urlencode({"t": token, "q": ref, "v": "3"})
        body = urlopen(get_url, timeout=60).read()
        root = ET.fromstring(body)
        if root.tag == "FlexStatementResponse" and root.findtext("Status") != "Success":
            code = root.findtext("ErrorCode")
            if code in {"1019", "1001"}:
                continue
            raise SystemExit(root.findtext("ErrorMessage") or "Flex get failed")
        return body.decode("utf-8", errors="replace")
    raise SystemExit("Flex statement not ready")


def parse_trades(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for node in root.iter("Trade"):
        side = (node.attrib.get("buySell") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        rows.append({
            "trade_id": node.attrib.get("tradeID") or node.attrib.get("ibExecID") or "",
            "symbol": node.attrib.get("symbol") or "",
            "sec_type": node.attrib.get("assetCategory") or node.attrib.get("secType") or "",
            "time_utc": node.attrib.get("dateTime") or node.attrib.get("tradeDate") or "",
            "side": side,
            "qty": node.attrib.get("quantity") or "0",
            "price": node.attrib.get("tradePrice") or "0",
            "realized_pnl": node.attrib.get("fifoPnlRealized") or "0",
            "commission": node.attrib.get("ibCommission") or "0",
        })
    return rows


def load_journal() -> list[dict]:
    if not JOURNAL.exists():
        return []
    with JOURNAL.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_journal(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r.get("time_utc", ""), r.get("trade_id", "")))
    with JOURNAL.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def rebuild_seed(rows: list[dict]) -> None:
    DATA.mkdir(exist_ok=True)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("sec_type") in {"OPT", "FOP"}:
            continue
        by_symbol[row["symbol"]].append(row)
    for symbol, trades in by_symbol.items():
        slots: dict[str, list[str]] = defaultdict(list)
        for row in trades:
            day = row["time_utc"][:8]
            if len(day) != 8 or not day.isdigit():
                continue
            side = "1" if row["side"] == "BUY" else "2"
            qty = str(abs(float(row["qty"])))
            price = row["price"]
            pnl = float(row["realized_pnl"] or 0)
            close = f"{100000 + pnl:.2f}"
            hhmm = row["time_utc"][9:13] if len(row["time_utc"]) >= 13 else "0000"
            slots[day].append(f"{day}T,{side},{qty},{price},{close},{hhmm}")
        width = max((len(v) for v in slots.values()), default=1)
        for i in range(width):
            lines = [v[i] for v in sorted(slots) if len(v) > i]
            path = DATA / f"{symbol}_T{i + 1}.csv"
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def summary(rows: list[dict]) -> None:
    closed = [r for r in rows if abs(float(r.get("realized_pnl") or 0)) >= 0.01]
    wins = [r for r in closed if float(r["realized_pnl"]) > 0]
    losses = [r for r in closed if float(r["realized_pnl"]) < 0]
    net = sum(float(r["realized_pnl"]) for r in closed)
    wr = (len(wins) / len(closed) * 100) if closed else 0
    print(f"journal {len(rows)} rows | closed {len(closed)} | win rate {wr:.1f}% | realized {net:.2f}")


def main() -> None:
    token = os.environ.get("IBKR_FLEX_TOKEN", "")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID", "")
    if not token or not query_id:
        raise SystemExit("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID")
    xml_text = flex_statement(token, query_id)
    fresh = parse_trades(xml_text)
    old = load_journal()
    known = {r["trade_id"] for r in old if r.get("trade_id")}
    added = [r for r in fresh if r["trade_id"] and r["trade_id"] not in known]
    save_journal(old + added)
    rebuild_seed(old + added)
    print(f"added {len(added)}")
    summary(old + added)


if __name__ == "__main__":
    main()
