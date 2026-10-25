"""Look up a voucher (or list recent ones) in the SQLite database.

Read-only. Safe to run while the service is live (SQLite is in WAL mode, which
allows concurrent readers). Uses Python's built-in sqlite3, so nothing needs to
be installed on the Orange Pi.

    # on the Pi:
    cd /opt/bottle-vendo
    ./.venv/bin/python scripts/voucher_lookup.py            # list recent sessions
    ./.venv/bin/python scripts/voucher_lookup.py ABCD1234   # one voucher's full story
    ./.venv/bin/python scripts/voucher_lookup.py --open     # only OPEN sessions
    ./.venv/bin/python scripts/voucher_lookup.py --db /path/to/bottle_vendo.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = APP_ROOT / "database" / "bottle_vendo.db"


def _connect(db_path: str) -> sqlite3.Connection:
    # read-only URI so we can never disturb the live service's data
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_secs(s: int) -> str:
    s = int(s or 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def list_recent(conn, only_open: bool, limit: int) -> None:
    where = "WHERE State='OPEN'" if only_open else ""
    rows = conn.execute(
        f"SELECT Voucher, State, BottleCount, MaxBottles, TimeAddedSeconds, "
        f"ExtendTime, UpdatedAt FROM PortalSession {where} "
        f"ORDER BY UpdatedAt DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        print("(no sessions)")
        return
    print(f"{'VOUCHER':<10} {'STATE':<11} {'BOTTLES':<8} {'TIME':<10} {'EXT':<4} UPDATED")
    print("-" * 70)
    for r in rows:
        print(f"{r['Voucher']:<10} {r['State']:<11} "
              f"{str(r['BottleCount'])+'/'+str(r['MaxBottles']):<8} "
              f"{_fmt_secs(r['TimeAddedSeconds']):<10} "
              f"{('yes' if r['ExtendTime'] else 'no'):<4} {r['UpdatedAt']}")


def show_voucher(conn, voucher: str) -> None:
    s = conn.execute("SELECT * FROM PortalSession WHERE Voucher=?", (voucher,)).fetchone()
    if not s:
        print(f"No session found for voucher '{voucher}'.")
        print("Tip: run without a voucher to list recent ones.")
        return
    print(f"=== SESSION  {voucher} ===")
    print(f"  state             : {s['State']}")
    print(f"  bottles           : {s['BottleCount']} / {s['MaxBottles']}"
          + ("   (MAX REACHED)" if s['BottleCount'] >= s['MaxBottles'] else ""))
    print(f"  time on session   : {_fmt_secs(s['TimeAddedSeconds'])} "
          f"({s['MinutesPerBottle']} min/bottle)")
    print(f"  extend-time       : {'yes' if s['ExtendTime'] else 'no'}")
    print(f"  device            : mac={s['Mac']} ip={s['Ip']}")
    print(f"  started / updated : {s['StartedAt']}  /  {s['UpdatedAt']}")

    g = conn.execute("SELECT * FROM RewardGrant WHERE SessionId=?", (s['Id'],)).fetchone()
    print("\n=== WI-FI GRANT ===")
    if not g:
        print("  (no grant yet — user has not tapped DONE, or 0 bottles)")
    else:
        print(f"  status            : {g['Status']}"
              + ("   (retrying on MikroTik)" if g['Status'] == 'PENDING' else ""))
        print(f"  minutes granted   : {g['MinutesGranted']} min")
        print(f"  target limit      : {_fmt_secs(g['TargetLimitSeconds'])} "
              f"(absolute uptime set on MikroTik)")
        print(f"  attempts          : {g['Attempts']}"
              + (f"   last error: {g['LastError']}" if g['LastError'] else ""))

    dets = conn.execute(
        "SELECT TransactionId, Timestamp, DetectionResult, Prediction, Confidence, "
        "CreditedToSession, ErrorMessage FROM DetectionTransaction "
        "WHERE SessionVoucher=? ORDER BY Id", (voucher,)).fetchall()
    print(f"\n=== DETECTIONS for this voucher ({len(dets)}) ===")
    for d in dets:
        flag = "credited" if d['CreditedToSession'] else "-"
        err = f"  [{d['ErrorMessage']}]" if d['ErrorMessage'] else ""
        print(f"  {d['Timestamp']}  {d['DetectionResult']:<6} "
              f"{d['Prediction']:<15} {d['Confidence']:.2f}  {flag}{err}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect a voucher / session in the DB")
    ap.add_argument("voucher", nargs="?", help="voucher code; omit to list recent")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="path to bottle_vendo.db")
    ap.add_argument("--open", action="store_true", help="list only OPEN sessions")
    ap.add_argument("--limit", type=int, default=15, help="rows when listing")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"DB not found: {args.db}")
        return 1
    conn = _connect(args.db)
    try:
        if args.voucher:
            show_voucher(conn, args.voucher.strip().upper())
        else:
            list_recent(conn, args.open, args.limit)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
