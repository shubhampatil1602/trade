#!/usr/bin/env python3
"""Chart Scanner for india-scanner: NSE setups. Standard library only.

    python3 scan.py universe                       # NSE's equity list -> trader-in/universe.txt
    python3 scan.py daily                          # before 9:15: daily setups on every NSE EQ stock
    python3 scan.py daily --min-turnover-cr 25     # only stocks that traded Rs 25 crore+ yesterday
    python3 scan.py orb                            # after 9:35: opening-range breakouts so far
    python3 scan.py orb --at 10:30 --top 100       # cut the clock at 10:30 IST, 100 most traded stocks
    python3 scan.py orb --symbols RELIANCE,TCS,JPL

universe  NSE's EQUITY_L.csv: series EQ (intraday allowed). BE / BZ
          (trade-for-trade: delivery only, no intraday) are listed apart
          and left out of the scans. SME stocks are not on this list.

daily     Pre-market scan of daily CLOSES from NSE's own bhavcopy (one file
          a session for every stock; the last 45 sessions are cached in
          trader-in/cache/bhav/, so only new days download). Liquidity: the
          last close >= --min-price (50) and that day's turnover >=
          --min-turnover-cr (10, rupees crore). Setups, closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10% over the 10 sessions before the last 5,
                             then the last 5 closes inside a 5% range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          A stock needs 30 sessions of history. Younger stocks (recent IPOs)
          are counted and handed to /india-ipo, which has setups for them.

orb       Opening-range breakouts on 5-minute bars. The range is the first
          --or-minutes (15) of the session: 9:15 to 9:30. On a stock's
          LISTING DAY the market opens for it at 10:00 (special pre-open
          9:00-9:45), so its range is 10:00 to 10:15. A hit is the first
          completed bar after the range to CLOSE above the range high. A
          chart is usable only when every bar from the open to the cutoff is
          there. Symbols: --symbols, else the --top (150) most traded EQ stocks
          of the last session (bhavcopy turnover), one bar request each.
          Bars come from Angel One SmartAPI when the ANGEL_* variables are
          set, else Yahoo (.NS).

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

MIN_DAILY_BARS = 30
HISTORY = 45
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


def progress(a, msg):
    if not getattr(a, "json", False):
        print(msg, file=sys.stderr)


def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups(c):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = rsi(c), rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "high20": hi20, "ma20": ma20, "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(history, symbols, min_price, min_turnover_cr):
    """Pure. history = [(date, {SYM: row})], oldest first. Returns (hits, counts, young)."""
    counts = {"universe": len(symbols), "illiquid": 0, "short": 0, "scanned": 0, "missing": 0}
    hits, young = [], []
    last_day, last_rows = history[-1]
    for sym in symbols:
        row = last_rows.get(sym)
        if not row or row["series"] != "EQ":
            counts["missing"] += 1
            continue
        turnover_cr = row["c"] * row["vol"] / 1e7
        if row["c"] < min_price or turnover_cr < min_turnover_cr:
            counts["illiquid"] += 1
            continue
        daily, actions = L.adjusted_daily(history, sym)
        closes = [x["c"] for x in daily]
        if len(closes) < MIN_DAILY_BARS:
            counts["short"] += 1
            young.append(sym)
            continue
        counts["scanned"] += 1
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": last_day.isoformat(), "rsi": round(nums["rsi"], 1),
                         "turnover_cr": round(turnover_cr, 1), "deliv_pct": row.get("deliv_pct"),
                         "high20": round(nums["high20"], 2), "adjusted": actions or None})
    hits.sort(key=lambda h: (-h["turnover_cr"], h["symbol"]))
    counts["hits"] = len(hits)
    return hits, counts, young


def orb_hit(bars, n_range):
    """The first completed bar after the range to CLOSE above the range high, or None."""
    rng = bars[:n_range]
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for b in bars[n_range:]:
        if b["c"] > oh:
            return {"price": round(b["c"], 2), "at": L.ampm(b["end"].time()), "bar_start": L.hm(b["start"].time()),
                    "bar_index": b["slot"], "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": b["end"]}
    return None


def usable(bars, n_range, open_dt, cutoff):
    """Every opening-range bar is there, and at most 1 in 15 of the bars after it is missing
    (Yahoo drops a few NSE bars; a longer gap is a halt or no trades)."""
    want = min(int((cutoff - open_dt).total_seconds() // (L.BAR_MIN * 60)), L.SESSION_BARS)
    slots = {b["slot"] for b in bars}
    if want < n_range or not all(k in slots for k in range(n_range)):
        return False
    return want - len(slots) <= max(1, (want - n_range) // 15)


def cmd_universe(a):
    eq = L.equity_list()
    syms = sorted(s for s, r in eq.items() if r["series"] == "EQ")
    t2t = sorted(s for s, r in eq.items() if r["series"] in L.T2T_SERIES)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(syms) + "\n")
    print("NSE equity list: %s stocks in series EQ (intraday allowed), %s in BE/BZ (trade-for-trade, "
          "delivery only: left out)." % ("{:,}".format(len(syms)), "{:,}".format(len(t2t))))
    print("Wrote %s." % a.out)
    return 0


def read_symbols(a, eq):
    if a.symbols:
        return [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if a.universe and os.path.exists(a.universe):
        with open(a.universe, encoding="utf-8") as fh:
            return [ln.strip().upper() for ln in fh if ln.strip()]
    return sorted(s for s, r in eq.items() if r["series"] == "EQ")


def cmd_daily(a):
    now = L.parse_now(a.now)
    local = L.to_ist(now)
    end = local.date()
    if not (L.trading_day(end)[0] and local.time() >= datetime.time(18, 30)):
        end = L.prev_trading_day(end)          # NSE posts the day's bhavcopy in the evening
    eq = L.equity_list()
    symbols = read_symbols(a, eq)
    progress(a, "Reading %d sessions of NSE bhavcopy up to %s (cached after the first run)..." % (HISTORY, end))
    history = L.daily_history(HISTORY, end, lambda m: progress(a, m))
    if not history:
        raise ScanError("no NSE bhavcopy could be read up to %s" % end)
    hits, counts, young = scan_daily(history, symbols, a.min_price, a.min_turnover_cr)
    last_day = history[-1][0]
    doc = {"created_by": "india-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "as_of": "%s close" % last_day.isoformat(), "universe": len(symbols), "scanned": counts["scanned"],
           "source": "nse-bhavcopy", "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover_cr},
           "young": young, "note": NOTE, "hits": hits}
    if not a.no_write:
        L.write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Daily scan, closes through %s (NSE bhavcopy, %d sessions)." % (L.day_label(last_day), len(history)))
    print("%s stocks; %s passed the liquidity floor (₹%g+ and ₹%g crore+ turnover); %s scanned%s."
          % ("{:,}".format(len(symbols)), "{:,}".format(len(symbols) - counts["illiquid"] - counts["missing"]),
             a.min_price, a.min_turnover_cr, "{:,}".format(counts["scanned"]),
             (", %d too young (under %d sessions: /india-ipo recent covers them)" % (counts["short"], MIN_DAILY_BARS))
             if counts["short"] else ""))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched: %s. Most traded first:" % (
            len(hits), "" if len(hits) == 1 else "s", ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        for h in hits[:a.show]:
            print("  %-12s %12s  ₹%6.1f cr  RSI %5.1f  %s" % (h["symbol"], L.inr(h["price"]), h["turnover_cr"],
                                                           h["rsi"], ", ".join(LABEL[t] for t in h["setups"])))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No setups.")
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /india-news SYMBOL.")
    return 0 if hits else 1


def cmd_orb(a):
    now = L.parse_now(a.now)
    local = L.to_ist(now)
    day = local.date()
    cfg = L.load_config(a.config)
    if a.or_minutes < 5 or a.or_minutes % 5 or a.or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = L.trading_day(day)
    if not ok:
        raise ScanError("No NSE session on %s (%s). Run orb on a trading day after the opening range, or "
                        "replay a past day with --now \"YYYY-MM-DD 10:30\"." % (L.day_label(day), why))
    close_dt = datetime.datetime.combine(day, L.CLOSE)
    if a.at:
        cutoff = datetime.datetime.combine(day, L.parse_hhmm(a.at, "--at"))
        if not a.now and cutoff > local + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (a.at, L.hm(local.time())))
    else:
        cutoff = local.replace(second=0, microsecond=0)
    cutoff = min(cutoff, close_dt)
    eq = L.equity_list()
    if a.symbols:
        symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        last = L.daily_history(1, L.prev_trading_day(day))
        if not last:
            raise ScanError("no bhavcopy to rank stocks by turnover: pass --symbols")
        rows = last[-1][1]
        ranked = sorted((s for s, r in rows.items() if r["series"] == "EQ" and r["c"] >= a.min_price),
                        key=lambda s: -rows[s]["c"] * rows[s]["vol"])
        symbols = ranked[:a.top]
        # stocks listing today are not in yesterday's bhavcopy: add them
        symbols += [s for s, r in eq.items() if r["listed"] == day and r["series"] == "EQ" and s not in symbols]
    n_range = a.or_minutes // 5
    notes, hits, failed = [], [], {}
    counts = {"requested": len(symbols), "usable": 0, "listing_day": 0, "too_early": 0}
    progress(a, "Fetching today's 5-minute bars for %d stocks (%s)..." % (len(symbols), L.source_for(cfg)))
    for i, sym in enumerate(symbols, 1):
        listing = (eq.get(sym) or {}).get("listed") == day
        open_t = L.LISTING_OPEN if listing else L.OPEN
        open_dt = datetime.datetime.combine(day, open_t)
        first_close = open_dt + datetime.timedelta(minutes=a.or_minutes + 5)
        if cutoff < first_close:
            counts["too_early"] += 1
            continue
        try:
            raw, src = L.bars(sym, "5m", day, day, cfg, notes)
        except L.DataError as e:
            failed[sym] = str(e)
            continue
        bars = L.session_bars(raw, day, "5m", listing, cutoff)
        if not usable(bars, n_range, open_dt, cutoff):
            failed[sym] = "a missing 5-minute bar (halt, no trades, or the data is behind)"
            continue
        counts["usable"] += 1
        counts["listing_day"] += 1 if listing else 0
        h = orb_hit(bars, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"], "listing_day": listing}, **h))
        if i % 25 == 0:
            progress(a, "  %d of %d" % (i, len(symbols)))
    if counts["too_early"] == len(symbols):
        raise ScanError("It is %s IST: the %d-minute range runs 9:15 to %s and the first bar after it closes at "
                        "%s. Run orb again then." % (L.hm(cutoff.time()), a.or_minutes,
                                                     L.hm((datetime.datetime.combine(day, L.OPEN) + datetime.timedelta(minutes=a.or_minutes)).time()),
                                                     L.hm((datetime.datetime.combine(day, L.OPEN) + datetime.timedelta(minutes=a.or_minutes + 5)).time())))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    doc = {"created_by": "india-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "as_of": "%s %s IST" % (day.isoformat(), L.hm(cutoff.time())), "universe": len(symbols),
           "scanned": counts["usable"], "failed": len(failed), "source": L.source_for(cfg),
           "or_minutes": a.or_minutes, "interval": "5m", "notes": sorted(set(notes)), "note": NOTE, "hits": hits}
    if not a.no_write:
        L.write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, errors=failed), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s IST: %d-minute range from the open, 5-minute bars (%s)."
          % (L.day_label(day), L.hm(cutoff.time()), a.or_minutes, doc["source"]))
    print("%d of %d charts usable%s%s." % (counts["usable"], len(symbols),
                                          (" (%d failed or had a gap)" % len(failed)) if failed else "",
                                          (", %d listing today (range from 10:00)" % counts["listing_day"])
                                          if counts["listing_day"] else ""))
    for n in sorted(set(notes)):
        print("Note: " + n)
    if hits:
        print("%d closed above their opening-range high by %s:" % (len(hits), L.ampm(cutoff.time())))
        for h in hits[:a.show]:
            print("  %8s  %-12s %11s  range %s to %s  %+.2f%% over%s" % (
                h["at"], h["symbol"], L.inr(h["price"]), L.p2(h["or_low"]), L.p2(h["or_high"]), h["pct_over"],
                "  LISTING DAY" if h["listing_day"] else ""))
    else:
        print("No chart closed above its opening-range high by %s." % L.ampm(cutoff.time()))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: /india-news SYMBOL, then /india-entry SYMBOL.")
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE chart scanner (india-scanner)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("universe")
    p.add_argument("--out", default=L.path("universe.txt"))
    for name in ("daily", "orb"):
        p = sub.add_parser(name)
        p.add_argument("--symbols")
        p.add_argument("--universe", default=L.path("universe.txt"))
        p.add_argument("--min-price", type=float, default=50.0)
        p.add_argument("--out", default=L.path("watchlist.json"))
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--json", action="store_true")
        p.add_argument("--show", type=int, default=25)
        p.add_argument("--now", help="pretend it is this IST time, \"YYYY-MM-DD HH:MM\"")
        p.add_argument("--config", default=L.path("config.json"))
        if name == "daily":
            p.add_argument("--min-turnover-cr", type=float, default=10.0)
        else:
            p.add_argument("--at")
            p.add_argument("--or-minutes", type=int, default=15)
            p.add_argument("--top", type=int, default=150)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except (ScanError, L.DataError, ValueError) as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
