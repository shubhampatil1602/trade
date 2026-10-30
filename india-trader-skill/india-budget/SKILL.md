---
name: india-budget
description: >-
  NSE Budget Manager (Indian stocks, INR). Sizes an intraday (MIS) trade to
  your budget and your max loss per trade, counting the real round-trip
  charges (brokerage for Angel One, Groww or Zerodha, STT, exchange fee,
  SEBI fee, stamp duty, GST) inside the max loss, with optional MIS leverage,
  a position cap, a max order and a cash buffer; says which limit decided
  the size and writes one order (entry, stop-loss, target) to
  trader-in/orders.json. Use when the user says "how many shares", "size this
  trade", "I have Rs 50,000", "I only want to risk Rs 500", "what are the
  charges", "set my rules", "intraday leverage", or after /india-exit.
argument-hint: "[--budget N] [--max-loss N] [--limit [PRICE]] [--write]"
---

# india-budget

How many shares, so that a stop-out costs what you decided and no more,
charges included. It writes a proposal; nothing is placed.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Files go to `trader-in/`.

On Windows, run these with `python` (or `py -3`) instead of `python3`.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                                 # trader-in/plan.json + config
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                         # write trader-in/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 50000 --max-loss 500   # override for this run
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write                 # a limit order at the plan's entry
```

## Your rules: `trader-in/config.json`

Copy `${CLAUDE_SKILL_DIR}/config.example.json` to `trader-in/config.json` and
edit it with the user. The keys that matter most:

| key | default | meaning |
| --- | --- | --- |
| `budget` | none | rupees for trading |
| `max_loss_per_trade_inr` | 500 | the most one stop-out may cost, charges included |
| `max_daily_loss_inr` | 1500 | all of today's entries' max losses together |
| `max_trades_per_day` | 3 | entries a day |
| `cash_buffer_pct` | 10 | never invested |
| `max_position_pct` / `max_order_inr` | 75 / 40000 | size caps |
| `mis_leverage` | 1 | 1 = only what your cash pays for. Brokers offer up to about 5x for MIS: borrowed money, same rupee loss at the stop, bigger share of the account |
| `broker` | `angelone` | `angelone`, `groww` or `zerodha`: the brokerage preset and the app steps |
| `charges` | {} | override any rate, e.g. `{"brokerage_flat": 20, "brokerage_pct": 0.05}` |
| `no_entries_before` / `no_entries_after` / `flat_by` | 09:30 / 14:45 / 15:05 | IST |

## How many shares

The smallest of: max loss / risk a share (reduced until loss + charges fits),
the position cap, the max order, and cash after the buffer x leverage. A stop
too far for your max loss is a refusal, not a smaller order, and so is a
reward:risk under your minimum **after charges**.

## Steps

1. If there is no `trader-in/config.json`, set one up with the user first.
2. Run `size.py --write`. Read the user the size, the loss at the stop with
   the charges, the gain at the target after charges, and which limit bound it.
3. Hand off to `/india-risk`.

## Safety

- Education and tooling, not financial advice.
- Nothing in this pack places an order. You place every order yourself in your
  broker's app, after india-risk passes it and you say yes in the chat.
- No leverage beyond `mis_leverage`, no short selling unless the config allows it.
