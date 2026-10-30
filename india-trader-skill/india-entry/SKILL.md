---
name: india-entry
description: >-
  NSE Entry Timer (Indian stocks). Watches a setup bar by bar on 5-minute or
  1-minute bars (Angel One SmartAPI or Yahoo .NS) and calls the entry on the
  first completed bar that CLOSES above the 15-minute opening range (9:15-9:30
  IST, or 10:00-10:15 on a stock's listing day) or above a level you give.
  Says waiting and how far, triggered with the time and price, expired, not
  open yet, or not a day trade (trade-for-trade BE stocks). Writes
  trader-in/entry.json for /india-exit. Long only, never places an order. Use
  when the user says "tell me when to get in", "watch RELIANCE", "has it
  broken out", "opening range breakout", "entry signal", "time my entry",
  "check my watchlist", or after /india-scanner, /india-ipo and /india-news.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# india-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. A
price that pokes above the high mid-bar is not an entry; a bar that CLOSES
above it is. Then it gives the time and the price, writes them down for the
Exit Planner, and stops. It never buys anything.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE              # 9:15-9:30 range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write      # also save trader-in/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check TCS --setup level --level 2510
python3 "${CLAUDE_SKILL_DIR}/entry.py" check JPL --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:45   # one line per bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in trader-in/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch JPL --now "2026-10-28 10:00"   # replay a past day bar by bar
```

## The rule

- **orb:** the high and low of the first 15 minutes, 9:15-9:30 (three
  5-minute bars). On the stock's **listing day** trading starts at 10:00, so
  the range is 10:00-10:15 and no entry comes before 10:15.
- **level:** a price you give: a 20-day high from `/india-scanner`, the
  listing-day high from `/india-ipo`.
- **The trigger** is the first completed bar after the range that CLOSES above
  the level, inside the window: `no_entries_before` (09:30) to
  `no_entries_after` (14:45) in `trader-in/config.json`. A close exactly at the
  level is not above it. The time is the bar's close time.
- **Only closed bars count:** a bar counts 20 seconds after it closes.
- **Volume, if you want it:** `--min-volume-x 1.5` needs the trigger bar at
  1.5x the range bars' average volume.
- **Not a day trade:** BE/BZ (trade-for-trade) stocks: every trade is
  delivered, so it stops and says so. NSE holidays: market closed.

## Steps

1. Get the symbol, or run `watchlist` after the scanner. Check `/india-news` first.
2. Run `check SYMBOL` and give the status in one line.
3. To wait for it, run `watch SYMBOL --until 14:45` in the background, or
   `check SYMBOL --write` on a `/loop` every 5 minutes. Keep the laptop awake.
4. On a trigger, give the time and the price and say nothing was placed. If
   it is more than two bars old, say a late entry is a different trade.
5. Hand off to `/india-exit` to set the stop and target BEFORE anything is bought.

## What it never does

It never places an order, never sizes, never sets the stop, never moves the
level after the fact, never counts a bar that has not closed, and never runs
`/india-broker` from a background watch: nobody is there to say yes.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
- Long side only. No short selling or leverage unless your config allows it.
