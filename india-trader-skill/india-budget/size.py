#!/usr/bin/env python3
"""Position sizing for india-budget. Standard library only, no network except an optional quote.

Sizes ONE intraday (MIS) trade from trader-in/plan.json to your budget and your
max loss per trade, then writes one order (entry + stop-loss + target) to
trader-in/orders.json for india-risk and india-broker. It never places anything.

    python3 size.py                                   # trader-in/plan.json + trader-in/config.json
    python3 size.py --write                           # write trader-in/orders.json
    python3 size.py --budget 50000 --max-loss 500     # override the config for this run
    python3 size.py --limit                           # a limit order at the plan's entry
    python3 size.py --limit 1174.50 --write

How many shares (whole shares; NSE equity has a lot size of 1):

    shares = floor of the smallest of
      max loss       (max_loss_per_trade_inr - round-trip charges) / risk a share
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget - cash buffer) x mis_leverage / entry
                     (and no more than the cash in trader-in/portfolio.json, if any)

Charges count against the max loss: brokerage, STT, exchange fee, SEBI fee,
stamp duty and GST for the round trip at the stop (india-lib's rates; your
broker's preset from "broker", overridable under "charges"). With
"include_charges": false they are shown but not counted.

mis_leverage (default 1): brokers lend for intraday (MIS), often up to 5x.
1 means you only buy what your cash pays for. Borrowed money makes the same
stop cost the same rupees, but lets one trade be a much bigger share of the
account; india-risk blocks leverage above your setting.

The entry it sizes at: a limit price, else the plan's entry, or a fresh price
in trader-in/prices.json (under 15 minutes old) if higher. Zero shares, or a
reward:risk under min_reward_risk after charges, is a plain refusal.
"""

import argparse
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

FRESH_MINUTES = 15
EPS = 1e-9
SETUP_WORDS = {"orb": "opening-range breakout", "level": "close above a level"}


class SizeError(Exception):
    pass


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def shares_for(rupees, per_share):
    if per_share <= 0:
        return 0
    return int(math.floor(rupees / per_share + EPS))


def fresh_price(doc, sym, now_utc):
    q = (doc or {}).get("quotes", {}).get(sym) or {}
    p = _f(q.get("price"))
    t = q.get("time")
    if p is None or not t:
        return p, None, False
    try:
        ist = datetime.datetime.strptime(str(t)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            ist = datetime.datetime.strptime(str(t)[:20], "%d-%b-%Y %H:%M:%S")
        except ValueError:
            return p, None, False
    age = (L.to_ist(now_utc) - ist).total_seconds()
    return p, ist, -60 <= age <= FRESH_MINUTES * 60


def size_trade(plan, cfg, limit=None, price=None, cash=None, today=None):
    """Pure: one plan + your rules -> the sizing (a dict). qty may be 0."""
    sym = str(plan.get("symbol") or "").upper()
    if not sym:
        raise SizeError("the plan has no symbol (run /india-exit)")
    if str(plan.get("side") or "buy").lower() != "buy":
        raise SizeError("this pack sizes long (buy) intraday trades only")
    if today and plan.get("date") and plan["date"] != today:
        raise SizeError("the plan is for %s and today is %s (IST): make a new plan with /india-exit"
                        % (plan["date"], today))
    if str(plan.get("series") or "EQ") in L.T2T_SERIES and not cfg.get("allow_t2t"):
        raise SizeError("%s is trade-for-trade (series %s): delivery only, no intraday" % (sym, plan["series"]))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for n, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if not v or v <= 0:
            raise SizeError("the plan needs a positive %s (run /india-exit)" % n)
    if sym in {str(b).upper() for b in cfg.get("blocked") or []}:
        raise SizeError("%s is on your blocked list" % sym)
    budget = _f(cfg.get("budget")) or 0
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader-in/config.json")
    buf_pct = _f(cfg.get("cash_buffer_pct")) or 0
    lev = max(1.0, _f(cfg.get("mis_leverage")) or 1.0)
    if limit is not None:
        ref, ref_from = round(limit, 2), "limit"
    elif price is not None and price > entry_plan + 0.001:
        ref, ref_from = round(price, 2), "price"
    else:
        ref, ref_from = round(entry_plan, 2), "plan"
    tick = float(L.tick_for(ref, sym))
    if limit is not None and not L.on_tick(ref, tick):
        raise SizeError("the limit %s is not on %s's %s tick: NSE would reject it" % (L.p2(ref), sym, tick))
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s" % (L.p2(stop), L.p2(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. Skip it or "
                        "make a new plan" % (L.p2(target), L.p2(ref)))
    risk = round(ref - stop, 2)
    reward = round(target - ref, 2)
    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s buffer" % (L.inr(budget), L.inr(cash_buffer))
    if cash is not None and cash - cash_buffer < available:
        available = cash - cash_buffer
        cash_note = "cash %s (portfolio) minus the %s buffer" % (L.inr(cash), L.inr(cash_buffer))
    available = max(available, 0.0)
    buying_power = available * lev
    max_loss = _f(cfg.get("max_loss_per_trade_inr"))
    include = cfg.get("include_charges", True)

    def loss_at(q):
        ch = L.charges(q * ref, q * stop, cfg)["total"] if q else 0.0
        return round(q * risk + (ch if include else 0), 2), ch

    limits = []
    if max_loss:
        q = shares_for(max_loss, risk)
        while q > 0 and loss_at(q)[0] > max_loss + 0.005:     # charges eat into the max loss
            q -= 1
        limits.append({"name": "max loss", "shares": q,
                       "how": "%s max loss / %s risk a share%s" % (L.inr(max_loss), L.p2(risk),
                                                                    " (after charges)" if include else "")})
    cap_pct = _f(cfg.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "shares": shares_for(cap, ref),
                       "how": "%g%% of %s = %s / %s" % (cap_pct, L.inr(budget), L.inr(cap), L.p2(ref))})
    max_order = _f(cfg.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (L.inr(max_order), L.p2(ref))})
    limits.append({"name": "cash", "shares": shares_for(buying_power, ref),
                   "how": "%s = %s%s / %s" % (cash_note, L.inr(available),
                                              (" x %gx MIS leverage" % lev) if lev > 1 else "", L.p2(ref))})
    qty = max(0, min(l["shares"] for l in limits))
    bound = next(l["name"] for l in limits if l["shares"] == qty)
    for l in limits:
        l["binds"] = l["name"] == bound
    loss, ch_stop = loss_at(qty)
    ch_target = L.charges(qty * ref, qty * target, cfg)["total"] if qty else 0.0
    gain = round(qty * reward - (ch_target if include else 0), 2)
    cost = round(qty * ref, 2)
    rr_net = round(gain / loss, 2) if loss > 0 else None
    refusal = None
    min_rr = _f(cfg.get("min_reward_risk"))
    if qty == 0:
        if bound == "max loss":
            refusal = ("one share risks %s plus charges, more than your %s max loss per trade. No order. Skip it, "
                       "or plan a closer stop with /india-exit." % (L.inr(risk), L.inr(max_loss)))
        else:
            refusal = "one share of %s costs %s, more than your %s limit. No order." % (sym, L.inr(ref), bound)
    elif min_rr and rr_net is not None and rr_net < min_rr - EPS:
        refusal = ("after charges the reward:risk is %.2f (gain %s at the target vs loss %s at the stop), under your "
                   "minimum %g: india-risk would block it. No order." % (rr_net, L.inr(gain), L.inr(loss), min_rr))
    elif cost < (_f(cfg.get("min_order_inr")) or 0):
        refusal = "%d share(s) of %s is %s, under your %s minimum order. No order." % (
            qty, sym, L.inr(cost), L.inr(cfg["min_order_inr"]))
    return {"symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
            "entry_time": plan.get("entry_time"), "entry_plan": round(entry_plan, 2), "entry": ref,
            "entry_from": ref_from, "stop": round(stop, 2), "target": round(target, 2), "tick": tick,
            "risk_per_share": risk, "budget": budget, "cash_buffer": cash_buffer, "cash_available": round(available, 2),
            "mis_leverage": lev, "max_loss_per_trade_inr": max_loss, "limits": limits, "qty": qty, "bound_by": bound,
            "cost": cost, "margin_needed": round(cost / lev, 2), "max_loss": loss, "charges_at_stop": ch_stop,
            "charges_at_target": ch_target, "gain_at_target": gain, "reward_risk_net": rr_net,
            "include_charges": include, "flat_by": plan.get("flat_by") or cfg.get("flat_by"),
            "product": cfg.get("product", "MIS"), "order_type": "limit" if limit is not None else "market",
            "listing_day": plan.get("listing_day", False), "refusal": refusal}


def make_order(s):
    """The one proposed order: broker-neutral fields (strings) plus why and plan."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"symbol": s["symbol"], "exchange": "NSE", "side": "buy", "qty": str(s["qty"]), "type": s["order_type"],
         "product": s["product"], "validity": "DAY", "stop_loss": "%.2f" % s["stop"], "target": "%.2f" % s["target"]}
    if s["order_type"] == "limit":
        o["limit_price"] = "%.2f" % s["entry"]
    o["why"] = "%s on %s: %d shares, bound by your %s (max loss %s at the stop incl. charges %s)" % (
        SETUP_WORDS.get(s["setup"], s["setup"] or "day trade"), s["symbol"], s["qty"], s["bound_by"],
        L.inr(s["max_loss"]), L.inr(s["charges_at_stop"]))
    o["plan"] = {"entry": s["entry"], "stop": s["stop"], "target": s["target"], "risk_per_share": s["risk_per_share"],
                 "max_loss": s["max_loss"], "charges_at_stop": s["charges_at_stop"], "setup": s["setup"],
                 "entry_time": s["entry_time"], "flat_by": s["flat_by"], "tick": s["tick"],
                 "mis_leverage": s["mis_leverage"], "listing_day": s["listing_day"]}
    return o


def report(s, order, notes):
    out = ["%s buy (%s, %s), entry %s%s" % (s["symbol"], SETUP_WORDS.get(s["setup"], s["setup"] or "setup"),
                                            s["product"], L.inr(s["entry_plan"]),
                                            (" at " + s["entry_time"]) if s["entry_time"] else "")]
    out.append("Stop %s. Target %s. Tick %s." % (L.inr(s["stop"]), L.inr(s["target"]), s["tick"]))
    out += notes
    src = {"plan": "the plan's entry", "price": "the latest price, above the plan's entry",
           "limit": "your limit price"}[s["entry_from"]]
    out.append("Sized at %s (%s). Risk %s a share." % (L.inr(s["entry"]), src, L.inr(s["risk_per_share"])))
    out.append("")
    out.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        out.append("  %-13s %-62s %5d shares%s" % (l["name"], l["how"], l["shares"], "  <- binds" if l["binds"] else ""))
    out.append("")
    if order is None:
        out.append("No order: " + s["refusal"])
        out.append("Education and tooling, not financial advice.")
        return "\n".join(out)
    out.append("Size: %d shares of %s = %s at %s%s." % (s["qty"], s["symbol"], L.inr(s["cost"]), L.p2(s["entry"]),
                                                        (" (margin about %s at %gx)" % (L.inr(s["margin_needed"]),
                                                                                        s["mis_leverage"]))
                                                        if s["mis_leverage"] > 1 else ""))
    out.append("At the stop:   lose %d x %s = %s + charges %s = %s%s." % (
        s["qty"], L.p2(s["risk_per_share"]), L.inr(s["qty"] * s["risk_per_share"]), L.inr(s["charges_at_stop"]),
        L.inr(s["qty"] * s["risk_per_share"] + s["charges_at_stop"]),
        "" if s["include_charges"] else " (charges not counted: include_charges false)"))
    out.append("At the target: gain %s after charges %s. Reward:risk after charges %.2f." % (
        L.inr(s["gain_at_target"]), L.inr(s["charges_at_target"]), s["reward_risk_net"] or 0))
    out.append("A stop is an order, not a promise: a gap or a circuit can fill it worse.")
    out.append("")
    how = ("limit %s" % order["limit_price"]) if order["type"] == "limit" else "market"
    out.append("Proposed order (nothing has been placed): BUY %s %s on NSE, %s, %s, stop-loss %s, target %s."
               % (order["qty"], order["symbol"], how, order["product"], order["stop_loss"], order["target"]))
    if s["flat_by"]:
        out.append("Flat by %s IST: anything still open then gets closed (india-alerts rings, india-broker flatten)."
                   % s["flat_by"])
    out.append("Next: /india-risk checks it, then /india-broker shows the ticket and asks you yes or no.")
    out.append("Education and tooling, not financial advice.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Position sizing for one NSE intraday trade (india-budget)")
    ap.add_argument("--plan", default=L.path("plan.json"))
    ap.add_argument("--config", default=L.path("config.json"))
    ap.add_argument("--prices", default=L.path("prices.json"))
    ap.add_argument("--portfolio", default=L.path("portfolio.json"))
    ap.add_argument("--budget", type=float)
    ap.add_argument("--max-loss", type=float)
    ap.add_argument("--leverage", type=float)
    ap.add_argument("--limit", nargs="?", const="plan")
    ap.add_argument("--write", nargs="?", const=L.path("orders.json"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    try:
        now = L.parse_now(a.now)
        today = L.to_ist(now).date().isoformat()
        cfg = L.load_config(a.config)
        if a.budget is not None:
            cfg["budget"] = a.budget
        if a.max_loss is not None:
            cfg["max_loss_per_trade_inr"] = a.max_loss
        if a.leverage is not None:
            cfg["mis_leverage"] = a.leverage
        plan = L.read_json(a.plan, "plan (run /india-exit first)")
        limit = None
        if a.limit is not None:
            limit = _f(plan.get("entry")) if a.limit == "plan" else _f(a.limit)
            if not limit or limit <= 0:
                raise SizeError("--limit wants a price, like --limit 1174.50")
        sym = str(plan.get("symbol") or "").upper()
        notes, price = [], None
        pdoc = L.read_json(a.prices, default=None)
        if pdoc and limit is None:
            p, t, fresh = fresh_price(pdoc, sym, now)
            if p is not None and fresh:
                price = p
                if p > (_f(plan.get("entry")) or 0) + 0.001:
                    notes.append("%s last %s (%s IST) is above the plan's entry %s: sized at %s, a bigger risk."
                                 % (sym, L.p2(p), t.strftime("%H:%M"), L.p2(plan["entry"]), L.p2(p)))
            elif p is not None:
                notes.append("%s in %s is older than %d minutes: not used." % (sym, a.prices, FRESH_MINUTES))
        pf = L.read_json(a.portfolio, default=None)
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
        s = size_trade(plan, cfg, limit, price, cash, today)
        order = make_order(s)
        if order and a.write:
            L.write_json(a.write, {"created_by": "india-budget", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "status": "proposed", "broker": cfg.get("broker"), "currency": "INR",
                                   "note": "Proposals only. Nothing is placed until india-risk passes each order "
                                           "and you say yes to it in the chat.",
                                   "source": "%s (%s %s)" % (a.plan, s["symbol"], s["date"]), "orders": [order]})
        if a.json:
            print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2,
                             ensure_ascii=False))
        else:
            print(report(s, order, notes))
            if order and a.write:
                print("Wrote 1 proposed order to %s." % a.write)
        return 0 if order else 1
    except (SizeError, L.DataError, ValueError) as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
