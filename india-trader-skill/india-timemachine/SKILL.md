---
name: india-timemachine
description: >-
  NSE Time Machine (Indian stocks). Before you risk a rupee, replays the rest
  of the session 10,000 times from the stock's own 5-minute bars at the same
  time of day over its last ~60 full NSE sessions (9:15-15:30), plus the same
  opening-range rule on every one of those real sessions, and gives the odds
  of target first, stop first, or neither by your flat-by time, the price
  range at the close, average R and one honest verdict. Works on young stocks
  from 5 sessions (marked THIN). Use when the user says "run the time
  machine", "replay this trade", "what are the odds", "simulate", "monte
  carlo", "backtest this setup", "is this a coin flip", or after /india-exit
  writes a plan.
argument-hint: "[run|chart] [--until 15:05]"
---

# india-timemachine

A reality check, not a prediction. It is built from the stock's own recent
bars, and results, news, a circuit or a block deal can do what those
sessions never did.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:05            # trader-in/plan.json to your flat-by
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:05 --write    # save trader-in/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from trader-in/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-29 --entry 1174.30 \
    --stop 1163.30 --target 1196.30 --entry-time "9:40 AM"
```

`--seed` (19), `--replays` (10,000), `--window` (2 bars) and `--json`.

## How it works

1. About 60 sessions of 5-minute bars (Angel One keeps more history; Yahoo
   about 59 days). Full sessions are 75 bars. Yahoo drops a bar here and
   there: a session with at most 4 missing bars still counts, each filled flat
   at the previous close, and the run says how many.
2. Each replay walks from the entry bar to `--until`, drawing every bar whole
   from the same time of day (+-2 bars) on a random past session, scaled to
   the current price. A bar touching both stop and target counts as the stop.
3. History: the plan's own rule (same range length, stop rule, target R) on
   each real past session.
4. Verdict: the target's share of the replays that hit a side against the
   break-even share 1 / (1 + R): under 5 points "coin flip / break-even", 5 to
   15 "leans", 15+ "strongly".

**New listings:** 20+ full sessions is normal; 5-19 runs marked **THIN** (a
small pool: rough odds); under 5, or on the listing day itself, it refuses.

## Steps

1. Run with `--until` set to the plan's flat_by (15:05): you will be out by then.
2. Give the user the three odds, the verdict line and the history count in
   plain words. Say THIN loudly if it is.
3. Hand off to `/india-budget`.

## Safety

- Education and tooling, not financial advice. A simulation is not a promise.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
