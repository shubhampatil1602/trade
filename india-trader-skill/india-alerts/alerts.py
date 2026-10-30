#!/usr/bin/env python3
"""Price alerts and the NSE clock for india-alerts. Standard library only.

    python3 alerts.py clock                          # is NSE open? pre-open, next open, holidays
    python3 alerts.py clock --refresh                # re-read NSE's holiday list (new years)
    python3 alerts.py quote RELIANCE TCS --write     # quotes -> trader-in/prices.json
    python3 alerts.py quote --holdings --write       # every symbol in your portfolio and plan
    python3 alerts.py add --from-plan                # stop, target and flat-by from trader-in/plan.json
    python3 alerts.py add --symbol RELIANCE --stop 1163.30
    python3 alerts.py add --symbol TCS --above 2510
    python3 alerts.py add --symbol JPL --pct 5
    python3 alerts.py add --at 15:05 --label "flat by close" --symbol RELIANCE
    python3 alerts.py add --opening-bell
    python3 alerts.py check                          # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py list
    python3 alerts.py reset reliance-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        moved "pct" percent or more from the previous close; "direction" up / down
  opening_bell    NSE opened today (9:15 IST; rings once a trading day)
  time            the IST clock passed "at" (HH:MM) on a trading day (once a day)

A rule with "date" is watched on that IST date only (plan rules are for the
plan's day). Price alerts ring ONCE until you reset them ("rearm": true re-arms
when the price crosses back). Quotes: Angel One (FULL, with the day's circuit
limits) when the ANGEL_* variables are set, else Yahoo (delayed). For alerts
only: never time an order on them. "check" on a /loop every minute or two is
how Claude watches a trade.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell", "time"}
NO_QUOTE = {"opening_bell", "time"}
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class AlertError(Exception):
    pass


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    return datetime.time(int(m.group(1)), int(m.group(2))) if m else None


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s needs \"at\" as an IST time like \"15:05\"" % rule.get("id"))


def _rang_today(stamp, today):
    if not stamp:
        return False
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp[:10] == today
    return L.to_ist(utc).date().isoformat() == today


def evaluate(rules, quotes, status, state, now_utc):
    """Pure. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    local = L.to_ist(now_utc)
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        if r.get("date") and str(r["date"]) != today:
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": "", "message": "Opening bell: NSE is open (%s IST)." % status["now_ist"]})
                rung[rid] = stamp
            continue
        if typ == "time":
            at = _hhmm(r["at"])
            if status.get("opens") and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                msg = "%s%s: it is %s IST (set for %s)" % (label[:1].upper(), label[1:], local.strftime("%H:%M"),
                                                           at.strftime("%H:%M"))
                msg += ", NSE closes at 15:30." if status["open"] else ", NSE has closed."
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    msg += (" If %s is still open, close it now, before your broker's square-off: india-broker "
                            "flatten --symbol %s, then india-risk and your yes. Cancel the resting SL-M first."
                            % (sym, sym))
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        sym = r["symbol"].upper()
        q = quotes.get(sym)
        if not q or q.get("price") is None:
            continue
        p = float(q["price"])
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                extra = ""
                if r["type"] == "target":
                    extra = " Sell at market now and CANCEL the resting SL-M stop (do not leave both open)."
                elif r["type"] == "stop":
                    extra = " Check the SL-M filled; if it did not (a gap or a circuit), sell now."
                rings.append({"id": rid, "symbol": sym, "message": "%s %s %s: %s your %s %s%s.%s" % (
                    sym, "is" if status["open"] else "last", L.inr(p), word, label, L.p2(r["price"]),
                    (" (" + r["note"] + ")") if r.get("note") else "", extra)})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            if abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0)) and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to %s (from %s)."
                              % (sym, move, L.inr(p), L.p2(ref))})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def rules_from_plan(plan, cfg_flat_by=None):
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run /india-exit first)")
    sym = str(plan["symbol"]).upper()
    out = []
    for key in ("stop", "target"):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s" % key)
        out.append({"id": "%s-plan-%s" % (sym.lower(), key), "symbol": sym, "type": key, "price": float(v),
                    "note": "plan %s" % key})
    flat = plan.get("flat_by") or cfg_flat_by or "15:05"
    out.append({"id": "%s-flat-by" % sym.lower(), "symbol": sym, "type": "time", "at": flat, "label": "flat by close"})
    if plan.get("date"):
        for r in out:
            r["date"] = str(plan["date"])
    return out


def get_quotes(symbols, cfg, notes):
    return L.quotes(symbols, cfg, notes)


def write_prices(path, quotes, now_utc):
    doc = L.read_json(path, default=None) or {}
    q = doc.get("quotes") or {}
    q.update(quotes)
    L.write_json(path, {"created_by": "india-alerts", "updated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "quotes": q})


def cmd_clock(a):
    if a.refresh:
        n = L.refresh_holidays()
        print("Refreshed NSE's holiday list: %d holidays (%s)." % (n, ", ".join(map(str, sorted(L.known_years())))))
    now = L.parse_now(a.now)
    s = L.market_status(now)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0 if s["open"] else 1
    word = {"open": "OPEN", "pre-open": "PRE-OPEN (9:00-9:08 order entry, 9:08-9:15 matching)",
            "before pre-open": "CLOSED (pre-open starts at 9:00)", "closed": "CLOSED"}[s["phase"]]
    print("NSE equity market, %s IST: %s%s." % (s["now_ist"], word, (" (%s)" % s["why"]) if s["why"] else ""))
    if s["open"]:
        print("Normal market until 15:30.")
    if s.get("next_open"):
        print("Next open: %s IST." % s["next_open"])
    upcoming = sorted((d, n) for d, n in L.holidays().items() if d >= s["date"])[:3]
    if upcoming:
        print("Next holidays: " + "; ".join("%s %s" % (d, n) for d, n in upcoming))
    if s.get("calendar_note"):
        print("Note: " + s["calendar_note"])
    return 0 if s["open"] else 1


def cmd_quote(a):
    cfg = L.load_config(a.config)
    syms = [s.upper() for s in a.symbols]
    if a.holdings:
        syms += [s for s in L.watch_symbols(watchlist=False) if s not in syms]
    if not syms:
        raise AlertError("give symbols, or --holdings")
    notes = []
    qs = get_quotes(syms, cfg, notes)
    now = L.parse_now(a.now)
    if a.write and qs:
        write_prices(a.prices, qs, now)
    if a.json:
        print(json.dumps({"quotes": qs, "notes": notes}, indent=2, ensure_ascii=False))
    else:
        for s in syms:
            q = qs.get(s)
            if not q:
                print("%-12s no quote" % s)
                continue
            chg = (" %+.2f%%" % (100 * (q["price"] / q["prev_close"] - 1))) if q.get("prev_close") else ""
            circ = ("  circuit %s-%s" % (L.p2(q["lower_circuit"]), L.p2(q["upper_circuit"]))) \
                if q.get("upper_circuit") else ""
            print("%-12s %12s%s  (%s, %s)%s" % (s, L.inr(q["price"]), chg, q.get("source"), q.get("time") or "-", circ))
        for n in notes:
            print("Note: " + n)
        if a.write and qs:
            print("Wrote %s." % a.prices)
    return 0 if qs else 1


def cmd_add(a):
    rules = L.read_json(a.file, default=None) or {"rules": []}
    new = []
    if a.from_plan:
        cfg = L.load_config(a.config)
        new = rules_from_plan(L.read_json(a.plan, "plan (run /india-exit first)"), cfg.get("flat_by"))
    elif a.opening_bell:
        new = [{"id": "opening-bell", "type": "opening_bell"}]
    elif a.at:
        sym = (a.symbol or "").upper()
        new = [{"id": "%s-time-%s" % (sym.lower() or "market", a.at.replace(":", "")), "type": "time", "at": a.at,
                "label": a.label or "time alert", "symbol": sym or None}]
    else:
        if not a.symbol:
            raise AlertError("give --symbol with --stop, --target, --above, --below or --pct")
        sym = a.symbol.upper()
        for typ, v in (("stop", a.stop), ("target", a.target), ("above", a.above), ("below", a.below)):
            if v is not None:
                new.append({"id": "%s-%s-%s" % (sym.lower(), typ, ("%g" % v).replace(".", "_")), "symbol": sym,
                            "type": typ, "price": float(v)})
        if a.pct is not None:
            new.append({"id": "%s-pct-%g" % (sym.lower(), a.pct), "symbol": sym, "type": "pct_move", "pct": a.pct})
    if not new:
        raise AlertError("nothing to add")
    if a.date:
        for r in new:
            r["date"] = a.date
    for r in new:
        validate(r)
    ids = {r["id"] for r in new}
    rules["rules"] = [r for r in rules.get("rules", []) if r.get("id") not in ids] + new
    L.write_json(a.file, rules)
    for r in new:
        what = {"time": "at %s IST" % r.get("at"), "opening_bell": "at the opening bell",
                "pct_move": "on a %g%% move" % r.get("pct", 0)}.get(r["type"], "%s %s" % (r["type"], L.p2(r["price"]))
                                                                    if r.get("price") else r["type"])
        print("Added %s: %s %s%s." % (r["id"], r.get("symbol") or "NSE", what, (" on " + r["date"]) if r.get("date") else ""))
    print("Run alerts.py check (on a /loop every minute or two while the trade is open).")
    return 0


def cmd_check(a):
    now = L.parse_now(a.now)
    status = L.market_status(now)
    rules = (L.read_json(a.file, default=None) or {}).get("rules", [])
    if not rules:
        print("No alerts set. alerts.py add --from-plan makes the stop, target and flat-by alerts.")
        return 1
    today = status["date"]
    syms = sorted({r["symbol"].upper() for r in rules if r.get("type") not in NO_QUOTE and r.get("symbol")
                   and (not r.get("date") or r["date"] == today)})
    notes, quotes = [], {}
    if syms and (status["open"] or a.after_hours):
        quotes = get_quotes(syms, L.load_config(a.config), notes)
        if quotes:
            write_prices(a.prices, quotes, now)
    state = L.read_json(a.state, default=None) or {}
    rings, state = evaluate(rules, quotes, status, state, now)
    L.write_json(a.state, state)
    if a.json:
        print(json.dumps({"rings": rings, "status": status, "notes": notes}, indent=2, ensure_ascii=False))
        return 0 if rings else 1
    for r in rings:
        print("RING  " + r["message"])
    if not rings:
        print("Nothing rang (%s IST, NSE %s%s)." % (status["now_ist"], status["phase"],
                                                   ", %d symbol(s) quoted" % len(quotes) if quotes else ""))
    for n in notes:
        print("Note: " + n)
    return 0 if rings else 1


def cmd_list(a):
    rules = (L.read_json(a.file, default=None) or {}).get("rules", [])
    state = L.read_json(a.state, default=None) or {}
    if not rules:
        print("No alerts set.")
        return 1
    for r in rules:
        rang = state.get("rung", {}).get(r.get("id"))
        print("%-28s %-12s %-12s %s%s%s" % (r.get("id"), r.get("symbol") or "-", r["type"],
                                            L.p2(r["price"]) if r.get("price") else (r.get("at") or r.get("pct") or ""),
                                            (" on " + r["date"]) if r.get("date") else "",
                                            ("  rang " + rang) if rang else ""))
    return 0


def cmd_reset(a):
    state = L.read_json(a.state, default=None) or {}
    if a.id in state.get("rung", {}):
        del state["rung"][a.id]
        L.write_json(a.state, state)
        print("Re-armed %s." % a.id)
        return 0
    print("%s has not rung." % a.id)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE clock and price alerts (india-alerts)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("clock")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--now")
    p = sub.add_parser("quote")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true")
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("--from-plan", action="store_true")
    p.add_argument("--plan", default=L.path("plan.json"))
    p.add_argument("--symbol")
    for k in ("--stop", "--target", "--above", "--below", "--pct"):
        p.add_argument(k, type=float)
    p.add_argument("--at")
    p.add_argument("--label")
    p.add_argument("--opening-bell", action="store_true")
    p.add_argument("--date")
    p = sub.add_parser("check")
    p.add_argument("--json", action="store_true")
    p.add_argument("--after-hours", action="store_true", help="quote even when NSE is closed")
    sub.add_parser("list")
    p = sub.add_parser("reset")
    p.add_argument("id")
    for p in sub.choices.values():
        p.add_argument("--file", default=L.path("alerts.json"))
        p.add_argument("--state", default=L.path("state", "alerts.json"))
        p.add_argument("--prices", default=L.path("prices.json"))
        p.add_argument("--config", default=L.path("config.json"))
        if "--now" not in p._option_string_actions:
            p.add_argument("--now")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "add": cmd_add, "check": cmd_check, "list": cmd_list,
                "reset": cmd_reset}[a.cmd](a)
    except (AlertError, L.DataError, ValueError) as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
