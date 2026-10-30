---
name: india-exit
description: >-
  NSE Exit Planner (Indian stocks). Sets the stop and the target before you
  enter: stop at the opening-range midpoint by default (or the range low, a
  price, a percent, or an ATR multiple), rounded DOWN onto the stock's NSE
  tick (Re 0.01 under Rs 250, 0.05, 0.10 ...), target at 2R, flat-by time
  before your broker's intraday square-off, and warnings when the target or
  stop lies beyond today's circuit limits. Refuses trade-for-trade stocks
  and stops too tight to survive the spread. Writes trader-in/plan.json. Use
  when the user says "where's my stop", "set stop loss and target", "plan
  the exit", "what's my R", "risk reward", or right after /india-entry
  triggers.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|price:X]"
---

# india-exit

Set the exit before the entry. If price hits the stop, you are out; never
move it further away. A stop is a plan, not a guarantee: a gap or a circuit
can fill it worse, or not at all.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # from trader-in/entry.json: or-mid, 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --write                  # save trader-in/plan.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-low --target 1.5R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol TCS --entry 2480 --setup level --stop pct:1
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # 14-bar ATR up to the entry bar
python3 "${CLAUDE_SKILL_DIR}/exit.py" show
```

## The rules

- **Ticks:** every price is on the stock's NSE tick (Angel One's scrip master
  when cached, else NSE's table: 0.01 under ₹250, 0.05 to ₹1,000, 0.10 to
  ₹5,000, 0.50 to ₹10,000, 1.00 to ₹20,000, 5.00 above). The stop rounds
  down, the target to the nearest tick. NSE rejects an off-tick price.
- **Refused:** a stop at or above the entry, a target at or below it, a risk
  under 0.05% of the price or under 2 ticks, an entry that has not triggered
  (unless `--entry` is given), a BE/BZ stock, a short.
- **Circuits:** with yesterday's close (bhavcopy) and the stock's band (NSE
  sec_list) it warns when the target is above the upper circuit (it cannot
  fill) or the stop below the lower one. F&O stocks have no fixed band. A 2%
  or 5% band gets its own warning.
- **flat_by** (15:05 default): out by then, win or lose. Brokers square off
  MIS themselves (Angel One about 15:15, Groww about 15:10, Zerodha about
  15:20: check yours) and may charge for it; it warns when flat_by is not
  earlier.

## Steps

1. Right after `/india-entry` triggers, run `plan --write`.
2. Read the user the entry, stop, target, risk per share, reward:risk, flat-by
   and any circuit warning.
3. Hand off to `/india-timemachine` (replay it) and `/india-budget` (size it).

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
- Long side only.
