#!/usr/bin/env python3
"""Exit Planner for india-exit: the stop and the target BEFORE you enter. Standard library only.
Long side only. It never places an order.

    python3 exit.py plan                                  # from trader-in/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-low --target 1.5R --write
    python3 exit.py plan --symbol RELIANCE --entry 1174.30 --or-high 1170.50 --or-low 1156.20
    python3 exit.py plan --symbol TCS --entry 2480 --setup level --stop pct:1
    python3 exit.py plan --stop atr:1.5                   # 14-bar ATR of the session up to the entry
    python3 exit.py show

Stops (a long's stop rounds DOWN onto the stock's NSE tick, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on the session's bars up to
             the entry bar (Angel One or Yahoo). Fewer than 14 bars: all of them.
Targets (rounded to the nearest tick): 2R (default), 1R, 1.5R ... or price:X.

The tick: Angel One's scrip master when cached (it has each stock's own tick),
else NSE's table: Re 0.01 under Rs 250, 0.05 to 1,000, 0.10 to 5,000, 0.50 to
10,000, 1.00 to 20,000, 5.00 above. A price off the tick is rejected by the
exchange, so every price here is on it.

Refused, with the reason: a stop at or above the entry, a target at or below
it, a risk under 0.05% of the price or under 2 ticks (inside the spread's
noise), an entry.json that has not triggered (unless you give --entry), a
trade-for-trade (BE / BZ) stock, a short.

Circuit limits: with yesterday's close (NSE bhavcopy) and the stock's band
(NSE sec_list) it warns when the target or the stop lies beyond today's
limits: a target past the upper circuit cannot fill, and at the lower circuit
there may be no buyers for your stop.

flat_by (default 15:05, or "flat_by" in trader-in/config.json): out by then,
win or lose. Your broker squares off intraday (MIS) positions on its own
(Angel One about 15:15, Groww about 15:10, Zerodha about 15:20: check yours);
it warns when flat_by is not before that.
"""

import argparse
import datetime
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")
NOTE = "A stop is a plan, not a guarantee: a gap, a circuit or a fast market can fill past it."
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


class PlanError(Exception):
    pass


def num(x):
    return format(L.D(x).normalize(), "f")


def ratio(x):
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*(?:₹|rs\.?)?\s*([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), L.D(m.group(2))
    if val <= 0 or (kind == "pct" and val >= 100):
        raise PlanError("--stop %s needs a number above 0%s" % (kind, " and under 100" if kind == "pct" else ""))
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = L.D(m.group(1))
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*(?:₹|rs\.?)?\s*([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", L.D(m.group(1))
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def compute(entry, stop_rule, stop_param, target_rule, target_param, tick, or_high=None, or_low=None,
            atr=None, atr_bars=None):
    """Stop, target and risk on the tick grid. Raises PlanError with the reason."""
    tick = L.D(tick)
    e = L.to_tick(entry, tick)
    if e <= 0:
        raise PlanError("the entry price must be above ₹0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = L.D(or_high), L.D(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (L.p2(oh), L.p2(ol)))
        if stop_rule == "or_mid":
            raw, label = (oh + ol) / 2, "opening-range midpoint"
            detail = "(%s + %s) / 2 = %s" % (L.p2(oh), L.p2(ol), num(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = %s" % (L.p2(e), num(stop_param), num(raw.quantize(Decimal("0.0001"))))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        a = L.D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (num(stop_param), atr_bars)
        detail = "ATR %s over the %d bars to the entry%s" % (num(a.quantize(Decimal("0.0001"))), atr_bars,
                                                            " (fewer than 14)" if atr_bars < ATR_BARS else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = L.to_tick(raw, tick, "down")
    if stop != L.D(raw):
        detail = (detail + ", rounded down to the %s tick" % num(tick)) if detail else \
            "%s rounded down to the %s tick" % (num(L.D(raw)), num(tick))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (L.p2(stop), label, L.p2(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above ₹0" % L.p2(stop))
    risk = e - stop
    floor_risk = max(e * MIN_RISK_PCT / 100, 2 * tick)
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than %s (0.05%% of the price or 2 ticks): too tight "
                        "to survive the spread. Widen it." % (L.inr(risk), L.inr(floor_risk)))
    if target_rule == "r":
        target = L.to_tick(e + target_param * risk, tick)
        n = int(target_param) if target_param == target_param.to_integral_value() else None
        t_label = "%sR (%s times the risk)" % (num(target_param), WORDS.get(n, num(target_param)))
    else:
        target = L.to_tick(target_param, tick)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (L.p2(target), L.p2(e)))
    rr = ((target - e) / risk).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label, "risk": risk,
            "reward_risk": rr, "tick": tick}


def atr_of(bars, n=ATR_BARS):
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


def gather(a):
    if a.side in ("sell", "short"):
        raise PlanError("this pack plans longs only. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        return {"symbol": a.symbol.upper(), "entry": a.entry, "setup": a.setup or ("orb" if a.or_high else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "or_from": None,
                "listing_day": None, "source": "flags"}
    p = a.from_path
    doc = L.read_json(p, "entry (run /india-entry first, or give --symbol and --entry)")
    if doc.get("status") != "triggered" and a.entry is None:
        raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                        % (p, doc.get("symbol"), doc.get("status") or "unknown"))
    orr = doc.get("opening_range") or {}
    return {"symbol": str(doc.get("symbol") or "").upper(), "entry": a.entry if a.entry is not None else doc.get("price"),
            "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
            "entry_time": a.time or (doc.get("time") if a.entry is None else None),
            "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
            "interval": a.interval or doc.get("interval") or "5m",
            "or_high": a.or_high if a.or_high is not None else orr.get("high"),
            "or_low": a.or_low if a.or_low is not None else orr.get("low"),
            "or_minutes": orr.get("minutes") or a.or_minutes, "or_from": orr.get("from"),
            "listing_day": doc.get("listing_day"), "source": p}


def build_plan(a, now_utc):
    info = gather(a)
    cfg = L.load_config(a.config)
    sym = info["symbol"]
    day = datetime.date.fromisoformat(str(info["date"])) if info["date"] else L.to_ist(now_utc).date()
    stock = L.stock_info(sym)
    if not stock:
        raise PlanError("%s is not on NSE's equity list" % sym)
    if stock.get("series") in L.T2T_SERIES and not cfg.get("allow_t2t"):
        raise PlanError("%s is in series %s (trade-for-trade): delivery only, it cannot be day traded"
                        % (sym, stock["series"]))
    listing = info["listing_day"] if info["listing_day"] is not None else stock.get("listed") == day
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    tick = L.tick_for(info["entry"], sym)
    atr = atr_n = None
    if stop_rule == "atr":
        raw, _src = L.bars(sym, info["interval"], day, day, cfg)
        bars = L.session_bars(raw, day, info["interval"], listing)
        if info["entry_bar"] is not None:
            bars = [b for b in bars if b["slot"] <= info["entry_bar"]]
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed bars by the entry; found %d" % len(bars))
        atr, atr_n = atr_of(bars)
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, tick, info["or_high"],
                info["or_low"], atr, atr_n)
    flat = L.parse_hhmm(a.flat_by or cfg.get("flat_by") or "15:05", "flat_by")
    warnings = []
    broker = L.BROKERS.get(str(cfg.get("broker", "angelone")).lower())
    if broker and flat >= L.parse_hhmm(broker["square_off"]):
        warnings.append("flat_by %s is not before %s's usual intraday square-off (about %s): it may close the "
                        "position for you, with a fee. Set flat_by earlier." % (L.hm(flat), broker["name"],
                                                                                broker["square_off"]))
    if info["entry_time"]:
        et = datetime.datetime.strptime(info["entry_time"].strip(), "%I:%M %p").time() \
            if ("AM" in info["entry_time"] or "PM" in info["entry_time"]) else L.parse_hhmm(info["entry_time"])
        if et >= flat:
            raise PlanError("the entry at %s is at or after your flat-by %s: no time left to hold it"
                            % (info["entry_time"], L.hm(flat)))
    # circuit limits from yesterday's close and the band
    prev = None
    if not listing:
        try:
            rows = L.bhavcopy(L.prev_trading_day(day))
            prev = (rows or {}).get(sym, {}).get("c")
        except L.DataError:
            prev = None
    lo_c, hi_c, how = L.circuit_limits(sym, prev)
    if listing:
        warnings.append("listing day: the price band is set from the auction (equilibrium) price, not a previous "
                        "close; check the stock's limits in your broker app")
    if hi_c and float(c["target"]) > hi_c:
        warnings.append("the target %s is above today's upper circuit %s (%s): it cannot fill there"
                        % (L.p2(c["target"]), L.p2(hi_c), how))
    if lo_c and float(c["stop"]) < lo_c:
        warnings.append("the stop %s is below today's lower circuit %s (%s): at the circuit there may be no buyers"
                        % (L.p2(c["stop"]), L.p2(lo_c), how))
    if stock.get("band") is not None and stock["band"] <= 5:
        warnings.append("%s has a %g%% band: a circuit can freeze it, and a stop may not fill" % (sym, stock["band"]))
    mrr = cfg.get("min_reward_risk")
    if mrr is not None and c["reward_risk"] < L.D(mrr):
        warnings.append("reward:risk %s is under your min_reward_risk %s: india-risk will block it"
                        % (ratio(c["reward_risk"]), num(L.D(mrr))))
    if day != L.to_ist(now_utc).date():
        warnings.append("this plan is for %s, not today (%s)" % (day, L.to_ist(now_utc).date()))
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(info["or_high"]), "low": float(info["or_low"]), "minutes": int(info["or_minutes"] or 15),
               "from": info["or_from"] or ("10:00" if listing else "09:15")}
    plan = {"created_by": "india-exit", "symbol": sym, "side": "buy", "date": day.isoformat(), "currency": "INR",
            "setup": info["setup"], "entry": float(c["entry"]), "entry_time": info["entry_time"],
            "entry_bar": info["entry_bar"], "interval": info["interval"], "listing_day": bool(listing),
            "opening_range": orr, "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "stop_detail": c["stop_detail"], "target": float(c["target"]), "target_r": float(c["target_r"]),
            "target_label": c["target_label"], "risk_per_share": float(c["risk"]),
            "reward_risk": float(c["reward_risk"]), "tick": float(c["tick"]), "flat_by": L.hm(flat),
            "series": stock.get("series"), "band_pct": stock.get("band"),
            "circuit": {"lower": lo_c, "upper": hi_c, "how": how} if hi_c else None,
            "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE}
    if atr is not None:
        plan["atr"], plan["atr_bars"] = round(atr, 4), atr_n
    return plan, warnings


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e, risk = L.D(plan["entry"]), L.D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    when = (" at %s" % plan["entry_time"]) if plan.get("entry_time") else ""
    if plan.get("entry_bar") is not None and when:
        when += " (bar %d)" % plan["entry_bar"]
    out = ["Exit plan: %s long, %s, %s%s" % (plan["symbol"], L.day_label(day), setup,
                                            ", LISTING DAY" if plan.get("listing_day") else "")]
    out.append("  Entry         %s%s" % (L.inr(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (L.inr(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (L.inr(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry), tick %s" % (L.inr(plan["risk_per_share"]), pct,
                                                                     num(L.D(plan["tick"]))))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    out.append("  Flat by       %s IST, win or lose: no overnight hold (MIS)" % plan["flat_by"])
    if plan.get("circuit"):
        out.append("  Circuit       %s to %s today (%s)" % (L.p2(plan["circuit"]["lower"]),
                                                            L.p2(plan["circuit"]["upper"]), plan["circuit"]["how"]))
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


def cmd_plan(a):
    now = L.parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        L.write_json(a.write, plan)
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2, ensure_ascii=False))
        return 0
    print("\n".join(describe(plan)))
    for w in warnings:
        print("Note: " + w + ".")
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: /india-timemachine replays it, then /india-budget sizes it. Nothing was placed.")
    return 0


def cmd_show(a):
    plan = L.read_json(a.plan, "plan (make one: exit.py plan --write)")
    if a.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    print("\n".join(describe(plan)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE exit planner (india-exit). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--from", dest="from_path", default=L.path("entry.json"))
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--side", default="buy")
    p.add_argument("--date")
    p.add_argument("--time", help="entry time, like \"10:15 AM\"")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--stop")
    p.add_argument("--target")
    p.add_argument("--flat-by")
    p.add_argument("--config", default=L.path("config.json"))
    p.add_argument("--write", nargs="?", const=L.path("plan.json"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--now")
    p = sub.add_parser("show")
    p.add_argument("--plan", default=L.path("plan.json"))
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except (PlanError, L.DataError, ValueError) as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
