#!/usr/bin/env python3
"""Entry Timer for india-entry: watches an NSE setup bar by bar. Standard library only. Long side only.
It never places an order.

    python3 entry.py check RELIANCE                   # 15-minute opening range (9:15-9:30), 5-minute bars
    python3 entry.py check RELIANCE --write           # also save trader-in/entry.json
    python3 entry.py check TCS --setup level --level 2510
    python3 entry.py check JPL --interval 1m --min-volume-x 1.5
    python3 entry.py watch RELIANCE --until 14:45     # one line per closed bar, stops on the trigger
    python3 entry.py watchlist                        # every hit in trader-in/watchlist.json
    python3 entry.py check JPL --now "2026-10-28 10:30"   # replay a past session (IST)

The rule:
  orb    the opening range is the high and low of the first --or-minutes (15)
         of the session: 9:15-9:30. On a stock's LISTING DAY trading starts at
         10:00 (special pre-open 9:00-9:45), so the range is 10:00-10:15.
  level  a price you give, like a 20-day high from /india-scanner or the
         listing-day high from /india-ipo.
  The trigger is the first completed bar after the range (any bar for a
  level) that CLOSES above the level, inside your entry window. A wick above
  is not a trigger. The time is the bar's close time, the price its close.
  --min-volume-x X also needs the trigger bar's volume at X times the range
  bars' average.
  Entry window: no_entries_before (09:30) and no_entries_after (14:45) from
  trader-in/config.json, IST. On a listing day the window opens no earlier
  than the end of the range.
  BE / BZ (trade-for-trade) stocks cannot be day traded: it says so and stops.

Bars: Angel One SmartAPI when the ANGEL_* variables are set, else Yahoo (.NS).
A bar counts once the clock is 20 seconds past its close.
Exit codes: 0 triggered, 1 waiting / expired / no data / error.
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

INTERVALS = {"1m": 60, "5m": 300}
SETTLE = 20      # seconds after a bar's close before it counts
GRACE = 3        # seconds after SETTLE before watch asks again


class EntryError(Exception):
    pass


def r2(x):
    return None if x is None else round(float(x), 2)


def evaluate(raw_bars, symbol, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None, listing=False, series="EQ"):
    """Pure: the setup's status at now_utc from one session's bars."""
    cfg = cfg or L.load_config()
    step = INTERVALS[interval]
    if or_minutes <= 0 or (or_minutes * 60) % step:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE")
    local = L.to_ist(now_utc)
    day = local.date()
    res = {"symbol": symbol, "setup": setup, "side": "buy", "date": day.isoformat(), "interval": interval,
           "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"), "status": "waiting", "phase": "",
           "reason": "", "opening_range": None, "level": None, "trigger": None, "last": None,
           "listing_day": listing, "series": series, "min_volume_x": min_volume_x}
    if series in L.T2T_SERIES:
        res.update(status="no-data", phase="t2t", reason="%s is in series %s (trade-for-trade): every trade is "
                   "delivered, intraday is not allowed" % (symbol, series))
        return res
    ok, why = L.trading_day(day)
    if not ok:
        res.update(status="no-data", phase="closed", reason="NSE is closed today (%s); next session %s"
                   % (why, L.next_trading_day(day)))
        return res
    o, c = L.session(day, listing)
    open_dt = datetime.datetime.combine(day, o)
    close_dt = datetime.datetime.combine(day, c)
    or_end = open_dt + datetime.timedelta(minutes=or_minutes)
    before = datetime.datetime.combine(day, L.parse_hhmm(cfg["no_entries_before"], "no_entries_before"))
    if listing:
        before = max(before, or_end)
    cutoff = datetime.datetime.combine(day, L.parse_hhmm(cfg["no_entries_after"], "no_entries_after"))
    res.update(opens=L.hm(o), closes=L.hm(c), no_entries_before=L.hm(before.time()),
               no_entries_after=L.hm(cutoff.time()))
    settled = local - datetime.timedelta(seconds=SETTLE)
    bars = L.session_bars(raw_bars, day, interval, listing, settled)
    forming = [b for b in L.session_bars(raw_bars, day, interval, listing) if b["end"] > settled and b["start"] <= local]
    if forming:
        b = forming[0]
        res["forming"] = {"bar_start": L.hm(b["start"].time()), "closes": L.ampm(b["end"].time()), "price": r2(b["c"])}
    res["bars_closed"] = len(bars)
    if local < open_dt:
        res.update(phase="pre-open", reason="trading opens at %s%s" % (
            L.ampm(o), " (listing day: special pre-open until 9:45)" if listing else "") +
            (" and the %d-minute range is set at %s" % (or_minutes, L.ampm(or_end.time())) if setup == "orb" else ""))
        return res
    n_or = or_minutes * 60 // step
    or_bars = [b for b in bars if b["slot"] < n_or]
    avg_vol = (sum(b["v"] for b in or_bars) / float(len(or_bars))) if or_bars else None
    if or_bars:
        res["opening_range"] = {"high": r2(max(b["h"] for b in or_bars)), "low": r2(min(b["l"] for b in or_bars)),
                                "minutes": or_minutes, "bars": len(or_bars), "of": n_or,
                                "from": L.hm(o), "to": L.hm(or_end.time()), "complete": local >= or_end}
    if setup == "orb":
        if local < or_end:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s"
                       % (len(or_bars), n_or, L.ampm(or_end.time())))
            return res
        if not or_bars:
            res.update(status="no-data", phase="open", reason="no bars for the opening range yet")
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)
    res["level"] = r2(lvl)
    first_slot = n_or if setup == "orb" else 0
    trig, thin = None, []
    for b in bars:
        if b["slot"] < first_slot or b["end"] < before or b["end"] > cutoff:
            continue
        if b["c"] > lvl:
            vx = (b["v"] / avg_vol) if avg_vol else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": L.hm(last["start"].time()), "close": r2(last["c"]),
                       "vs_level_pct": r2(100.0 * (last["c"] / lvl - 1))}
    if trig:
        vx = (trig["v"] / avg_vol) if avg_vol else None
        res.update(status="triggered", phase="open" if local < close_dt else "after-close",
                   reason="the %s bar closed above %s" % (L.hm(trig["start"].time()),
                                                          "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": L.ampm(trig["end"].time()), "close_time": L.hm(trig["end"].time()),
                          "bar_start": L.hm(trig["start"].time()), "bar_index": trig["slot"], "price": r2(trig["c"]),
                          "volume": trig["v"], "volume_x": r2(vx), "pct_over": r2(100.0 * (trig["c"] / lvl - 1))}
        res["minutes_since"] = int((local - trig["end"]).total_seconds() // 60)
        return res
    if not bars:
        res.update(status="no-data", phase="open", reason="no closed bars for %s yet" % day)
        return res
    what = ("close above the range high %s" % L.p2(lvl)) if setup == "orb" else ("close above %s" % L.p2(lvl))
    if local >= cutoff:
        res.update(status="expired", phase="after-close" if local >= close_dt else "after-cutoff",
                   reason="no %s by %s, your last time for a new entry" % (what, L.ampm(cutoff.time())))
        return res
    res["phase"] = "open"
    if last["c"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at %s but on %.2fx the range's volume, under your %.2fx" % (
            L.hm(last["start"].time()), L.p2(last["c"]), last["v"] / avg_vol if avg_vol else 0, min_volume_x)
    elif last["c"] > lvl and last["end"] < before:
        res["reason"] = "the %s bar closed above at %s, but no entries before %s" % (
            L.hm(last["start"].time()), L.p2(last["c"]), L.hm(before.time()))
    else:
        gap = lvl - last["c"]
        res["reason"] = "last close %s (%s bar), %s %s %s (%+.2f%%)" % (
            L.inr(last["c"]), L.hm(last["start"].time()), L.inr(abs(gap)), "under" if gap >= 0 else "over",
            ("the range high %s" % L.p2(lvl)) if setup == "orb" else ("the level %s" % L.p2(lvl)),
            100.0 * (last["c"] / lvl - 1))
    return res


def entry_doc(res, now_utc, source):
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    return {"created_by": "india-entry", "symbol": res["symbol"], "setup": res["setup"], "side": "buy",
            "date": res["date"], "status": res["status"], "time": t.get("time"), "bar_start": t.get("bar_start"),
            "bar_index": t.get("bar_index"), "price": t.get("price"), "level": res.get("level"),
            "interval": res["interval"], "listing_day": res.get("listing_day", False),
            "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"],
                              "from": orr["from"]} if orr else None,
            "source": source, "checked_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": res.get("reason")}


LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}


def describe(res, source):
    out = []
    setup = ("opening-range breakout (%d-minute range)" % res["or_minutes"]) if res["setup"] == "orb" else \
        "close above a level"
    out.append("%s  %s IST  %s on %s bars (%s)%s" % (res["symbol"], res["now"][:16], setup,
                                                     "5-minute" if res["interval"] == "5m" else "1-minute", source,
                                                     "  LISTING DAY: trading from 10:00" if res.get("listing_day") else ""))
    orr = res.get("opening_range")
    if orr:
        out.append("Opening range %s-%s: high %s, low %s (%d of %d bars)." % (
            orr["from"], orr["to"], L.p2(orr["high"]), L.p2(orr["low"]), orr["bars"], orr["of"]))
    if res["status"] == "triggered":
        t = res["trigger"]
        out.append("TRIGGERED %s at %s: the %s bar (bar %d) closed above %s (%+.2f%%)%s." % (
            t["time"], L.inr(t["price"]), t["bar_start"], t["bar_index"],
            ("the range high %s" % L.p2(res["level"])) if res["setup"] == "orb" else ("the level %s" % L.p2(res["level"])),
            t["pct_over"], (", volume %.2fx the range" % t["volume_x"]) if t.get("volume_x") else ""))
        if res.get("minutes_since", 0) > 10:
            out.append("That was %d minutes ago: a late entry is a different trade." % res["minutes_since"])
        out.append("This is the moment to get in, if you take the trade. Nothing was placed.")
        out.append("Next: set the stop and target BEFORE you buy: /india-exit (exit.py plan).")
    else:
        word = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED",
                "t2t": "NOT A DAY TRADE"}.get(res["phase"], LABEL[res["status"]])
        out.append("%s  %s." % (word, res["reason"]))
        if res.get("forming") and res["status"] == "waiting":
            f = res["forming"]
            out.append("The %s bar is still forming (%s so far, closes %s); it does not count until it closes." % (
                f["bar_start"], L.inr(f["price"]), f["closes"]))
    return out


def load(a, sym, now_utc, cfg):
    day = L.to_ist(now_utc).date()
    info = L.stock_info(sym)
    if not info:
        raise EntryError("%s is not on NSE's equity list (check the symbol: RELIANCE, not RELIANCE.NS)" % sym)
    listing = info.get("listed") == day
    series = info.get("series") or "EQ"
    notes = []
    if series in L.T2T_SERIES or not L.trading_day(day)[0]:
        return [], "-", listing, series, notes      # evaluate() says why; no bars needed
    raw, src = L.bars(sym, a.interval, day, day, cfg, notes)
    return raw, src, listing, series, notes


def run_check(a, sym, cfg):
    now = L.parse_now(a.now)
    raw, src, listing, series, notes = load(a, sym, now, cfg)
    res = evaluate(raw, sym, now, a.setup, a.or_minutes, a.interval, a.level, a.min_volume_x, cfg, listing, series)
    return res, src, notes, now


def cmd_check(a):
    cfg = L.load_config(a.config)
    sym = a.symbol.upper()
    res, src, notes, now = run_check(a, sym, cfg)
    if a.write:
        L.write_json(a.out, entry_doc(res, now, src))
    if a.json:
        print(json.dumps(dict(res, source=src, notes=notes), indent=2, ensure_ascii=False))
    else:
        print("\n".join(describe(res, src)))
        for n in notes:
            print("Note: " + n)
        if a.write:
            print("Wrote %s." % a.out)
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = L.load_config(a.config)
    sym = a.symbol.upper()
    if a.until:
        cfg["no_entries_after"] = a.until
    if a.now:
        # replay: step the clock bar by bar through the session without waiting
        start = L.parse_now(a.now)
        local = L.to_ist(start)
        step = INTERVALS[a.interval]
        raw, src, listing, series, notes = load(a, sym, start, cfg)
        t = local
        last_n = -1
        while True:
            res = evaluate(raw, sym, L.ist_to_utc(t), a.setup, a.or_minutes, a.interval, a.level, a.min_volume_x,
                           cfg, listing, series)
            if res.get("bars_closed", 0) != last_n or res["status"] != "waiting":
                last_n = res.get("bars_closed", 0)
                print("%s  %-9s %s" % (L.hm(t.time()), LABEL[res["status"]], res["reason"]))
            if res["status"] != "waiting" or t.time() >= L.CLOSE:
                break
            t += datetime.timedelta(seconds=step)
        if res["status"] == "triggered":
            L.write_json(a.out, entry_doc(res, L.ist_to_utc(t), src))
            print("Wrote %s." % a.out)
        return 0 if res["status"] == "triggered" else 1
    step = INTERVALS[a.interval]
    errors = 0
    while True:
        try:
            res, src, notes, now = run_check(a, sym, cfg)
            errors = 0
        except L.DataError as e:
            errors += 1
            print("%s  data error (%d of 5): %s" % (L.hm(L.to_ist(L.utc_now()).time()), errors, e), flush=True)
            if errors >= 5:
                return 1
            time.sleep(30)
            continue
        print("%s  %-9s %s" % (res["now"][11:16], LABEL[res["status"]], res["reason"]), flush=True)
        if res["status"] == "triggered":
            L.write_json(a.out, entry_doc(res, now, src))
            print("\n".join(describe(res, src)))
            print("Wrote %s." % a.out)
            return 0
        if res["status"] in ("expired",) or res["phase"] in ("closed", "t2t", "after-close"):
            return 1
        ts = L.ts_of(L.utc_now())
        wake = (ts // step + 1) * step + SETTLE + GRACE
        time.sleep(max(5, wake - ts))


def cmd_watchlist(a):
    cfg = L.load_config(a.config)
    wl = L.read_json(a.watchlist, "watchlist (run /india-scanner or /india-ipo recent --write first)")
    syms = [str(h.get("symbol", "")).upper() for h in wl.get("hits", [])][:a.max]
    if not syms:
        print("The watchlist is empty.")
        return 1
    hits = 0
    for sym in syms:
        try:
            res, src, notes, now = run_check(a, sym, cfg)
        except (EntryError, L.DataError) as e:
            print("%-12s error: %s" % (sym, e))
            continue
        hits += res["status"] == "triggered"
        t = res.get("trigger") or {}
        print("%-12s %-9s %s" % (sym, LABEL[res["status"]], ("%s at %s" % (t["time"], L.inr(t["price"])))
                                 if t else res["reason"]))
    print("%d of %d triggered. Nothing was placed." % (hits, len(syms)))
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE entry timer (india-entry). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("check", "watch", "watchlist"):
        p = sub.add_parser(name)
        if name != "watchlist":
            p.add_argument("symbol")
        else:
            p.add_argument("--watchlist", default=L.path("watchlist.json"))
            p.add_argument("--max", type=int, default=25)
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--level", type=float)
        p.add_argument("--interval", choices=["5m", "1m"], default="5m")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--min-volume-x", type=float)
        p.add_argument("--now", help="pretend it is this IST time, \"YYYY-MM-DD HH:MM\"")
        p.add_argument("--config", default=L.path("config.json"))
        p.add_argument("--out", default=L.path("entry.json"))
        p.add_argument("--json", action="store_true")
        p.add_argument("--write", action="store_true")
        if name == "watch":
            p.add_argument("--until", help="last time for a new entry, HH:MM IST (default: the config's)")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except (EntryError, L.DataError, ValueError) as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
