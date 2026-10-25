# Voucher Lookup — checking a voucher in the database

`voucher_lookup.py` inspects the vendo's SQLite database: it lists recent portal
sessions, or prints one voucher's full story — session state, bottle count, time
credited, the Wi‑Fi grant (status + minutes), and every detection for it.

It is **read‑only** (opens the database in read‑only mode) and uses only Python's
built‑in `sqlite3`, so it is safe to run **while the machine is live** and needs
nothing installed on the Orange Pi.

Database it reads: `/opt/bottle-vendo/database/bottle_vendo.db` (WAL mode).

---

## Run it on the Orange Pi over SSH

From your PC (PowerShell, or any terminal). The Pi is at **`10.10.10.252`**, user
**`orangepi`**.

### Option A — log in, then run

```bash
ssh orangepi@10.10.10.252
```
Enter the password when prompted, then on the Pi:
```bash
cd /opt/bottle-vendo
./.venv/bin/python scripts/voucher_lookup.py            # list recent sessions
./.venv/bin/python scripts/voucher_lookup.py XB38T3MG   # one voucher's full story
./.venv/bin/python scripts/voucher_lookup.py --open     # only sessions still OPEN
```
Type `exit` to leave the Pi.

### Option B — one line from your PC (no separate login)

```bash
ssh orangepi@10.10.10.252 "cd /opt/bottle-vendo && ./.venv/bin/python scripts/voucher_lookup.py"
```
Add a voucher on the end to look one up:
```bash
ssh orangepi@10.10.10.252 "cd /opt/bottle-vendo && ./.venv/bin/python scripts/voucher_lookup.py XB38T3MG"
```

> Use **`./.venv/bin/python`** (the app's virtual‑env Python). Plain
> `python3 scripts/voucher_lookup.py` also works since the script is stdlib‑only.

---

## Command reference

```
voucher_lookup.py [VOUCHER] [--open] [--limit N] [--db PATH]
```

| Argument | Meaning |
|---|---|
| `VOUCHER` | Show this voucher's session, grant and detections. Omit to list recent sessions. Case‑insensitive. |
| `--open` | When listing, show only sessions still `OPEN`. |
| `--limit N` | How many rows when listing (default 15). |
| `--db PATH` | Point at a different database file (default `./database/bottle_vendo.db`). |

---

## Example output

Listing:
```
VOUCHER    STATE       BOTTLES  TIME       EXT  UPDATED
----------------------------------------------------------------------
XB38T3MG   FINALIZED   5/5      1h 15m 0s  yes  2026-09-20T11:21:09
PY6BEMLN   CANCELLED   0/5      0s         no   2026-09-20T10:42:35
```

One voucher:
```
=== SESSION  XB38T3MG ===
  state             : FINALIZED
  bottles           : 5 / 5   (MAX REACHED)
  time on session   : 1h 15m 0s (15 min/bottle)
  extend-time       : yes
  device            : mac=4E:59:FD:72:35:15 ip=10.10.10.194
=== WI-FI GRANT ===
  status            : SUCCESS
  minutes granted   : 75 min
  target limit      : 1h 15m 0s (absolute uptime set on MikroTik)
=== DETECTIONS for this voucher (5) ===
  2026-09-20T10:24:33  ACCEPT plastic_bottle  0.92  credited
  ...
```

`state` values: `OPEN` (in progress), `FINALIZING`/`FINALIZED` (Wi‑Fi granted),
`CANCELLED`, `EXPIRED`. Grant `status`: `SUCCESS`, `PENDING` (MikroTik was down —
auto‑retries), `FAILED`.

---

## Doing it by hand (raw SQL)

If you'd rather query directly — for example the helper isn't present — the
`sqlite3` CLI is **not** installed on the Pi, so use the app's Python:

```bash
cd /opt/bottle-vendo
./.venv/bin/python
```
```python
import sqlite3
c = sqlite3.connect("database/bottle_vendo.db"); c.row_factory = sqlite3.Row
# one voucher
print(dict(c.execute("SELECT * FROM PortalSession WHERE Voucher=?", ("XB38T3MG",)).fetchone()))
# its Wi-Fi grant
print(dict(c.execute("SELECT Status, MinutesGranted, TargetLimitSeconds "
                     "FROM RewardGrant WHERE Voucher=?", ("XB38T3MG",)).fetchone()))
```

Or install the CLI once (needs internet): `sudo apt install -y sqlite3`, then
`sqlite3 /opt/bottle-vendo/database/bottle_vendo.db`.

Full schema and more queries: [../../docs/FINAL-DOCUMENTATION.md](../../docs/FINAL-DOCUMENTATION.md) §7.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ssh` can't connect | `ping 10.10.10.252`; confirm the Pi is powered and on the network. |
| `DB not found` | You're not in `/opt/bottle-vendo`, or pass `--db /opt/bottle-vendo/database/bottle_vendo.db`. |
| `No session found for voucher` | Check spelling, or run with no voucher to list what exists. |
