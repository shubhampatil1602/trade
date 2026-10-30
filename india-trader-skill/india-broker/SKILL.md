---
name: india-broker
description: >-
  Order tickets for Angel One, Groww and Zerodha (Indian stocks, NSE). Shows
  each order india-risk passed as a plain ticket with step-by-step
  instructions for the broker's app (search, BUY, Intraday/MIS, market or
  limit, quantity, then an SL-M stop-loss), records your explicit yes word
  for word (refuses "ok", "yes to all", or a yes naming another stock or
  quantity), then records what happened in the app, the fill and the
  portfolio. Builds the sell ticket that flattens a day trade by your flat-by
  time. Never sends anything to a broker: you place it yourself. Use when the
  user says "place the order", "buy it", "execute", "give me the ticket",
  "how do I enter this in Angel One / Groww", "I bought it", "close my
  position", "flatten", or after /india-risk passes.
argument-hint: "[show|approve|result|fill|flatten|log]"
---

# india-broker

The last step, and the only one that leads to money moving: and you are the
one who moves it. This skill never connects to a broker. It turns an order
that india-risk passed into a ticket you place in your own app, and it keeps
the record.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                         # every order, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --order-id 2610290001234 \
    --filled-qty 31 --filled-price 1174.60
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 31 --price 1196.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

## The flow

1. `show`. An order is READY only if india-risk passed THIS exact order today
   (fingerprint) and nobody has decided on it. Show the user the whole ticket.
2. Wait for the user's own reply. Pass it word for word: `approve --index N
   --reply "<their words>"`. Only a reply starting with "yes" counts; not "ok",
   "sure", "go", "do it", "yes to all", or a yes with a different stock or
   number. If it is refused, ask again with the ticket. Never approve for them.
3. Read the user the app steps it prints. They place it themselves.
4. When they say what happened, record it: `result --status placed` (with the
   order id and fill if they have it), `not_placed`, or `rejected`.
5. After a buy fills: `/india-alerts add --from-plan`.

## Exits, the safe way

Rest ONE exit order at the broker: an **SL-M sell at the stop**, MIS, the whole
quantity. Do **not** also rest a target sell for the same shares: with MIS,
if both fill you end up **short**. india-alerts rings at the target; then sell
at market and cancel the SL-M. If the user's app has an order type that holds
a target and a stop together and cancels one when the other fills, they may
use that instead. Before a flatten sell, cancel the resting SL-M.

## Brokers

`"broker"` in `trader-in/config.json`: `angelone` (default), `groww`,
`zerodha`. It picks the app name in the steps and the brokerage preset for
charges. Angel One and Groww have no official MCP server, so there is no
automated route; Zerodha's official Kite MCP exists, but this pack keeps every
order manual.

## What it never does

It never places, modifies or cancels an order, never asks for or stores a
password, PIN, TOTP or API key, never approves without the user's own words,
never batches yeses, and never runs from a background job.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- One order, one yes. The user places every order themselves.
- No short selling or leverage beyond the config.
