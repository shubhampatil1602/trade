#!/usr/bin/env python3
"""Pre-trade risk check for india-risk. Standard library only; reads NSE's cached lists.

Reads the proposed orders (trader-in/orders.json) and checks every one against
your rules (trader-in/config.json), your account (trader-in/portfolio.json),
prices (trader-in/prices.json) and what was placed today
(trader-in/state/broker.json). Each order gets PASS or BLOCK with the reasons.
Orders are checked in file order as if each PASS had filled.

    python3 check.py                                # writes trader-in/state/risk.json
    python3 check.py --now "2026-10-29 09:45"       # pin the clock (IST)
    python3 check.py --json --no-write

An ENTRY opens or adds to a position. An EXIT only reduces what you hold.
Getting out is never blocked by a day limit or the entry window: an exit is
checked for format, ticks and short sales only; the rest become warnings.

What blocks an order (every order):
  format       symbol, side, whole-share qty, type (market / limit / sl / sl-m),
               product (MIS / CNC), a limit order without a limit price,
               exchange other than NSE
  tick         a limit, stop-loss or target price that is not on the stock's
               NSE tick (the exchange rejects it)
  asset        not on NSE's equity list; SME (SM / ST) series
  t2t          a BE / BZ (trade-for-trade) stock as MIS: intraday is not
               allowed in trade-for-trade, every trade is delivered
  short        selling more than you hold, unless "allow_short": true

What blocks an entry (on top):
  blocked      a symbol on your "blocked" list
  size         under min_order_inr, over max_order_inr
  position     over max_position_pct of the account
  cash         needs more than your cash after the buffer x mis_leverage
               (leverage above "mis_leverage" is borrowed money you did not allow)
  duplicate    the same symbol and side twice in this batch, or already placed today
  daily limit  max_orders_per_day orders placed today
  stop         no stop while "require_stop": true; a stop on the wrong side
  loss         qty x (entry - stop) + round-trip charges over max_loss_per_trade_inr
               (entry = the limit price, else the HIGHER of the plan's entry and
               a fresh price, so a stale plan cannot understate the risk)
  reward:risk  after charges, under min_reward_risk
  daily loss   today's entries' max losses plus this one over max_daily_loss_inr
  trades       max_trades_per_day entries already placed today
  window       before no_entries_before / after no_entries_after (IST), market
               closed (weekend, NSE holiday, outside 9:15-15:30); on a stock's
               listing day no entry before 10:15 (trading opens at 10:00)

Warnings (never block): a target above today's upper circuit or a stop below
the lower circuit (exact from an Angel One quote in prices.json, else the NSE
band on yesterday's close), a 2% or 5% band, CNC for a day trade, a live account.

Exit code 0 when every order passes, 1 when any is blocked. Verdicts go to
trader-in/state/risk.json with a fingerprint of each order, so india-broker
only shows an order as READY if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "type", "product", "validity", "limit_price",
                "trigger_price", "stop_loss", "target")
TYPES = {"market", "limit", "sl", "sl-m"}
PRODUCTS = {"MIS", "CNC"}
FRESH_MINUTES = 15
PAISE = 0.005


class RiskError(Exception):
    pass


def fingerprint(order):
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def window_check(now_ist, c, listing_day):
    before = L.parse_hhmm(c.get("no_entries_before") or "09:15", "no_entries_before")
    after = L.parse_hhmm(c.get("no_entries_after") or "15:30", "no_entries_after")
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    warns = [x for x in [L.calendar_note(d)] if x]
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    ok, why = L.trading_day(d)
    if not ok:
        return ["window: NSE is closed today (%s, %s IST)" % (why, stamp)], warns
    if t < L.OPEN or now_ist.time() >= L.CLOSE:
        return ["window: the market is closed (%s IST; the session is 09:15 to 15:30)" % stamp], warns
    if listing_day:
        first = datetime.time(10, 15)
        if t < first:
            return ["window: listing day: trading opens at 10:00 and the opening range ends 10:15; no entry before "
                    "10:15 (it is %s)" % L.hm(t)], warns
    if t < before:
        return ["window: no new entries before %s IST (it is %s)" % (L.hm(before), L.hm(t))], warns
    if t > after:
        return ["window: no new entries after %s IST (it is %s)" % (L.hm(after), L.hm(t))], warns
    return [], warns


def account(cfg, portfolio, prices):
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader-in/portfolio.json and no budget in the config: nothing to check against")
        return {"cash": budget, "shares": {}, "values": {}}
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("symbol") or p.get("ticker") or "").upper()
        n = _f(p.get("shares", p.get("qty"))) or 0
        shares[t] = shares.get(t, 0) + n
        values[t] = shares[t] * (prices.get(t) or _f(p.get("avg_price")) or 0)
    return {"cash": cash, "shares": shares, "values": values}


def check(orders, cfg, portfolio=None, prices=None, quotes=None, state=None, now=None, price_times=None):
    """Pure-ish (reads NSE's cached lists). Returns {"results", "pass", "block", "warnings", ...}."""
    c = dict(L.DEFAULTS)
    c.update(cfg or {})
    prices = {k.upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    quotes = quotes or {}
    now_utc = now or L.utc_now()
    now_ist = L.to_ist(now_utc)
    today = now_ist.date().isoformat()
    acct = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    eq = L.equity_list()
    done = [r for r in (state or {}).get("orders", []) if r.get("status") == "placed" and r.get("date") == today]
    entries_today = [r for r in done if r.get("kind", "entry" if r.get("side") == "buy" else "exit") == "entry"]
    loss_today = sum(_f(r.get("max_loss")) or 0 for r in entries_today)
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done}
    lev_cap = max(1.0, _f(c.get("mis_leverage")) or 1.0)
    top_warn = []
    if str(c.get("broker", "")).lower() not in L.BROKERS:
        top_warn.append("unknown broker %r in the config: charges use Angel One's preset" % c.get("broker"))
    seen, results = set(), []
    batch_entries, batch_loss = 0, 0.0

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        return prices[sym] if t and -60 <= (now_ist - t).total_seconds() <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info, later = [], [], [], []
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        product = str(o.get("product", "MIS")).strip().upper()
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        q = _f(o.get("qty"))
        stock = eq.get(sym) or {}
        series = stock.get("series") or ""
        listing_day = stock.get("listed") == now_ist.date()
        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if q is None or q <= 0 or abs(q - round(q)) > 1e-9:
            reasons.append("format: qty must be a whole number of shares above 0, got %r" % o.get("qty"))
        if typ not in TYPES:
            reasons.append("format: type must be market, limit, sl or sl-m, got %r" % typ)
        if typ in ("limit", "sl") and _f(o.get("limit_price")) is None:
            reasons.append("format: a %s order needs a limit_price" % typ)
        if typ in ("sl", "sl-m") and _f(o.get("trigger_price")) is None:
            reasons.append("format: a %s order needs a trigger_price" % typ)
        if product not in PRODUCTS:
            reasons.append("format: product must be MIS (intraday) or CNC (delivery), got %r" % product)
        if str(o.get("exchange", "NSE")).upper() != "NSE":
            reasons.append("format: this pack trades NSE only, got %r" % o.get("exchange"))
        # asset
        if sym and not stock:
            reasons.append("asset: %s is not on NSE's equity list" % sym)
        elif series in ("SM", "ST"):
            reasons.append("asset: %s is an SME stock (series %s): lot-based, not handled here" % (sym, series))
        elif series in L.T2T_SERIES and product == "MIS" and not c.get("allow_t2t"):
            reasons.append("t2t: %s is trade-for-trade (series %s): intraday (MIS) is not allowed, every trade is "
                           "delivered" % (sym, series))
        # ticks
        ref_for_tick = _f(o.get("limit_price")) or _f(plan.get("entry")) or prices.get(sym) or 0
        if ref_for_tick:
            tick = L.tick_for(ref_for_tick, sym)
            for k in ("limit_price", "trigger_price", "stop_loss", "target"):
                v = _f(o.get(k))
                if v is not None and not L.on_tick(v, tick):
                    reasons.append("tick: %s %s is not on %s's %s tick; NSE rejects it" % (k, o.get(k), sym, tick))
        # reference price
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        if typ in ("limit", "sl"):
            ref, ref_from = _f(o.get("limit_price")), "limit"
        else:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            ref, ref_from = (max(cands, key=lambda x: x[0]) if cands else (prices.get(sym), "stale price"))
            if live and plan_entry and live > plan_entry + PAISE:
                info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                            % (sym, L.p2(live), L.p2(plan_entry), L.p2(live)))
        value = q * ref if q and ref else None
        held = acct["shares"].get(sym, 0.0)
        exiting = side == "sell" and held > 0 and q is not None and q <= held + 1e-9
        kind = "exit" if exiting else "entry"
        if side == "sell" and kind == "entry" and not c.get("allow_short"):
            reasons.append("short: selling more %s than you hold (%g shares); short selling is off. If you bought it, "
                           "record the fill first (ticket.py fill)" % (sym, held))
        if kind == "entry" and value is None:
            later.append("price: no price for %s to value the order; add it with india-alerts (alerts.py quote %s "
                         "--write)" % (sym, sym))
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < (_f(c.get("min_order_inr")) or 0) - PAISE:
                later.append("size: %s is under your %s minimum order" % (L.inr(value), L.inr(c["min_order_inr"])))
            if _f(c.get("max_order_inr")) and value > float(c["max_order_inr"]) + PAISE:
                later.append("size: %s is over your %s max order" % (L.inr(value), L.inr(c["max_order_inr"])))
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            power = (cash - buffer_inr) * (lev_cap if product == "MIS" else 1.0)
            if value > power + PAISE:
                need_lev = value / max(cash - buffer_inr, 0.01)
                later.append("cash: needs %s but your cash after the buffer is %s%s; that is %.1fx leverage, over your "
                             "mis_leverage %g" % (L.inr(value), L.inr(max(cash - buffer_inr, 0)),
                                                  (" (x %g = %s)" % (lev_cap, L.inr(power))) if lev_cap > 1 else "",
                                                  need_lev, lev_cap))
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            pos_after = acct["values"].get(sym, 0) + value
            if total > 0 and pos_after > limit + PAISE:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %g%% cap"
                             % (sym, L.inr(pos_after), 100.0 * pos_after / total, L.inr(total),
                                float(c["max_position_pct"])))
        # stop, loss, reward:risk, daily loss, trades, window
        max_loss = rr = stop = target = risk_ps = None
        if kind == "entry" and side == "buy":
            stop = _f(o.get("stop_loss")) or _f(plan.get("stop"))
            target = _f(o.get("target")) or _f(plan.get("target"))
            if product == "CNC":
                warns.append("product CNC (delivery) on a day-trade plan: it will not be squared off at the close; "
                             "MIS is intraday")
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true)")
                else:
                    warns.append("no stop: the loss is not capped and not counted in the daily loss")
            elif ref:
                if stop >= ref - PAISE:
                    later.append("stop: the stop %s must be below the entry %s" % (L.p2(stop), L.p2(ref)))
                else:
                    risk_ps = round(ref - stop, 2)
                    ch = L.charges(q * ref, q * stop, c)["total"]
                    max_loss = round(q * risk_ps + (ch if c.get("include_charges", True) else 0), 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + PAISE:
                        later.append("loss: %g x %s = %s + charges %s = %s at the stop, over your %s max loss per trade"
                                     % (q, L.p2(risk_ps), L.inr(q * risk_ps), L.inr(ch), L.inr(max_loss), L.inr(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if target <= ref + PAISE:
                            later.append("target: the target %s must be above the entry %s" % (L.p2(target), L.p2(ref)))
                        else:
                            gain = q * (target - ref) - L.charges(q * ref, q * target, c)["total"]
                            rr = round(gain / max_loss, 2) if max_loss else None
                            if min_rr and rr is not None and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f after charges is under your minimum %g" % (rr, min_rr))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against")
                    # circuit limits
                    prev = None
                    try:
                        prev = (L.bhavcopy(L.prev_trading_day(now_ist.date()), fetch=False) or {}).get(sym, {}).get("c")
                    except L.DataError:
                        prev = None
                    lo_c, hi_c, how = L.circuit_limits(sym, prev, quotes.get(sym))
                    if hi_c and target and target > hi_c:
                        warns.append("the target %s is above today's upper circuit %s (%s): it cannot fill there"
                                     % (L.p2(target), L.p2(hi_c), how))
                    if lo_c and stop < lo_c:
                        warns.append("the stop %s is below today's lower circuit %s (%s)" % (L.p2(stop), L.p2(lo_c), how))
                    band = (L.price_bands().get(sym) or {}).get("band")
                    if band is not None and band <= 5:
                        warns.append("%s has a %g%% band: a circuit can freeze it and the stop may not fill" % (sym, band))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None and loss_today + batch_loss + max_loss > daily + PAISE:
                later.append("daily loss: %s already at risk today plus %s = %s, over your %s daily loss limit"
                             % (L.inr(loss_today + batch_loss), L.inr(max_loss),
                                L.inr(loss_today + batch_loss + max_loss), L.inr(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries today already, your limit is %d a day"
                             % (len(entries_today) + batch_entries, cap_t))
            win, wwarn = window_check(now_ist, c, listing_day)
            later.extend(win)
            warns.extend(wwarn)
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share, max loss %s incl. charges%s"
                            % (L.p2(ref), ref_from, L.p2(stop), L.p2(target) if target else "none", L.p2(risk_ps),
                               L.inr(max_loss), (", reward:risk %.2f" % rr) if rr is not None else ""))
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        cap_n = int(c.get("max_orders_per_day") or 0)
        passed = sum(1 for r in results if r["verdict"] == "PASS")
        if cap_n and len(done) + passed >= cap_n:
            later.append("daily limit: %d placed today plus %d passed in this batch; your limit is %d"
                         % (len(done), passed, cap_n))
        seen.add(key)
        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)
        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and value is not None:
            if kind == "entry" and side == "buy":
                batch_entries += 1
                batch_loss += max_loss or 0.0
                acct["cash"] -= value
                acct["values"][sym] = acct["values"].get(sym, 0) + value
                acct["shares"][sym] = held + (q or 0)
            elif side == "sell":
                acct["cash"] += value
                acct["shares"][sym] = held - (q or 0)
        results.append({"index": i, "symbol": sym, "side": side, "kind": kind, "qty": o.get("qty"),
                        "value_inr": round(value, 2) if value else None, "entry_ref": ref, "entry_ref_from": ref_from,
                        "stop": stop, "target": target, "risk_per_share": risk_ps, "max_loss": max_loss,
                        "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict, "reasons": reasons,
                        "warnings": warns, "info": info})
    return {"date": today, "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"), "results": results,
            "pass": sum(1 for r in results if r["verdict"] == "PASS"),
            "block": sum(1 for r in results if r["verdict"] == "BLOCK"), "warnings": top_warn,
            "today": {"orders_placed": len(done), "entries_placed": len(entries_today),
                      "loss_at_risk": round(loss_today, 2)}}


def report(res):
    out = ["Risk check, %s IST: %d order(s), %d PASS, %d BLOCK." % (res["now_ist"], len(res["results"]),
                                                                   res["pass"], res["block"])]
    for w in res["warnings"]:
        out.append("Warning: " + w)
    for r in res["results"]:
        out.append("")
        out.append("%d. %s  %s %s %s shares (%s)" % (r["index"], r["verdict"], r["side"].upper(), r["symbol"],
                                                    r["qty"], r["kind"]))
        for x in r["info"]:
            out.append("   " + x)
        for x in r["reasons"]:
            out.append("   BLOCK: " + x)
        for x in r["warnings"]:
            out.append("   note: " + x)
    t = res["today"]
    out.append("")
    out.append("Today before these: %d placed, %d entries, %s at risk." % (t["orders_placed"], t["entries_placed"],
                                                                         L.inr(t["loss_at_risk"])))
    out.append("A PASS is a rules check, not a recommendation. Next: /india-broker shows the ticket."
               if res["pass"] else "Nothing passed: nothing can be placed.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE pre-trade risk check (india-risk)")
    ap.add_argument("--orders", default=L.path("orders.json"))
    ap.add_argument("--config", default=L.path("config.json"))
    ap.add_argument("--portfolio", default=L.path("portfolio.json"))
    ap.add_argument("--prices", default=L.path("prices.json"))
    ap.add_argument("--state", default=L.path("state", "broker.json"))
    ap.add_argument("--out", default=L.path("state", "risk.json"))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    try:
        doc = L.read_json(a.orders, "orders (run /india-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not orders:
            raise RiskError("no orders in %s" % a.orders)
        pdoc = L.read_json(a.prices, default=None) or {}
        quotes = pdoc.get("quotes") or {}
        prices, times = {}, {}
        for s, qd in quotes.items():
            if _f(qd.get("price")):
                prices[s.upper()] = float(qd["price"])
                try:
                    times[s.upper()] = datetime.datetime.strptime(str(qd.get("time"))[:19].replace("T", " "),
                                                                  "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
        res = check(orders, L.load_config(a.config), L.read_json(a.portfolio, default=None), prices, quotes,
                    L.read_json(a.state, default=None), L.parse_now(a.now), times)
        if not a.no_write:
            L.write_json(a.out, res)
        print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
        return 0 if res["block"] == 0 else 1
    except (RiskError, L.DataError, ValueError) as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
