#!/usr/bin/env python3
"""Insider and big-money tracker for india-insider. Standard library only.

    python3 insider.py                               # watchlist + portfolio + plan: insider trades (30 days)
    python3 insider.py --symbols RELIANCE,JPL --days 90
    python3 insider.py deals                         # today's NSE bulk and block deals, every stock
    python3 insider.py deals --watch                 # only stocks you watch or hold
    python3 insider.py --promoters                   # promoters and directors only

insiders  SEBI's Prohibition of Insider Trading (PIT) disclosures, as NSE
          publishes them (nseindia.com/api/corporates-pit): who traded (name and
          category: promoter, promoter group, director, KMP, immediate relative,
          designated person), buy / sell / pledge, how many shares, the value,
          how (market, off market, ESOP, pledge, gift...), the dates and the
          holding before and after. One request per stock.
          Flagged: a promoter, director or KMP buying or selling in the market
          (Market Purchase / Market Sale), and pledges (a pledge by a promoter
          can mean borrowed money against the shares).

deals     NSE's bulk deals (a client trading more than 0.5% of a company's
          shares in a day) and block deals (single trades of Rs 10 crore+ in the
          block window), from the day's files on nsearchives.nseindia.com.
          Each row: the stock, the client, buy or sell, quantity and price.

These disclosures are public and filed after the fact (insider trades within
two trading days of the trade; bulk and block deals at the end of the day).
They are not inside information and not a signal to copy. New disclosures are
marked NEW until seen once (trader-in/state/insider.json).
"""

import argparse
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

BULK = L.NSE_ARCHIVE + "content/equities/bulk.csv"
BLOCK = L.NSE_ARCHIVE + "content/equities/block.csv"
KEY_PEOPLE = ("promoter", "director", "key managerial", "kmp")
MARKET_MODES = ("market purchase", "market sale")


class InsiderError(Exception):
    pass


def _n(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def crore(x):
    return "₹%s cr" % ("{:,.2f}".format(x / 1e7))


def pit(symbol, start, end):
    q = urllib.parse.urlencode({"index": "equities", "symbol": symbol, "from_date": start.strftime("%d-%m-%Y"),
                                "to_date": end.strftime("%d-%m-%Y")})
    doc = L.nse_json("corporates-pit?" + q)
    out = []
    for r in (doc or {}).get("data", []):
        cat = (r.get("personCategory") or "").strip()
        mode = (r.get("acqMode") or "").strip()
        kind = (r.get("tdpTransactionType") or "").strip()
        qty = _n(r.get("secAcq"))
        val = _n(r.get("secVal"))
        key = any(k in cat.lower() for k in KEY_PEOPLE)
        flag = []
        if key and mode.lower() in MARKET_MODES:
            flag.append("%s %s in the market" % (cat.lower(), "BOUGHT" if kind.lower() == "buy" else "SOLD"))
        km = (kind + " " + mode).lower()
        if "pledge" in km:
            released = any(w in km for w in ("revok", "revoc", "release", "invoc"))
            word = "pledge invoked (lender sold)" if "invoc" in km else "pledge released" if released else "pledge"
            flag.append(word + (" by a %s" % cat.lower() if key else ""))
        out.append({"id": str(r.get("pid") or r.get("did") or "%s-%s-%s" % (symbol, r.get("acqName"), r.get("date"))),
                    "symbol": symbol, "name": r.get("acqName"), "category": cat, "type": kind, "mode": mode,
                    "qty": int(qty), "value": val, "from": r.get("acqfromDt"), "to": r.get("acqtoDt"),
                    "filed": r.get("date"), "before_pct": r.get("befAcqSharesPer"),
                    "after_pct": r.get("afterAcqSharesPer"), "security": r.get("secType"), "key_person": key,
                    "flags": flag})
    out.sort(key=lambda x: _sort_date(x["filed"]), reverse=True)
    return out


def _sort_date(s):
    try:
        return datetime.datetime.strptime(s, "%d-%b-%Y %H:%M")
    except (TypeError, ValueError):
        return datetime.datetime(1970, 1, 1)


def deals_file(url, kind):
    text = L.http_get(url).decode("utf-8", "replace")
    rows = []
    for r in L._csv_rows(text):
        sym = (r.get("SYMBOL") or "").upper()
        if not sym or sym == "NO RECORDS" or (r.get("DATE") or "").upper() == "NO RECORDS":
            continue
        qty = _n(r.get("QUANTITY TRADED"))
        price = _n(r.get("TRADE PRICE / WGHT. AVG. PRICE"))
        rows.append({"kind": kind, "date": r.get("DATE"), "symbol": sym, "company": r.get("SECURITY NAME"),
                     "client": r.get("CLIENT NAME"), "side": (r.get("BUY/SELL") or "").upper(), "qty": int(qty),
                     "price": price, "value": qty * price})
    return rows


def cmd_insiders(a):
    now = L.parse_now(a.now)
    today = L.to_ist(now).date()
    symbols = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else L.watch_symbols()
    if not symbols:
        raise InsiderError("no symbols: pass --symbols, or run /india-scanner first")
    symbols = symbols[:a.max_symbols]
    state = L.read_json(a.state, default=None) or {"seen": []}
    seen = set(state.get("seen", []))
    allrows, errors = [], {}
    for s in symbols:
        try:
            allrows += pit(s, today - datetime.timedelta(days=a.days), today)
        except L.DataError as e:
            errors[s] = str(e)
        L.SLEEP(L.PAUSE)
    if a.promoters:
        allrows = [r for r in allrows if r["key_person"]]
    for r in allrows:
        r["new"] = r["id"] not in seen
    state["seen"] = sorted(seen | {r["id"] for r in allrows})[-5000:]
    L.write_json(a.state, state)
    if a.json:
        print(json.dumps({"insiders": allrows, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if allrows else 1
    print("Insider trades (SEBI PIT disclosures on NSE), last %d days, %d stock(s)." % (a.days, len(symbols)))
    by = {}
    for r in allrows:
        by.setdefault(r["symbol"], []).append(r)
    for s in symbols:
        rows = by.get(s, [])
        if not rows:
            print("\n%s: none filed" % s)
            continue
        flagged = [r for r in rows if r["flags"]]
        print("\n%s: %d disclosure(s)%s" % (s, len(rows), (", %d flagged" % len(flagged)) if flagged else ""))
        for r in rows[:a.show]:
            print("  %s %-17s %-28s %-22s %-6s %12s %14s  %s%s" % (
                "NEW" if r["new"] else "   ", (r["filed"] or "")[:17], (r["name"] or "")[:28],
                (r["category"] or "")[:22], (r["type"] or "")[:6], "{:,}".format(r["qty"]), crore(r["value"]),
                r["mode"], ("  <- " + "; ".join(r["flags"])) if r["flags"] else ""))
        if len(rows) > a.show:
            print("  ... and %d more (--show N)" % (len(rows) - a.show))
    for s, e in errors.items():
        print("Note: %s: %s" % (s, e))
    print("\nPublic filings, made after the trade. Not inside information, not a signal to copy.")
    return 0 if allrows else 1


def cmd_deals(a):
    rows, errors = [], []
    for url, kind in ((BULK, "bulk"), (BLOCK, "block")):
        try:
            rows += deals_file(url, kind)
        except L.DataError as e:
            errors.append("%s deals: %s" % (kind, e))
    if a.watch:
        want = set(L.watch_symbols())
        rows = [r for r in rows if r["symbol"] in want]
    rows.sort(key=lambda r: -r["value"])
    if a.json:
        print(json.dumps({"deals": rows, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    day = rows[0]["date"] if rows else "today"
    print("NSE bulk and block deals, %s%s: %d." % (day, " (your stocks)" if a.watch else "", len(rows)))
    net = {}
    for r in rows:
        net.setdefault(r["symbol"], 0.0)
        net[r["symbol"]] += r["value"] if r["side"] == "BUY" else -r["value"]
    for r in rows[:a.show]:
        print("  %-5s %-12s %-4s %-44s %12s @ %10s = %s" % (r["kind"], r["symbol"], r["side"], (r["client"] or "")[:44],
                                                           "{:,}".format(r["qty"]), L.p2(r["price"]), crore(r["value"])))
    if len(rows) > a.show:
        print("  ... and %d more (--show N)" % (len(rows) - a.show))
    big = sorted(net.items(), key=lambda x: -abs(x[1]))[:8]
    if big:
        print("Net by stock (buys minus sells): " + ", ".join("%s %s%s" % (s, "+" if v >= 0 else "-", crore(abs(v)))
                                                            for s, v in big))
    for e in errors:
        print("Note: " + e)
    print("A bulk deal is a client trading 0.5%+ of a company's shares in a day; a block deal is a single Rs 10 crore+ "
          "trade. Many are arbitrage or market makers buying and selling the same day.")
    return 0 if rows else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Insider trades and bulk / block deals on NSE (india-insider)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("deals")
    p.add_argument("--watch", action="store_true")
    for p in (ap, sub.choices["deals"]):
        p.add_argument("--json", action="store_true")
        p.add_argument("--show", type=int, default=25)
        p.add_argument("--now")
        p.add_argument("--state", default=L.path("state", "insider.json"))
    ap.add_argument("--symbols")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--promoters", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=25)
    a = ap.parse_args(argv)
    try:
        return cmd_deals(a) if a.cmd == "deals" else cmd_insiders(a)
    except (InsiderError, L.DataError, ValueError) as e:
        print("insider.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
