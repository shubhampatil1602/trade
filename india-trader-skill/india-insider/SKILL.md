---
name: india-insider
description: >-
  Insider trades and big-money deals on NSE (Indian stocks). Reads SEBI
  insider-trading (PIT) disclosures for the stocks you watch: promoters,
  directors, KMPs and their relatives buying, selling or pledging, with
  quantity, value, mode and holding before and after, and flags promoter or
  director market buys and sells and pledges. Also reads NSE's daily bulk
  and block deals (who bought or sold 0.5%+ of a company, or Rs 10 crore+
  in one trade) with the net by stock. Use when the user says "promoter
  buying", "insider trading disclosures", "did the promoter sell", "pledge",
  "bulk deals", "block deals", "who is buying this stock", "big investors",
  "FII buying a stock", or "smart money".
argument-hint: "[deals] [--symbols A,B] [--days 30]"
---

# india-insider

What the people inside a company, and the biggest traders, did, as filed with
the exchange. Every one of these is public and filed after the trade. It is
not inside information and not a signal to copy.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py"                          # your stocks, insider trades, 30 days
python3 "${CLAUDE_SKILL_DIR}/insider.py" --symbols RELIANCE,JPL --days 90
python3 "${CLAUDE_SKILL_DIR}/insider.py" --promoters              # promoters, directors, KMPs only
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals                    # today's bulk + block deals, every stock
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --watch            # only stocks you watch or hold
```

## What it reads

| source | what |
| --- | --- |
| NSE `corporates-pit` (SEBI PIT disclosures) | who, category, buy / sell / pledge, qty, value, mode (market, off market, ESOP, gift, pledge), dates, holding % before and after. One request per stock |
| NSE `bulk.csv`, `block.csv` | the day's bulk deals (0.5%+ of the company in a day) and block deals (single Rs 10 crore+ trades in the block window) |

Flags: a promoter, director or KMP buying or selling **in the market**, a
**pledge** (borrowed money against the shares), a pledge **released**, a
pledge **invoked** (the lender sold).

## Steps

1. For insiders, run it on the symbols in play. Lead with flagged rows, then
   say how much is routine (relatives, off-market transfers, ESOPs).
2. For deals, show the biggest and the net by stock, and say that many bulk
   deals are high-frequency firms buying and selling the same stock the same day.
3. Never present a promoter buy as a reason to buy.

## What it never does

It never calls a filing inside information, never tells the user to copy a
trade, and never places an order.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
