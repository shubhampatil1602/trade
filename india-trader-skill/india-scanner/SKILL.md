---
name: india-scanner
description: >-
  NSE Chart Scanner (Indian stock market). Scans every NSE stock for setups:
  a pre-market daily scan from NSE's own bhavcopy (20-day closing-high
  breakouts, bull flags, reclaimed 20-day average, oversold, oversold bounces)
  and an intraday opening-range breakout scan on 5-minute bars (9:15-9:30
  range, or 10:00-10:15 on a stock's listing day). Skips trade-for-trade
  (BE/BZ) stocks, adjusts for splits and bonuses, writes
  trader-in/watchlist.json. Use when the user says "scan NSE", "scan the
  Indian market", "find setups in Nifty stocks", "what is breaking out on
  NSE", "opening range breakouts India", "build my watchlist", "pre-market
  scan", or at the start of an Indian trading day before /india-news.
argument-hint: "[daily|orb|universe] [--symbols A,B]"
---

# india-scanner

The Chart Scanner looks at every NSE stock and lists the charts that match a
rule. It is a list of charts, not a list of stocks to buy. Most people who day
trade lose money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder. Every india-* skill uses
the shared library in `${CLAUDE_SKILL_DIR}/../india-lib/`, and keeps its files
in `trader-in/` in the current project.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                    # NSE's equity list -> trader-in/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                       # every EQ stock, from NSE bhavcopy
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --min-turnover-cr 25  # only Rs 25 crore+ turnover
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                         # after 9:35 IST: breakouts so far
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:30 --top 100
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --symbols RELIANCE,TCS,JPL
```

Flags: `--min-price` (50), `--show N`, `--json`, `--no-write`, `--now
"YYYY-MM-DD HH:MM"` (IST) to replay a past session.

## Data

| source | what |
| --- | --- |
| NSE `EQUITY_L.csv` | the equity list, series and listing dates |
| NSE bhavcopy (`sec_bhavdata_full_DDMMYYYY.csv`) | every stock's daily OHLC, volume, delivery %. The last 45 sessions are cached in `trader-in/cache/bhav/`; only new days download. NSE posts it in the evening |
| Angel One SmartAPI, else Yahoo `.NS` | today's 5-minute bars for `orb` (one request per stock) |

## The rules

- **daily:** liquidity floor: last close ₹50+ and ₹10 crore+ turnover that day.
  A stock needs 30 sessions. Younger stocks (recent IPOs) are counted and
  left to `/india-ipo recent`, which has setups made for them.
- **Splits and bonuses:** on an ex-date NSE's PREV_CLOSE is adjusted; when it
  differs from the stored close, older bars are scaled, so a 1:2 split is not
  a 50% crash. Adjusted hits carry `"adjusted"`.
- **orb:** the range is the first 15 minutes, 9:15-9:30. A hit is the first
  completed 5-minute bar after it that CLOSES above the range high. On a
  stock's listing day it opens at 10:00 (special pre-open 9:00-9:45), so its
  range is 10:00-10:15. Symbols: `--symbols`, else the `--top` 150 most traded
  EQ stocks of the last session, plus anything listing today.
- **Trade-for-trade (BE/BZ)** stocks are never scanned: intraday is not
  allowed in them.

## Steps

1. Before 9:15 IST, run `daily`. After 9:35, run `orb`.
2. Tell the user how many charts matched and the top few, most traded first.
3. Hand off: `/india-news SYMBOL` for the filings, then `/india-entry SYMBOL`.

## What it never does

It never recommends a stock, never sizes or places an order, and never calls
a chart a sure thing.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
- No short selling or leverage unless your config allows it.
- Angel One credentials live in environment variables only, never in the chat or a file.
