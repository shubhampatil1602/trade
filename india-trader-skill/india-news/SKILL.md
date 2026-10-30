---
name: india-news
description: >-
  NSE News Monitor (Indian stocks). Reads NSE corporate announcements, the
  filings companies make under SEBI LODR (results, board meetings, orders and
  contracts, acquisitions, fund raises, pledges, management changes, credit
  ratings, litigation, price-movement clarifications), for every stock you
  watch or hold. Tags each one, marks the price-moving kinds, shows only
  what is new since the last check in IST with the PDF link, and flags 3%+
  moves from yesterday's close. Use when the user says "any news on
  RELIANCE", "why is this NSE stock moving", "check announcements", "did
  the company file results", "order win", "morning briefing India",
  "corporate announcements", or after /india-scanner finds setups.
argument-hint: "[--symbols A,B] [--hours 24] [--market]"
---

# india-news

The News Monitor reads what NSE-listed companies tell the exchange, in their
own words, and shows what is new. A filing can move a stock before you read
it.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                               # watchlist + portfolio + plan, 24 hours
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols RELIANCE,TCS --hours 72
python3 "${CLAUDE_SKILL_DIR}/news.py" --all                         # include items already seen
python3 "${CLAUDE_SKILL_DIR}/news.py" --market --important          # every NSE stock, price-moving kinds only
```

## Tags

`*` marks the kinds that move prices: **results**, **order-win**, **deal**
(acquisitions, mergers, stake sales), **fund-raise** (QIP, preferential,
rights), **split-bonus** (and buybacks), **pledge**, **management**
(resignations, appointments), **rating**, **regulatory** (penalties, tax
orders, litigation, insolvency, USFDA), **clarification** (the exchange
asked about a price move). Not starred: dividend, insider, board-meeting,
listing, routine (newspaper ads, trading-window notices, certificates,
transcripts, ESOP allotments), other.

## Steps

1. Run it for the symbols in play. With nothing passed it reads
   `trader-in/watchlist.json`, `portfolio.json` and `plan.json`.
2. Tell the user the starred items first, with the time and the PDF. Say when
   a stock has results or a board meeting today or tomorrow: an intraday trade
   is flat by the close, but results day itself is a different, wilder trade.
3. Say which stocks are MOVING (3%+) and whether a filing explains it.
4. Hand off to `/india-entry SYMBOL`.

## What it never does

It never summarises a PDF it has not read, never guesses why a stock moved
when no filing says so, and never calls news a buy or sell signal.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
