# India Day-Trader Skills (NSE)

Eleven Claude skills for intraday trading on NSE, adapted from
[Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT) for the Indian market: IST clock, NSE holidays and sessions, NSE's own
data, rupee charges, tick sizes, circuit limits, trade-for-trade stocks, and
new listings. Standard-library Python 3.8+, nothing to pip install.

**Nothing in this pack places an order.** You place every order yourself in
the Angel One, Groww or Zerodha app, after `/india-risk` passes it and you
type yes in the chat.

## Install

```bash
./install.sh            # copies india-* into ~/.claude/skills/ (global)
./install.sh --project  # or into ./.claude/skills/ of the current folder
```

Then open a new Claude Code session. Files are written to `trader-in/` in the
folder you work in. Start with your rules:

```bash
mkdir -p trader-in && cp ~/.claude/skills/india-budget/config.example.json trader-in/config.json
```

### Windows

1. Install Python 3.8+ from python.org and tick **Add python.exe to PATH**. On
   Windows the command is `python` (or `py -3`), not `python3`; the skills say so.
2. Install Claude Code (PowerShell: `irm https://claude.ai/install.ps1 | iex`),
   the Claude desktop app, or the VS Code extension. Git for Windows is optional.
3. Copy this folder to the PC (USB, Google Drive, OneDrive, a zip by email) and
   copy every `india-*` folder into `C:\Users\<you>\.claude\skills\`. From Git
   Bash, `sh install.sh` does the same.
4. Angel One variables: Start menu, "Edit environment variables for your
   account", **New** under User variables, once per variable. Then restart VS
   Code / Claude. No admin rights needed.

Two machines: `trader-in/` (portfolio, today's trades, the daily loss and
trade counts) lives in the folder you work in, on that machine. Trade from one
machine per day, or keep that folder in a synced drive, or the daily limits on
one machine will not see the other's trades.

## Data

| what | source | login |
| --- | --- | --- |
| Equity list, listing dates, series | NSE `EQUITY_L.csv` | none |
| Daily OHLC for every stock (daily scan, IPO setups, circuits) | NSE bhavcopy, cached | none |
| Price bands | NSE `sec_list.csv` | none |
| IPO calendar and issue prices | NSE IPO APIs | none |
| Announcements, insider trades, bulk/block deals | NSE APIs and files | none |
| 5-minute / 1-minute bars, quotes, exact circuit limits | **Angel One SmartAPI** | your SmartAPI app |
| Fallback bars and quotes | Yahoo `SYMBOL.NS` (unofficial, can lag or rate-limit) | none |

### Angel One SmartAPI (recommended)

1. Create an app at <https://smartapi.angelone.in> (free) and enable TOTP on
   your Angel One account; keep the TOTP secret (the text under the QR code).
2. Put four variables in your shell profile (`~/.zshrc` on a Mac; on Windows
   see above), never in a file in this folder and never in the chat:

   ```bash
   export ANGEL_API_KEY="..."
   export ANGEL_CLIENT_CODE="..."
   export ANGEL_PIN="...."
   export ANGEL_TOTP_SECRET="..."
   ```

The pack only reads candles and quotes; it never calls an order endpoint. The
day's session token is cached in `trader-in/state/angel_session.json`
(owner-only permissions on Mac and Linux). Keep `trader-in/state/` out of git.

Without the variables everything still works: NSE's files need no login, and
intraday bars and quotes come from Yahoo instead.

## The eleven

| command | job |
| --- | --- |
| `/india-scanner` | Daily setups on every NSE stock (bhavcopy); opening-range breakouts on 5-minute bars |
| `/india-ipo` | IPO calendar, recent listings with setups, the listing-day clock (trading from 10:00) |
| `/india-news` | NSE corporate announcements for your stocks, tagged, new items only |
| `/india-insider` | SEBI insider-trading disclosures; bulk and block deals |
| `/india-entry` | Bar-by-bar entry timer (9:15-9:30 range, 10:00-10:15 on listing day) |
| `/india-exit` | Stop and target on the NSE tick, circuit warnings, flat-by before square-off |
| `/india-timemachine` | 10,000 replays + history on the stock's own NSE sessions (THIN for young stocks) |
| `/india-budget` | Size to your max loss **including charges** (Angel One / Groww / Zerodha presets) |
| `/india-risk` | PASS / BLOCK: ticks, T2T, leverage, window, daily loss, charges |
| `/india-broker` | Step-by-step app ticket, your yes recorded word for word, fills and portfolio |
| `/india-alerts` | NSE clock and holidays, stop / target / flat-by alerts, quotes |

One trade, in order:

```
india-scanner (or india-ipo) -> india-news -> india-entry -> india-exit -> india-timemachine
  -> india-budget -> india-risk -> india-broker (you place it) -> india-alerts
```

## Indian-market rules built in

- **Session:** pre-open 9:00-9:15, normal 9:15-15:30 IST. Listing day: special
  pre-open 9:00-9:45, trading from 10:00.
- **Trade-for-trade (BE/BZ):** delivery only; the pack never day-trades them.
- **Ticks:** Re 0.01 under Rs 250, then 0.05 / 0.10 / 0.50 / 1 / 5 by price (or
  the stock's own tick from Angel One's scrip master).
- **Charges** (intraday): brokerage preset per broker, STT 0.025% sell, NSE
  0.00297%, SEBI Rs 10/crore, stamp 0.003% buy, GST 18%. Override in
  `"charges"`.
- **Exits:** rest only an SL-M stop at the broker; never a second resting
  target sell (with MIS both can fill and leave you short). The target is an
  alert.
- **Square-off:** flat_by 15:05 by default, before the brokers' own MIS
  square-off (about 15:10 to 15:20; check yours).

## Tests

```bash
python3 -m unittest discover -s tests -v     # offline, 24 tests
```

## Fine print

Not financial advice. Most intraday traders lose money. The data is free and
some of it unofficial; check anything that matters in your broker's app. A
stop is a plan, not a guarantee: gaps and circuits can fill it worse or not at
all. The Time Machine is a simulation, not a prediction. Charges and
square-off times change; your contract note is the truth.
