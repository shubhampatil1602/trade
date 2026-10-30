#!/usr/bin/env python3
"""News Monitor for india-news: NSE corporate announcements for the stocks you watch. Standard library only.

    python3 news.py                                # watchlist + portfolio + plan, last 24 hours, new items only
    python3 news.py --symbols RELIANCE,TCS,JPL --hours 72
    python3 news.py --all                          # also items you have already seen
    python3 news.py --market --important           # every NSE stock, important tags only

What it reads: NSE's corporate announcements feed (nseindia.com/api/
corporate-announcements), the filings companies make under SEBI's LODR rules:
results, board meetings, orders and contracts, acquisitions, fund raises,
pledges, management changes, credit ratings, clarifications on price moves.
Each item has the company's own subject line and a link to the PDF.

It tags every item (results, board-meeting, order-win, deal, fund-raise,
dividend, split-bonus, pledge, management, rating, regulatory, insider,
clarification, listing, routine) and marks the ones that move prices as
important. It shows only what is new since the last run (trader-in/state/
news.json remembers 30 days), newest first, in IST. With --moves (on by
default when there is a watch list) it also shows how far each watched stock
is from yesterday's close and flags 3%+ moves.

NSE announcements are the company's own words, not analysis. A filing can
move a stock before you read it.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

KEEP_DAYS = 30
MOVE_PCT = 3.0
TAGS = [  # (tag, important, pattern over category + subject, boilerplate removed), first match wins
    ("routine", False, r"trading window|newspaper|paper cutting|publication|certificate|74\(5\)|compliance|"
                       r"\bagm\b|annual report|postal ballot|e-voting|loss of share|duplicate share|transcript|"
                       r"audio recording|investor meet|investor presentation|analyst|conference|\besop|\besos|"
                       r"employee stock|codes? under|code of (?:conduct|practices)|"
                       r"change in (?:registered office|rta|address)"),
    ("regulatory", True, r"\bpenalty|show cause|\bsearch(?:es)?\b|\braids?\b|tax (?:order|demand)|income tax|\bgst\b|"
                         r"demand notice|enforcement|tribunal|\bnclt\b|insolvency|\bdefault|litigation|\bcourt\b|"
                         r"\bfraud|sebi order|adjudication|usfda|warning letter|\binspection|orders? passed|"
                         r"action\(s\) taken"),
    ("clarification", True, r"clarification|price movement|movement in price|spurt in volume"),
    ("results", True, r"financial result|quarterly result|audited|unaudited|\bresults?\b|earnings"),
    ("order-win", True, r"bagging|receiving of orders|work order|purchase order|letter of award|\bloa\b|"
                        r"\bcontracts?\b|\borders?\b"),
    ("deal", True, r"acquisition|amalgamation|merger|scheme of arrangement|demerger|takeover|open offer|"
                   r"joint venture|stake|divest|slump sale|strike off"),
    ("fund-raise", True, r"\bqip\b|qualified institutions|preferential|rights issue|fund raising|raising of funds|"
                         r"warrants|\bncds?\b|debentures"),
    ("split-bonus", True, r"bonus|split|sub-division|buyback|buy back|buy-back"),
    ("dividend", False, r"dividend|record date"),
    ("pledge", True, r"pledge|encumbrance"),
    ("management", True, r"resignation|cessation|appointment|change in (?:ceo|md|cfo|management|director)|"
                         r"key managerial"),
    ("rating", True, r"credit rating|\brating"),
    ("insider", False, r"insider|sast|regulation 7\b|regulation 29|regulation 31"),
    ("board-meeting", False, r"board meeting|outcome of board|meeting of the board"),
    ("listing", False, r"relisting|trading approval|listing approval|commencement of trading"),
]
BOILERPLATE = re.compile(r"(?:securities and exchange board of india|sebi)\s*\(\s*listing obligations and disclosure "
                         r"requirements\s*\)\s*regulations,?\s*\d{4}|listing obligations and disclosure requirements|"
                         r"regulation\s*30(?:\(\d+\))?|\blodr\b|has informed the exchange (?:about|regarding)|"
                         r"disclosure under", re.I)


class NewsError(Exception):
    pass


def tag(item):
    text = BOILERPLATE.sub(" ", "%s %s" % (item.get("category") or "", item.get("subject") or "")).lower()
    for name, important, pat in TAGS:
        if re.search(pat, text):
            return name, important
    return "other", False


def fetch_day_range(start, end, symbol=None):
    q = {"index": "equities", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}
    if symbol:
        q["symbol"] = symbol
    rows = L.nse_json("corporate-announcements?" + urllib.parse.urlencode(q))
    return rows if isinstance(rows, list) else []


def parse(rows):
    out = []
    for r in rows:
        try:
            t = datetime.datetime.strptime(r.get("an_dt") or r.get("sort_date"), "%d-%b-%Y %H:%M:%S")
        except (TypeError, ValueError):
            try:
                t = datetime.datetime.strptime(r.get("sort_date"), "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
        item = {"id": str(r.get("seq_id") or "%s-%s" % (r.get("symbol"), r.get("dt"))),
                "symbol": str(r.get("symbol") or "").upper(), "company": r.get("sm_name"),
                "time": t.strftime("%Y-%m-%d %H:%M"), "category": (r.get("desc") or "").strip(),
                "subject": re.sub(r"\s+", " ", (r.get("attchmntText") or "").strip()),
                "pdf": r.get("attchmntFile")}
        item["tag"], item["important"] = tag(item)
        out.append(item)
    return out


def collect(symbols, start, end, market=False, progress=None):
    items, errors = [], {}
    if market or len(symbols) > 8:
        rows = fetch_day_range(start, end)
        items = parse(rows)
        if not market:
            want = set(symbols)
            items = [i for i in items if i["symbol"] in want]
    else:
        for s in symbols:
            try:
                items += parse(fetch_day_range(start, end, s))
            except L.DataError as e:
                errors[s] = str(e)
            L.SLEEP(L.PAUSE)
    seen, out = set(), []
    for i in sorted(items, key=lambda x: x["time"], reverse=True):
        if i["id"] not in seen:
            seen.add(i["id"])
            out.append(i)
    return out, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE corporate announcements for your stocks (india-news)")
    ap.add_argument("--symbols")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--all", action="store_true", help="also items already seen")
    ap.add_argument("--market", action="store_true", help="every NSE stock, not just your watch list")
    ap.add_argument("--important", action="store_true", help="important tags only")
    ap.add_argument("--no-moves", action="store_true")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--state", default=L.path("state", "news.json"))
    ap.add_argument("--config", default=L.path("config.json"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    try:
        now = L.parse_now(a.now)
        local = L.to_ist(now)
        symbols = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else L.watch_symbols()
        if not symbols and not a.market:
            raise NewsError("no symbols: pass --symbols, run /india-scanner first, or use --market")
        since = local - datetime.timedelta(hours=a.hours)
        items, errors = collect(symbols, since.date(), local.date(), a.market)
        items = [i for i in items if since.strftime("%Y-%m-%d %H:%M") <= i["time"] <= local.strftime("%Y-%m-%d %H:%M")]
        if a.important:
            items = [i for i in items if i["important"]]
        state = L.read_json(a.state, default=None) or {"seen": {}}
        fresh = [i for i in items if a.all or i["id"] not in state["seen"]]
        cutoff = (local - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
        for i in items:
            state["seen"][i["id"]] = i["time"][:10]
        L.write_json(a.state, state)
        moves = {}
        if symbols and not a.no_moves and not a.market:
            notes = []
            for s, q in L.quotes(symbols[:25], L.load_config(a.config), notes).items():
                if q.get("price") and q.get("prev_close"):
                    moves[s] = round(100 * (q["price"] / q["prev_close"] - 1), 2)
        if a.json:
            print(json.dumps({"items": fresh[:a.max], "moves": moves, "errors": errors}, indent=2, ensure_ascii=False))
            return 0 if fresh else 1
        scope = "every NSE stock" if a.market else "%d stock(s): %s" % (len(symbols), ", ".join(symbols[:12]) +
                                                                       (" ..." if len(symbols) > 12 else ""))
        print("NSE announcements, last %g hours to %s IST, %s." % (a.hours, local.strftime("%a %H:%M"), scope))
        if moves:
            print("From yesterday's close: " + ", ".join("%s %+.2f%%%s" % (s, m, " MOVING" if abs(m) >= MOVE_PCT else "")
                                                         for s, m in sorted(moves.items(), key=lambda x: -abs(x[1]))))
        if not fresh:
            print("Nothing new%s." % ("" if a.all else " since the last check (--all shows seen items)"))
        for i in fresh[:a.max]:
            print("%s  %-12s %s%-13s %s" % (i["time"][5:], i["symbol"], "* " if i["important"] else "  ", i["tag"],
                                           (i["subject"] or i["category"])[:150]))
            if i["important"] and i.get("pdf"):
                print("%s%s" % (" " * 33, i["pdf"]))
        if len(fresh) > a.max:
            print("... and %d more (--max N)." % (len(fresh) - a.max))
        for s, e in errors.items():
            print("Note: %s: %s" % (s, e))
        print("* = the kind of filing that moves prices. The company's own words, not analysis. "
              "Next: /india-entry SYMBOL, or check results with the PDF.")
        return 0 if fresh else 1
    except (NewsError, L.DataError, ValueError) as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
