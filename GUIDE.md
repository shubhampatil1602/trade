# Guide: India Day-Trader Skills

How to set up and use the `india-*` Claude skills for NSE on the office
Windows PC (and the Mac). The full reference is
[india-trader-skill/README.md](india-trader-skill/README.md).

## 1. What it is

No app window of its own. You use it by chatting with Claude: the Claude panel
in VS Code, the Claude desktop app, or a terminal. Answers come back as text in
the chat; your files (watchlist, plan, orders, portfolio) are saved as JSON in
a `trader-in/` folder inside the folder you open.

Nothing in this pack places an order. You place every order yourself in the
Angel One or Groww app, after the risk check passes and you type yes.

## 2. Set up the office Windows PC

1. **Python 3.8 or newer** from <https://www.python.org/downloads/>. During
   install, tick **"Add python.exe to PATH"**. On Windows the command is
   `python` (or `py -3`), not `python3`.
2. **Claude Code**: the Claude desktop app, the VS Code extension, or in
   PowerShell: `irm https://claude.ai/install.ps1 | iex` (no admin needed).
   Git for Windows is optional: <https://git-scm.com/downloads/win>.
3. **Get this repo**: `git clone https://github.com/shubhampatil1602/trade.git`
   (or download the ZIP from GitHub and unzip it).
4. **Install the skills**: copy every `india-*` folder from
   `trade\india-trader-skill\` into `C:\Users\<you>\.claude\skills\`
   (create the folder if it is missing). From Git Bash,
   `sh india-trader-skill/install.sh` does the same.
5. **Your rules**: in the folder you will work in, create `trader-in\` and copy
   `india-trader-skill\india-budget\config.example.json` into it as
   `config.json`. Edit `budget` and `max_loss_per_trade_inr`.
6. **Angel One keys** (optional, see section 4). Then open a **new** Claude
   session.

Watch out:
- Office IT may block installing software or reaching finance websites.
- `trader-in/` (portfolio, today's trades, the daily loss limit) lives on each
  machine. Trade from one machine per day, or keep that folder in a synced
  drive, or the daily limits won't see the other machine's trades.

## 3. How to use it

Open the folder you want your trading files in, then ask in plain words or
type a command like `/india-ipo`:

| When (IST) | You say | What happens |
|---|---|---|
| 8:45 AM | "pre-market scan, then news on the hits" | Scans every NSE stock, then checks company announcements |
| 9:35 AM | "opening range scan" or "watch TCS" | Watches bar by bar, tells you when it breaks out |
| On a breakout | "plan it, replay it, size it" | Stop and target, simulated odds, share count, risk check |
| | "give me the ticket", then type yes | Step-by-step for the Angel One / Groww app; you place it |
| After the fill | "I bought 31 at 1174.60, watch it" | Alerts at the target, the stop and the 3:05 PM exit |
| Anytime | "which IPOs list this week?" | IPO calendar and recent listings |

The 11 commands: `/india-scanner`, `/india-ipo`, `/india-news`,
`/india-insider`, `/india-entry`, `/india-exit`, `/india-timemachine`,
`/india-budget`, `/india-risk`, `/india-broker`, `/india-alerts`.

Without Claude, the scripts run from a terminal too, for example:
`python C:\Users\<you>\.claude\skills\india-ipo\ipo.py calendar`.

Exits: rest only the **SL-M stop-loss** sell at the broker. Never also rest a
target sell for the same shares: with intraday (MIS), if both fill you end up
short. The target is an alert; when it rings, sell and cancel the SL-M.

## 4. Angel One credentials (optional)

There's no settings screen: they are four environment variables, set once per
machine. **Never** paste them in the chat or type them as terminal commands
(they'd stay in your command history). Together they are a full login to your
Angel One account.

**Windows:** Start menu, search **"Edit environment variables for your
account"**, click **New** under *User variables*, once for each:

| Name | Value |
|---|---|
| `ANGEL_API_KEY` | your SmartAPI app's API key |
| `ANGEL_CLIENT_CODE` | your Angel One client ID |
| `ANGEL_PIN` | your 4-digit login MPIN |
| `ANGEL_TOTP_SECRET` | the TOTP key (below) |

Then restart VS Code / Claude.

**Mac:** run `nano ~/.zshrc`, paste these lines with your values, save
(Ctrl+O, Enter, Ctrl+X), then quit VS Code fully (Cmd+Q) and reopen it:

```
export ANGEL_API_KEY="..."
export ANGEL_CLIENT_CODE="..."
export ANGEL_PIN="..."
export ANGEL_TOTP_SECRET="..."
```

Where each value comes from:
- **API key:** on <https://smartapi.angelone.in>, "Create an App" with type
  **Trading APIs** (one Trading key covers live market data and historical
  candles). Copy the API key, not the secret key. The redirect URL fields
  aren't used by the pack.
- **TOTP secret:** on SmartAPI's enable-TOTP page
  (smartapi.angelbroking.com/enable-totp), enter client ID, MPIN and the OTP.
  It shows a QR code with a key underneath: copy that key exactly as shown.

Check it: ask Claude "check my Angel One setup", or run `/india-alerts quote
RELIANCE`. It should say `(angel, …)` and show circuit limits. If it falls back
to Yahoo, it prints why. (The Angel One connection has not yet been tested
with a real account.)

## 5. Without the credentials

Everything still works. These need no login at all: the daily scan, IPO
calendar and recent listings, news, insider trades and bulk deals, the NSE
clock, sizing, the risk check, tickets and alerts.

Without Angel One, intraday prices come from Yahoo, which is free but
unofficial: it can block too many requests, skips a 5-minute bar now and then
(the pack works around it), keeps only about 60 days of 5-minute prices, and
has no exact circuit limits (the pack estimates them from NSE's band file).
Angel One mainly makes the opening-range scan, the entry timer and the alerts
more reliable.

---

Education and tooling, not financial advice. Most intraday traders lose money.
