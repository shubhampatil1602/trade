---
name: india-risk
description: >-
  NSE Risk Manager (Indian stocks). Checks every proposed order against your
  limits before it can reach the broker ticket: stop required, loss at the
  stop plus charges within your max loss per trade, reward:risk after
  charges, daily loss limit, trades per day, entry window in IST, NSE
  holidays and session hours, listing-day open (no entry before 10:15),
  prices on the NSE tick, trade-for-trade (BE/BZ) stocks blocked for MIS,
  MIS leverage within your setting, position size and cash, duplicates and a
  daily order limit; warns on circuit limits and narrow bands. Exits are
  never blocked by a day limit. PASS or BLOCK with reasons. Use when the user
  says "check my order", "is this safe", "risk check", "can I take this
  trade", "how much have I risked today", or before /india-broker, always.
argument-hint: "[--now \"YYYY-MM-DD HH:MM\"]"
---

# india-risk

The gate. Every order passes here or it never becomes a ticket. A PASS is a
rules check, not a recommendation.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                          # trader-in/orders.json -> state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json --no-write
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-29 09:45"
```

## What it checks

Every order: format (whole shares, market / limit / sl / sl-m, MIS / CNC,
NSE), **tick** (an off-tick price is rejected by NSE), asset (on NSE's list,
not SME), **t2t** (BE/BZ stocks cannot be MIS), short sales.

Entries, on top: blocked list, min / max order, position cap, **cash x
mis_leverage** (more is borrowed money you did not allow), duplicates, daily
order limit, stop required, **loss = qty x (entry - stop) + round-trip
charges** within `max_loss_per_trade_inr` (entry = the HIGHER of the plan's and
a fresh price), reward:risk after charges, daily loss, trades per day, and the
**window**: NSE closed (weekend, holiday, outside 9:15-15:30), before
`no_entries_before`, after `no_entries_after`, and on a listing day before 10:15.

Warnings: target above today's upper circuit, stop below the lower (exact
from an Angel One quote in `trader-in/prices.json`, else the band on
yesterday's close), a 2% / 5% band, CNC on a day trade.

Exits only reduce what you hold: only format, tick and short sales can block
one. Getting out is always possible.

## Steps

1. Run `check.py` after `/india-budget` (or after `ticket.py flatten`).
2. Tell the user PASS or BLOCK per order with the reasons, in plain words.
3. A PASS goes to `/india-broker`. A BLOCK stops there: never work around it.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
