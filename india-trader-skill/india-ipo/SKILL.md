---
name: india-ipo
description: >-
  New listings on NSE (Indian IPOs). The IPO calendar (open issues with
  subscription so far, upcoming, closed and awaiting listing, listing dates),
  every stock listed in the last 60 sessions with its issue price, listing
  gain, distance from the issue price and setups that need only a few days of
  history (closed above the listing-day high, closing high since listing,
  back above the issue price, tight base), and the listing-day clock (special
  pre-open 9:00-9:45, trading from 10:00, the 10:00-10:15 opening range). Use
  when the user says "IPO", "new listing", "listing today", "listing gain",
  "recently listed stocks", "IPO calendar", "which IPO lists today", "trade
  the listing", "is it above issue price", or names a stock that listed in
  the last few weeks.
argument-hint: "[calendar|recent|today] [SYMBOL]"
---

# india-ipo

New listings have little history, so the 20-day scans and a 60-session
replay cannot see them. This skill covers them: what is coming, what just
listed, and how listing day works. A listing gain is never predictable, and
new stocks move faster and further than old ones.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/ipo.py" calendar                 # open, upcoming, awaiting listing, listing dates
python3 "${CLAUDE_SKILL_DIR}/ipo.py" calendar --sme           # include SME issues
python3 "${CLAUDE_SKILL_DIR}/ipo.py" recent                   # listed in the last 60 sessions, with setups
python3 "${CLAUDE_SKILL_DIR}/ipo.py" recent --days 20 --write # make the hits the watchlist for /india-entry
python3 "${CLAUDE_SKILL_DIR}/ipo.py" today                    # stocks listing today and the clock
python3 "${CLAUDE_SKILL_DIR}/ipo.py" today JPL --now "2026-10-28 10:40"
```

## Listing day on NSE

| time (IST) | what happens |
| --- | --- |
| 9:00-9:45 | special pre-open order entry: a call auction, no price band, orders can be changed |
| 9:45-10:00 | order matching and buffer: the auction (equilibrium) price is the day's open |
| 10:00 | normal trading starts, with the stock's band from then on |
| 10:00-10:15 | the 15-minute opening range `/india-entry` and `/india-scanner orb` use for it |

Small issues can list with a 5% band and in trade-for-trade (BE): those
cannot be day traded at all, and the skill says so.

## Recent-listing setups

From NSE bhavcopy, split-adjusted, row 0 = listing day:

- **listing_high_breakout:** the last close is above the listing day's high (2nd session on).
- **ipo_closing_high:** the last close is above every close since listing (3rd session on).
- **issue_reclaim:** closed below the issue price before, now back above it.
- **tight_base:** 5+ sessions, the last 5 closes inside 8%, above the issue price.
- **below_issue:** under the issue price. A warning, not a setup.

The issue price comes from NSE's past-issues list, only when the company name
and listing date match (NSE reuses symbols). Stocks with no IPO record
(relistings, SME migrations, demergers) are marked.

## Steps

1. For "what IPOs are coming / listing": `calendar`.
2. On a listing day: `today`. Before 10:00 say what phase it is; after 10:00
   give the open against the issue price and the 10:00-10:15 range.
3. For recent listings: `recent`, then name the ones with setups and how far
   they are from the issue price. `--write` puts them on the watchlist.
4. Hand off to `/india-news SYMBOL`, then `/india-entry SYMBOL` (it moves the
   range to 10:00 on a listing day by itself). `/india-timemachine` needs 5
   full sessions and marks 5-19 as THIN.

## What it never does

It never predicts a listing gain, never applies for an IPO, never treats a
grey-market premium as data, and never places an order.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
- New listings can hit circuits: size small, and expect stops to fill worse.
