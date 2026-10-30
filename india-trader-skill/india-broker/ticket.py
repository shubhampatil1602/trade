#!/usr/bin/env python3
"""Order tickets and the decision log for india-broker. Standard library only.

This tool never talks to a broker and never places anything. You place the
order yourself in the Angel One, Groww or Zerodha app. It does its work around
that moment:

  show      each proposed order as a plain ticket: what to tap in your broker's
            app, step by step, with the stop-loss and the max loss at the stop.
            An order is READY only if india-risk passed THIS exact order today
            (matched by fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, word for word. It refuses
            "yes to all", "ok", "sure", a yes naming another stock or amount,
            and any order that is not READY. Then it prints the app steps.
  decline   records a no.
  result    records what happened in the app: placed (with the order id if you
            have it), not placed, or rejected (with the broker's reason).
  fill      records a fill in trader-in/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol by flat_by; it still goes
            through india-risk and your yes.
  log       today's decisions.

    python3 ticket.py show
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --order-id 2611290001234
    python3 ticket.py fill --symbol RELIANCE --side buy --qty 31 --price 1174.50
    python3 ticket.py flatten --symbol RELIANCE
    python3 ticket.py log

Exits, the safe way. The ticket has you rest ONE exit order at the broker: an
SL-M (stop-loss market) sell at the stop, MIS, for the whole quantity. The
target is NOT a second resting sell: with MIS, if a target sell and a stop sell
both fill you end up SHORT. india-alerts rings at the target instead, and you
sell then (and cancel the stop). If your app offers a bracket/OCO-style order
that cancels one leg when the other fills, you may use that instead.

Everything is written to trader-in/state/broker.json (append only).
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-risk"))
import indialib as L  # noqa: E402
from check import fingerprint  # noqa: E402

YES_WORDS = ("yes", "y", "haan", "ha")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later", "if", "unless",
               "nahi", "ruko"}


class TicketError(Exception):
    pass


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ist_iso(now_utc):
    return L.to_ist(now_utc).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def max_loss(order):
    plan = order.get("plan") or {}
    if _f(plan.get("max_loss")) is not None and order.get("side") == "buy":
        return round(_f(plan["max_loss"]), 2)
    return None


def plain(order):
    side = str(order.get("side", "")).upper()
    how = ("limit %s" % order["limit_price"]) if order.get("type") == "limit" else str(order.get("type", "market"))
    parts = ["%s %s %s on NSE, %s, %s" % (side, order.get("qty"), order.get("symbol"), how, order.get("product", "MIS"))]
    if order.get("stop_loss"):
        parts.append("stop-loss %s" % order["stop_loss"])
    if order.get("target"):
        parts.append("target %s" % order["target"])
    ml = max_loss(order)
    if ml is not None:
        parts.append("max loss about %s at the stop incl. charges" % L.inr(ml))
    return " / ".join(parts)


def amount_numbers(order):
    nums = [_f(order.get(k)) for k in ("qty", "limit_price", "stop_loss", "target")]
    nums.append(max_loss(order))
    return [n for n in nums if n is not None]


def parse_yes(reply, order, batch_symbols=()):
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\-]{1,15}", raw.upper()))
    named = tokens & ({s.upper() for s in batch_symbols} - {sym})
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums and not any(abs(n - w) < 0.005 for n in nums for w in amount_numbers(order)):
        return False, "the reply says %s, but this order is %s" % (", ".join("%g" % n for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


def app_steps(order, broker):
    """What to tap, in order. Generic NSE wording that matches Angel One, Groww and Zerodha's apps."""
    name = L.BROKERS.get(broker, {"name": broker.title()})["name"]
    sym, qty = order["symbol"], order["qty"]
    side = "BUY" if order["side"] == "buy" else "SELL"
    prod = "Intraday (MIS)" if order.get("product", "MIS") == "MIS" else "Delivery (CNC)"
    if order.get("type") == "limit":
        price = "Limit at %s" % order["limit_price"]
    else:
        price = "Market"
    steps = ["Open the %s app and search %s. Pick the NSE quote (not BSE)." % (name, sym),
             "Tap %s. Choose %s. Order type %s. Quantity %s." % (side, prod, price, qty),
             "Check the order screen shows NSE, %s, qty %s, then place it. Note the order id." % (prod, qty)]
    if order["side"] == "buy" and order.get("stop_loss"):
        steps.append("Once the buy is FILLED: place the stop. SELL %s %s, %s, order type SL-M (stop-loss market), "
                     "trigger price %s. This order protects you; leave it resting." % (qty, sym, prod,
                                                                                      order["stop_loss"]))
        if order.get("target"):
            steps.append("Do NOT also rest a target sell for the same shares: with MIS, if both fill you are short. "
                         "india-alerts rings at the target %s; then sell %s at market and CANCEL the SL-M order."
                         % (order["target"], qty))
            steps.append("(If your app offers an order that holds a target and a stop together and cancels one when "
                         "the other fills, you may use that instead, with target %s and stop %s.)"
                         % (order["target"], order["stop_loss"]))
    if order["side"] == "sell":
        steps.append("First CANCEL any open stop-loss order on %s, or the broker may reject this sell or the stop "
                     "may fill later and leave you short." % sym)
    return steps


def load_state(p):
    return L.read_json(p, default=None) or {"orders": []}


def latest(state, fp, today):
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec.get("date") == today:
            found = rec
    return found


def status_of(order, risk, state, today):
    fp = fingerprint(order)
    if not risk:
        return False, "NOT READY: india-risk has not checked these orders (run check.py)", None
    if risk.get("date") != today:
        return False, "NOT READY: the risk check is from %s, not today; run check.py again" % risk.get("date"), None
    rv = next((r for r in risk.get("results", []) if r.get("fingerprint") == fp), None)
    if rv is None:
        return False, "NOT READY: this exact order was not in the last risk check; run check.py again", None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by india-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        if rec.get("status") == "approved":
            return False, "APPROVED by you at %s: place it in the app, then record the result" % rec["at"], rv
        return False, "DONE: %s at %s" % (rec.get("status"), rec.get("at")), rv
    return True, "READY: india-risk PASS, waiting for your yes", rv


def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    broker = str(cfg.get("broker", "angelone")).lower()
    name = L.BROKERS.get(broker, {"name": broker.title()})["name"]
    lines = ["ORDER %d of %d, %s (you place it yourself in the app; nothing is sent from here)" % (idx, total, name),
             "  " + plain(order)]
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if ready:
        lines.append("  After your yes, the steps for the %s app:" % name)
        for n, s in enumerate(app_steps(order, broker), 1):
            lines.append("    %d. %s" % (n, s))
        lines.append("  Reply \"yes\" to approve this one order, or \"no\" to skip it.")
    return "\n".join(lines), ready


def _load(a):
    doc = L.read_json(a.orders, "orders (run /india-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = L.load_config(a.config)
    now = L.parse_now(a.now)
    return orders, cfg, L.read_json(a.risk, default=None), load_state(a.state), L.to_ist(now).date().isoformat(), now


def _pick(orders, index):
    if not index or not 1 <= index <= len(orders):
        raise TicketError("say which order: --index 1 to %d" % len(orders))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = []
        for i, o in picks:
            ready, label, rv = status_of(o, risk, state, today)
            out.append({"index": i, "ready": ready, "status": label, "plain": plain(o), "fingerprint": fingerprint(o),
                        "steps": app_steps(o, str(cfg.get("broker", "angelone")).lower()), "order": o})
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask again with the ticket for order %d." % (why, a.index))
        return 1
    broker = str(cfg.get("broker", "angelone")).lower()
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
        "symbol": order["symbol"], "side": order["side"], "qty": order["qty"], "kind": (rv or {}).get("kind", "entry"),
        "max_loss": max_loss(order), "stop": _f(order.get("stop_loss")), "plain": plain(order), "status": "approved",
        "reply": a.reply, "broker": broker})
    L.write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    print("Now place it yourself in the %s app:" % L.BROKERS.get(broker, {"name": broker})["name"])
    for n, s in enumerate(app_steps(order, broker), 1):
        print("  %d. %s" % (n, s))
    print("Then tell me what happened: ticket.py result --index %d --status placed|not_placed|rejected" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    state.setdefault("orders", []).append({"at": ist_iso(now), "date": today, "index": a.index,
                                           "fingerprint": fingerprint(order), "symbol": order["symbol"],
                                           "side": order["side"], "plain": plain(order), "status": "declined",
                                           "reply": a.reply or "no"})
    L.write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing to place." % (a.index, plain(order)))
    return 0


def apply_fill(path, cfg, symbol, side, qty, price, date):
    pf = L.read_json(path, default=None)
    if pf is None:
        if not cfg.get("budget"):
            raise TicketError("no %s yet and no budget in the config to start it from" % path)
        pf = {"cash": float(cfg["budget"]), "positions": []}
    symbol, qty, price = symbol.upper(), float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    pos = next((p for p in pf.setdefault("positions", []) if str(p.get("symbol", "")).upper() == symbol), None)
    value = round(qty * price, 2)
    if side == "buy":
        if pos is None:
            pos = {"symbol": symbol, "shares": 0.0, "avg_price": 0.0, "product": cfg.get("product", "MIS")}
            pf["positions"].append(pos)
        total = pos["shares"] * pos["avg_price"] + value
        pos["shares"] = round(pos["shares"] + qty, 6)
        pos["avg_price"] = round(total / pos["shares"], 4)
        pos["date"] = date
        pf["cash"] = round(float(pf.get("cash", 0)) - value, 2)
    else:
        if pos is None or pos["shares"] + 1e-9 < qty:
            raise TicketError("cannot record selling %g %s: the portfolio holds %s" % (qty, symbol,
                                                                                    pos["shares"] if pos else 0))
        pos["shares"] = round(pos["shares"] - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + value, 2)
    L.write_json(path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    state = load_state(state_path)
    state.setdefault("orders", []).append({"at": ist_iso(now), "date": today, "status": "filled",
                                           "symbol": symbol.upper(), "side": side, "qty": "%g" % float(qty),
                                           "price": float(price), "plain": "%s %g %s at %s" % (
                                               side.upper(), float(qty), symbol.upper(), L.p2(price))})
    L.write_json(state_path, state)


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": "placed" if a.status == "placed" else a.status}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.order_id:
        new["broker_order_id"] = a.order_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    L.write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, new["status"]))
    if a.status == "placed":
        if a.filled_qty and a.filled_price:
            apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
            log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
            print("Added the fill to %s." % a.portfolio)
        else:
            print("When it fills: ticket.py fill --symbol %s --side %s --qty Q --price P" % (rec["symbol"], rec["side"]))
        if rec["side"] == "buy":
            print("Then set the alerts: india-alerts add --from-plan (stop, target and flat-by %s)."
                  % (cfg.get("flat_by") or "15:05"))
    return 0


def cmd_fill(a):
    cfg = L.load_config(a.config)
    now = L.parse_now(a.now)
    date = a.date or L.to_ist(now).date().isoformat()
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(), L.inr(a.price), date,
                                                          L.inr(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = L.load_config(a.config)
    sym = a.symbol.strip().upper()
    now = L.parse_now(a.now)
    if a.qty is not None:
        qty, source = _f(a.qty), "you"
    else:
        pf = L.read_json(a.portfolio, default=None) or {}
        qty = sum(_f(p.get("shares")) or 0 for p in pf.get("positions", []) if str(p.get("symbol", "")).upper() == sym)
        source = a.portfolio
    if not qty or qty <= 0:
        raise TicketError("nothing to flatten: no %s shares recorded. Check your broker's positions and pass --qty"
                          % sym)
    plan = L.read_json(a.plan, default=None) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) or cfg.get("flat_by")
    order = {"symbol": sym, "exchange": "NSE", "side": "sell", "qty": "%d" % qty, "type": "market",
             "product": cfg.get("product", "MIS"), "validity": "DAY",
             "why": "flat by %s IST: closes the %s day trade before the broker's square-off (qty from %s)"
                    % (flat_by, sym, source)}
    L.write_json(a.orders, {"created_by": "india-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "status": "proposed", "currency": "INR",
                            "note": "Flatten ticket. Nothing is placed until india-risk passes it and you say yes.",
                            "orders": [order]})
    print("Flatten %s: %s." % (sym, plain(order)))
    print("Wrote it to %s. Next: india-risk check.py (an exit is never blocked by day limits), then ticket.py show."
          % a.orders)
    print("In the app: CANCEL the resting SL-M stop on %s first, then sell." % sym)
    return 0


def cmd_log(a):
    state = load_state(a.state)
    today = L.to_ist(L.parse_now(a.now)).date().isoformat()
    rows = [r for r in state.get("orders", []) if a.all or r.get("date") == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") else ""
        print("%s  %-11s %s%s%s" % (r.get("at"), r.get("status"), r.get("plain"), reply, extra))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (india-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--orders", default=L.path("orders.json"))
        p.add_argument("--config", default=L.path("config.json"))
        p.add_argument("--risk", default=L.path("state", "risk.json"))
        p.add_argument("--state", default=L.path("state", "broker.json"))
        p.add_argument("--now")

    p = sub.add_parser("show")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "not_placed", "rejected"])
    p.add_argument("--order-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default=L.path("portfolio.json"))
    p = sub.add_parser("fill")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date")
    p.add_argument("--now")
    p.add_argument("--portfolio", default=L.path("portfolio.json"))
    p.add_argument("--config", default=L.path("config.json"))
    p.add_argument("--state", default=L.path("state", "broker.json"))
    p = sub.add_parser("flatten")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty")
    p.add_argument("--orders", default=L.path("orders.json"))
    p.add_argument("--portfolio", default=L.path("portfolio.json"))
    p.add_argument("--plan", default=L.path("plan.json"))
    p.add_argument("--config", default=L.path("config.json"))
    p.add_argument("--now")
    p = sub.add_parser("log")
    p.add_argument("--state", default=L.path("state", "broker.json"))
    p.add_argument("--now")
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except (TicketError, L.DataError, ValueError) as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
