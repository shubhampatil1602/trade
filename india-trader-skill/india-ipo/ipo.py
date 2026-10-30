#!/usr/bin/env python3
"""New listings for india-ipo: the IPO calendar, recent listings and listing day. Standard library only.

    python3 ipo.py calendar                     # open, upcoming and about-to-list mainboard IPOs (NSE)
    python3 ipo.py recent                       # stocks listed in the last 60 sessions, with setups
    python3 ipo.py recent --days 20 --write     # also make them the watchlist for /india-entry
    python3 ipo.py today                        # stocks listing today and the listing-day clock
    python3 ipo.py today JPL --now "2026-10-28 10:20"

calendar  NSE's current issues (with subscription so far), upcoming issues and
          IPOs whose listing date is today or later. Mainboard (EQ) by default;
          --sme adds SME issues (NSE Emerge: lot-based, thin, not day-traded here).

recent    Every EQ / BE stock whose NSE listing date is within --days (60)
          sessions. Prices are NSE's bhavcopy, adjusted for splits and bonuses.
          For each: the issue price (NSE's past-issues list), the listing day's
          open, high, low and close, the last close, how far it is from the
          issue price and from the listing-day open. Setups that need only a
          few sessions (the 20-day scans need 30, so they skip these stocks):
            listing_high_breakout  the last close is above the listing day's high
                                   (from the 2nd session on)
            ipo_closing_high       the last close is above every close since listing
                                   (from the 3rd session on)
            issue_reclaim          closed under the issue price before, now back above it
            tight_base             5+ sessions, the last 5 closes inside an 8% range,
                                   above the issue price
            below_issue            the last close is under the issue price (a warning,
                                   not a setup)
          BE series (trade-for-trade) stocks are shown but marked: no intraday.

today     The listing-day clock. On its first day an IPO trades in a special
          pre-open session: order entry 9:00-9:45 (a call auction, no price
          band in it), order matching and buffer to 10:00, then normal trading
          from 10:00. The auction price is the day's open. After that NSE's
          normal band for the stock applies (often 20% for a mainboard IPO;
          smaller ones can have 5% and trade-for-trade). With bars (Angel One
          or Yahoo) it shows the open against the issue price (the listing gain),
          and the 15-minute opening range from 10:00 for /india-entry.

New listings move fast and have little history. Nothing here predicts a listing
gain. Exit code 0 when something was listed, 1 when nothing was.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

NOTE = "A new listing has little history: its moves are bigger and less predictable than an old stock's."
SETUP_LABEL = {
    "listing_high_breakout": "closed above the listing-day high",
    "ipo_closing_high": "closing high since listing",
    "issue_reclaim": "back above the issue price",
    "tight_base": "tight 5-day base above issue",
    "below_issue": "below the issue price",
}


class IPOError(Exception):
    pass


def progress(a, msg):
    if not getattr(a, "json", False):
        print(msg, file=sys.stderr)


def pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


def same_company(a, b):
    """NSE reuses symbols: the IPO record only counts when the first word of both names matches."""
    def first(x):
        w = [t for t in "".join(ch if ch.isalnum() else " " for ch in (x or "").lower()).split()
             if t not in ("the", "m", "s", "shri", "sri")]
        return w[0] if w else ""
    return bool(first(a)) and first(a) == first(b)


def new_listing_setups(rows, issue):
    """rows: adjusted daily bars since listing (oldest first, row 0 = listing day). -> tags."""
    tags = []
    n = len(rows)
    if n == 0:
        return tags
    closes = [r["c"] for r in rows]
    last = closes[-1]
    if n >= 2 and last > rows[0]["h"]:
        tags.append("listing_high_breakout")
    if n >= 3 and last > max(closes[:-1]):
        tags.append("ipo_closing_high")
    if issue:
        if n >= 2 and min(closes[:-1]) < issue <= last:
            tags.append("issue_reclaim")
        if n >= 5 and (max(closes[-5:]) - min(closes[-5:])) / last <= 0.08 and min(closes[-5:]) > issue:
            tags.append("tight_base")
        if last < issue:
            tags.append("below_issue")
    return tags


def cmd_calendar(a):
    now = L.parse_now(a.now)
    today = L.to_ist(now).date()
    cur = L.nse_json("ipo-current-issue", "ipo_current.json", 0.5)
    up = L.nse_json("all-upcoming-issues?category=ipo", "ipo_upcoming.json", 6)
    past = L.past_issues()
    keep = (lambda r: True) if a.sme else (lambda r: str(r.get("series", "EQ")).upper() != "SME")
    listing = sorted(((s, r) for s, r in past.items() if r["listing_date"] and r["listing_date"] >= today),
                     key=lambda x: x[1]["listing_date"])
    awaiting = sorted(((s, r) for s, r in past.items() if not r["listing_date"] and r.get("ipo_end")
                       and (today - r["ipo_end"]).days <= 10), key=lambda x: x[1]["ipo_end"])
    known = {s for s, _ in awaiting} | {s for s, _ in listing}
    for r in cur:   # closed today or yesterday: NSE keeps them on the current list for a day
        end = L._nse_date(r.get("issueEndDate"))
        if keep(r) and (str(r.get("status")).lower() == "closed" or (end and end < today)) \
                and str(r.get("symbol")).upper() not in known:
            awaiting.append((str(r.get("symbol")).upper(), {"company": r.get("companyName"), "ipo_end": end,
                                                             "price_range": r.get("issuePrice"),
                                                             "x": L._num(r.get("noOfTime"))}))
    open_now = [r for r in cur if keep(r) and str(r.get("status")).lower() == "active"]
    upcoming = [r for r in up if keep(r) and (L._nse_date(r.get("issueStartDate")) or today) > today]
    doc = {"created_by": "india-ipo", "as_of": L.to_ist(now).strftime("%Y-%m-%d %H:%M IST"),
           "open": open_now, "upcoming": upcoming,
           "listing": [dict(r, symbol=s, listing_date=r["listing_date"].isoformat(), ipo_end=None)
                       for s, r in listing],
           "awaiting_listing": [dict(r, symbol=s, ipo_end=r["ipo_end"].isoformat(), listing_date=None)
                                for s, r in awaiting]}
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False, default=str))
        return 0
    print("NSE IPO calendar, %s." % doc["as_of"])
    print("\nOpen now%s:" % ("" if a.sme else " (mainboard; --sme adds SME)"))
    for r in doc["open"]:
        x = L._num(r.get("noOfTime"))
        print("  %-12s %-44s %s to %s  %-22s %s" % (r.get("symbol"), (r.get("companyName") or "")[:44],
                                                     r.get("issueStartDate"), r.get("issueEndDate"),
                                                     r.get("issuePrice") or "-",
                                                     ("subscribed %.2fx so far" % x) if x is not None else ""))
    if not doc["open"]:
        print("  none")
    print("\nUpcoming:")
    for r in doc["upcoming"]:
        print("  %-12s %-44s %s to %s  %s" % (r.get("symbol"), (r.get("companyName") or "")[:44],
                                               r.get("issueStartDate"), r.get("issueEndDate"),
                                               r.get("issuePrice") or "-"))
    if not doc["upcoming"]:
        print("  none announced on NSE yet")
    print("\nClosed, waiting for allotment and listing:")
    for s, r in awaiting:
        print("  %-12s %-44s closed %s  %-22s %s" % (s, (r["company"] or "")[:44], r["ipo_end"],
                                                    r.get("price_range") or "",
                                                    ("subscribed %.2fx" % r["x"]) if r.get("x") is not None else ""))
    if not awaiting:
        print("  none")
    print("\nListing today or later:")
    for s, r in listing:
        print("  %-12s %-44s lists %s  issue price %s" % (s, (r["company"] or "")[:44], r["listing_date"],
                                                         L.inr(r["issue_price"]) if r["issue_price"] else "-"))
    if not listing:
        print("  none dated yet")
    print("\nOn listing day: special pre-open 9:00-9:45 (call auction), normal trading from 10:00. "
          "ipo.py today shows the clock.")
    return 0


def cmd_recent(a):
    now = L.parse_now(a.now)
    local = L.to_ist(now)
    today = local.date()
    end = today if (L.trading_day(today)[0] and local.time() >= datetime.time(18, 30)) else L.prev_trading_day(today)
    eq = L.equity_list()
    past = L.past_issues()
    oldest = end
    for _ in range(a.days - 1):
        oldest = L.prev_trading_day(oldest)
    young = sorted((s for s, r in eq.items() if r["listed"] and oldest <= r["listed"] <= today
                    and r["series"] in ("EQ", "BE", "BZ")), key=lambda s: eq[s]["listed"])
    if not young:
        print("No NSE listings in the last %d sessions." % a.days)
        return 1
    progress(a, "Reading %d sessions of NSE bhavcopy (cached after the first run)..." % (a.days + 1))
    history = L.daily_history(a.days + 1, end, lambda m: progress(a, m))
    out = []
    for s in young:
        r = eq[s]
        rec = past.get(s) or {}
        if rec and not same_company(r["name"], rec.get("company")):
            rec = {}
        if rec.get("listing_date") and abs((rec["listing_date"] - r["listed"]).days) > 7:
            rec = {}
        issue = rec.get("issue_price")
        rows, actions = L.adjusted_daily(history, s)
        rows = [x for x in rows if x["date"] >= r["listed"]]
        item = {"symbol": s, "name": r["name"], "series": r["series"], "listed": r["listed"].isoformat(),
                "sessions": len(rows), "issue_price": issue, "intraday": r["series"] == "EQ",
                "no_ipo_record": not rec}
        if r["listed"] > end:
            item["status"] = "lists %s" % r["listed"].isoformat()
        if rows:
            first, last = rows[0], rows[-1]
            item.update({"listing_open": round(first["o"], 2), "listing_high": round(first["h"], 2),
                         "listing_low": round(first["l"], 2), "listing_close": round(first["c"], 2),
                         "last_close": round(last["c"], 2), "last_date": last["date"].isoformat(),
                         "listing_gain_pct": pct(first["o"], issue), "vs_issue_pct": pct(last["c"], issue),
                         "vs_listing_open_pct": pct(last["c"], first["o"]),
                         "high_since": round(max(x["h"] for x in rows), 2),
                         "low_since": round(min(x["l"] for x in rows), 2),
                         "turnover_cr": round(last["c"] * last["vol"] / 1e7, 1),
                         "setups": new_listing_setups(rows, issue), "adjusted": actions or None})
        else:
            item["setups"] = []
        out.append(item)
    hits = [x for x in out if any(t != "below_issue" for t in x["setups"]) and x["intraday"]]
    doc = {"created_by": "india-ipo", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "recent",
           "as_of": "%s close" % end.isoformat(), "days": a.days, "listings": out, "note": NOTE}
    L.write_json(L.path("ipo_recent.json"), doc)
    if a.write:
        wl = {"created_by": "india-ipo", "created_at": doc["created_at"], "mode": "ipo", "as_of": doc["as_of"],
              "source": "nse-bhavcopy", "note": NOTE,
              "hits": [{"symbol": x["symbol"], "setups": x["setups"], "price": x.get("last_close"),
                        "listed": x["listed"], "issue_price": x["issue_price"], "listing_high": x.get("listing_high")}
                       for x in hits]}
        L.write_json(L.path("watchlist.json"), wl)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    print("NSE listings in the last %d sessions, closes through %s (bhavcopy, split-adjusted):" % (a.days, end))
    print("  %-12s %-10s %4s %10s %10s %8s %10s %8s  %s" % ("symbol", "listed", "days", "issue", "list open",
                                                           "gain", "last", "vs issue", "setups"))
    for x in sorted(out, key=lambda x: x["listed"], reverse=True):
        if "last_close" not in x:
            print("  %-12s %-10s %4s  %s" % (x["symbol"], x["listed"], 0, x.get("status") or "no bhavcopy yet"))
            continue
        flags = ", ".join(SETUP_LABEL[t] for t in x["setups"])
        if not x["intraday"]:
            flags = "BE trade-for-trade: delivery only, no intraday" + ("; " + flags if flags else "")
        if x["no_ipo_record"]:
            flags = "no NSE IPO record: relisting, SME migration or demerger" + ("; " + flags if flags else "")
        elif x["listing_gain_pct"] is not None and abs(x["listing_gain_pct"]) > 300:
            flags = "check the issue price (%s)" % L.p2(x["issue_price"]) + ("; " + flags if flags else "")
        print("  %-12s %-10s %4d %10s %10s %8s %10s %8s  %s" % (
            x["symbol"], x["listed"], x["sessions"], L.p2(x["issue_price"]) if x["issue_price"] else "-",
            L.p2(x["listing_open"]), ("%+.1f%%" % x["listing_gain_pct"]) if x["listing_gain_pct"] is not None else "-",
            L.p2(x["last_close"]), ("%+.1f%%" % x["vs_issue_pct"]) if x["vs_issue_pct"] is not None else "-", flags))
    print("%d of %d have a new-listing setup (intraday allowed)." % (len(hits), len(out)))
    if a.write:
        print("Wrote %s (mode ipo, %d hits) and %s." % (L.path("watchlist.json"), len(hits), L.path("ipo_recent.json")))
    else:
        print("Wrote %s. --write also makes the hits your watchlist." % L.path("ipo_recent.json"))
    print(NOTE + " Next: /india-news SYMBOL, then /india-entry SYMBOL (it knows the listing-day clock).")
    return 0


def cmd_today(a):
    now = L.parse_now(a.now)
    local = L.to_ist(now)
    day = local.date()
    cfg = L.load_config(a.config)
    eq = L.equity_list()
    past = L.past_issues()
    if a.symbol:
        syms = [a.symbol.upper()]
        if eq.get(syms[0], {}).get("listed") != day and (past.get(syms[0]) or {}).get("listing_date") != day:
            ld = L.listing_day_of(syms[0])
            print("%s does not list today (%s); its listing date is %s." % (syms[0], day, ld or "unknown"))
            return 1
    else:
        syms = sorted({s for s, r in eq.items() if r["listed"] == day} |
                      {s for s, r in past.items() if r["listing_date"] == day})
    ok, why = L.trading_day(day)
    if not ok:
        print("No NSE session today (%s)." % why)
        return 1
    if not syms:
        print("No NSE listing today (%s). ipo.py calendar shows what is coming." % day)
        return 1
    t = local.time()
    if t < L.PRE_OPEN:
        phase = "before the special pre-open: order entry opens at 9:00"
    elif t < datetime.time(9, 45):
        phase = "special pre-open: order entry until 9:45 (call auction, no price band, orders can be changed)"
    elif t < L.LISTING_OPEN:
        phase = "order matching and buffer: the auction price (the day's open) is set; normal trading at 10:00"
    elif t < datetime.time(10, 15):
        phase = "normal trading; the 15-minute opening range runs 10:00 to 10:15"
    elif t < L.CLOSE:
        phase = "normal trading; the opening range (10:00-10:15) is set"
    else:
        phase = "closed for the day"
    out = []
    print("Listing day, %s %s IST: %s." % (L.day_label(day), L.hm(t), phase))
    for s in syms:
        r = eq.get(s) or {}
        issue = (past.get(s) or {}).get("issue_price")
        item = {"symbol": s, "name": r.get("name") or (past.get(s) or {}).get("company"), "series": r.get("series"),
                "issue_price": issue}
        line = "  %-12s %s, issue price %s%s" % (s, item["name"] or "", L.inr(issue) if issue else "-",
                                                 "" if r.get("series", "EQ") == "EQ" else
                                                 "  [%s trade-for-trade: delivery only, NO intraday]" % r.get("series"))
        print(line)
        if t >= L.LISTING_OPEN:
            notes = []
            try:
                raw, src = L.bars(s, "5m", day, day, cfg, notes)
                bars = L.session_bars(raw, day, "5m", True, local.replace(second=0, microsecond=0))
            except L.DataError as e:
                bars, src = [], None
                print("    no bars yet: %s" % e)
            if bars:
                op = bars[0]["o"]
                item.update({"open": op, "listing_gain_pct": pct(op, issue), "last": bars[-1]["c"], "source": src})
                print("    opened %s%s; last %s (%s bar, %s)" % (
                    L.inr(op), (" = %+.2f%% on the issue price" % item["listing_gain_pct"]) if issue else "",
                    L.inr(bars[-1]["c"]), L.hm(bars[-1]["start"].time()), src))
                if len(bars) >= 3:
                    oh, ol = max(b["h"] for b in bars[:3]), min(b["l"] for b in bars[:3])
                    item["opening_range"] = {"high": oh, "low": ol, "minutes": 15}
                    print("    opening range 10:00-10:15: high %s, low %s" % (L.p2(oh), L.p2(ol)))
            for n in notes:
                print("    note: " + n)
        out.append(item)
    if a.json:
        print(json.dumps({"date": day.isoformat(), "phase": phase, "listings": out}, indent=2, ensure_ascii=False))
    print("Next: /india-entry SYMBOL watches the 10:00-10:15 range (it shifts the clock for a listing day "
          "itself). " + NOTE)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE new listings (india-ipo)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("calendar")
    p.add_argument("--sme", action="store_true")
    p = sub.add_parser("recent")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--write", action="store_true", help="also make the hits trader-in/watchlist.json")
    p = sub.add_parser("today")
    p.add_argument("symbol", nargs="?")
    p.add_argument("--config", default=L.path("config.json"))
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true")
        p.add_argument("--now", help="pretend it is this IST time, \"YYYY-MM-DD HH:MM\"")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"calendar": cmd_calendar, "recent": cmd_recent, "today": cmd_today}[a.cmd](a)
    except (IPOError, L.DataError, ValueError) as e:
        print("ipo.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
