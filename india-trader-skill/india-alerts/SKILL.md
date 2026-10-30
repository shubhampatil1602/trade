---
name: india-alerts
description: >-
  NSE clock and price alerts (Indian stocks). Says whether NSE is open,
  in pre-open (9:00-9:15) or closed, the next session and holidays (2026 list
  built in, refreshable from NSE). Turns the trade plan into three alerts at
  once (stop, target, flat-by time), plus above / below levels, percent moves
  and the opening bell, each ringing once, with what to do (at the target:
  sell and cancel the SL-M; at flat-by: flatten before the broker's
  square-off). Fetches quotes (Angel One with circuit limits, else Yahoo) into
  trader-in/prices.json. Use when the user says "alert me when", "watch my
  stop", "watch my trade", "is the market open", "NSE holiday", "remind me to
  exit by 3", "get me a price", or right after a buy fills.
argument-hint: "[clock|quote|add|check|list|reset]"
---

# india-alerts

Rings on the levels and the times that matter while a trade is open. Quotes
are for alerts only: never time an order on them.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                       # NSE open? next open, holidays
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock --refresh             # re-read NSE's holiday list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE TCS --write  # -> trader-in/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan             # stop, target, flat-by from the plan
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --above 2510
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol JPL --pct 5
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                       # rings what hit, once
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

## The NSE clock

Pre-open 9:00-9:08 (order entry) and 9:08-9:15 (matching); normal market
9:15-15:30 IST; no daylight saving. Holidays: NSE's 2026 list is built in;
`clock --refresh` reads NSE's holiday master for later years. Muhurat trading
(the Diwali evening session) is not modelled.

## Steps

1. Right after a buy fills: `add --from-plan`.
2. Watch it: run `check` on a `/loop` every minute or two while the trade is
   open (`check` quotes only during market hours unless `--after-hours`).
3. When something rings, tell the user at once, with what it says to do:
   - **target:** sell at market and CANCEL the resting SL-M (never leave both).
   - **stop:** check the SL-M filled; if not (a gap or a circuit), sell now.
   - **flat by:** flatten before the broker's own square-off:
     `/india-broker flatten`, then `/india-risk` and the user's yes.
4. Never place anything from a background loop: nobody is there to say yes.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
