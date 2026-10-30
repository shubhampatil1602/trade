"""Shared library for the India day-trader skills (NSE). Standard library only, Python 3.8+.

Every india-* skill imports this file from ../india-lib. It holds the things
that are the same everywhere: the IST clock and the NSE calendar, NSE's public
files (the equity list, the price bands, the daily bhavcopy, the IPO lists),
Angel One SmartAPI (candles and quotes, with your own credentials), the Yahoo
.NS fallback, tick sizes, intraday charges and the trader-in/ files.

Time. India has no daylight saving: IST is UTC+5:30 all year. Internally a
"utc" is a naive datetime in UTC and an "ist" is a naive datetime in IST.

The NSE equity session:
    09:00-09:08  pre-open order entry (call auction), 09:08-09:15 matching
    09:15-15:30  normal market
    Listing day of an IPO or a relisted stock: a special pre-open session,
    order entry 09:00-09:45, then normal trading from 10:00. So on that day
    the first 5-minute bar is the 10:00 bar, not the 09:15 one.

Data sources, chosen per call by "data_source" in trader-in/config.json:
    auto   Angel One SmartAPI when the four ANGEL_* environment variables are
           set, otherwise Yahoo (SYMBOL.NS). The default.
    angel  SmartAPI only (needs the credentials).
    yahoo  Yahoo only: free, unofficial, can lag or rate-limit.
NSE's own files (equity list, bhavcopy, bands, IPOs, announcements, deals)
come from nseindia.com / nsearchives.nseindia.com with an honest user agent
and a polite pause; they are cached under trader-in/cache/.

Angel One credentials live in environment variables only, never in a file
this pack writes and never in the chat:
    ANGEL_API_KEY      the SmartAPI app's API key
    ANGEL_CLIENT_CODE  your Angel One client code
    ANGEL_PIN          your 4-digit login PIN
    ANGEL_TOTP_SECRET  the TOTP secret shown when you enabled TOTP (base32)
The day's session token is cached in trader-in/state/angel_session.json
(owner-only permissions) so a scan does not log in once per stock.
"""

import base64
import csv
import datetime
import hashlib
import hmac
import io
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

# Windows writes piped output (how Claude Code reads it) in the ANSI code page, which has no ₹: print UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and (_stream.encoding or "").lower().replace("-", "") != "utf8":
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

UA ="india-trader-skill/1.0 (personal trading tool; Python urllib)"
HOME = "trader-in"
PAUSE = 0.35          # seconds between requests to a free endpoint
BACKOFF = 5.0         # seconds before the one retry after a 429 or a 5xx
SLEEP = time.sleep    # tests swap this out

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
PRE_OPEN = datetime.time(9, 0)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
LISTING_OPEN = datetime.time(10, 0)
BAR_MIN = 5
SESSION_BARS = 75     # 09:15 to 15:30 in 5-minute bars
EPOCH = datetime.datetime(1970, 1, 1)

# NSE capital-market trading holidays, from nseindia.com/api/holiday-master?type=trading
# (read 2026-10-30). Weekend dates in NSE's list are kept: they change nothing.
# "holidays refresh" (alerts.py clock --refresh) adds later years to the cache.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading in the evening; not modelled)",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
EMBEDDED_YEARS = (2026,)

NSE = "https://www.nseindia.com/api/"
NSE_ARCHIVE = "https://nsearchives.nseindia.com/"
EQUITY_L = NSE_ARCHIVE + "content/equities/EQUITY_L.csv"
NIFTY500 = NSE_ARCHIVE + "content/indices/ind_nifty500list.csv"
SEC_LIST = NSE_ARCHIVE + "content/equities/sec_list.csv"
BHAV = NSE_ARCHIVE + "products/content/sec_bhavdata_full_%s.csv"     # DDMMYYYY
SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
ANGEL_ROOT = "https://apiconnect.angelone.in"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")

T2T_SERIES = {"BE", "BZ"}          # trade-for-trade: every trade is delivered, no intraday
INTRADAY_SERIES = {"EQ"}

# NSE cash-market tick sizes by price (circulars of May 2024 and CMTR/67133 of
# 13 Mar 2025: under Rs 250 the tick is Re 0.01 from 15 Apr 2025). NSE reviews
# them monthly from the previous month's close, so the per-stock tick from
# Angel One's scrip master wins whenever it is cached.
TICK_TABLE = ((250, "0.01"), (1000, "0.05"), (5000, "0.10"), (10000, "0.50"), (20000, "1.00"), (None, "5.00"))

# Intraday equity charges. Statutory rates as of 2026-10; brokerage presets are
# each broker's published intraday plan. Check your contract note and override
# any of these under "charges" in trader-in/config.json.
STATUTORY = {
    "stt_sell_pct": 0.025,          # STT, intraday: 0.025% on the sell side
    "exchange_pct": 0.00297,        # NSE transaction charge, both sides
    "sebi_pct": 0.0001,             # SEBI fee Rs 10 per crore, both sides
    "stamp_buy_pct": 0.003,         # stamp duty, intraday: 0.003% on the buy side
    "gst_pct": 18.0,                # GST on brokerage + exchange + SEBI fee
}
BROKERS = {
    "angelone": {"name": "Angel One", "brokerage_flat": 20.0, "brokerage_pct": 0.03, "brokerage_min": 0.0,
                 "square_off": "15:15"},
    "groww": {"name": "Groww", "brokerage_flat": 20.0, "brokerage_pct": 0.1, "brokerage_min": 5.0,
              "square_off": "15:10"},
    "zerodha": {"name": "Zerodha", "brokerage_flat": 20.0, "brokerage_pct": 0.03, "brokerage_min": 0.0,
                "square_off": "15:20"},
}

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 40000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:05",
    "broker": "angelone",
    "product": "MIS",
    "mis_leverage": 1,
    "include_charges": True,
    "charges": {},
    "blocked": [],
    "allow_t2t": False,
    "allow_short": False,
    "data_source": "auto",
}


class DataError(Exception):
    """A plain-words problem with a data source."""


# ---------------------------------------------------------------- clock

def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def to_ist(utc):
    return utc + IST_OFFSET


def ist_to_utc(ist):
    return ist - IST_OFFSET


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc - EPOCH).total_seconds())


def ist_of_ts(ts):
    return to_ist(utc_of(ts))


def parse_now(text):
    """None -> the real clock. "YYYY-MM-DD HH:MM[:SS]" is IST; a trailing Z means UTC. Returns naive UTC."""
    if not text:
        return utc_now()
    t = str(text).strip().replace("T", " ")
    is_utc = t.endswith("Z")
    t = t.rstrip("Z").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(t, fmt)
            return dt if is_utc else ist_to_utc(dt)
        except ValueError:
            pass
    raise ValueError("--now wants \"YYYY-MM-DD HH:MM\" (IST), got %r" % text)


def parse_hhmm(text, what="time"):
    try:
        h, m = str(text).strip().split(":")
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError):
        raise ValueError("%s wants HH:MM (24-hour IST), got %r" % (what, text))


def hm(t):
    return t.strftime("%H:%M")


def ampm(t):
    """9:15 AM, 3:05 PM."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return d.strftime("%a %Y-%m-%d")


def holidays():
    """{iso date: name}: the embedded list plus any refreshed years in the cache."""
    out = dict(NSE_HOLIDAYS)
    cached = read_json(cache_path("nse_holidays.json"), default=None)
    if isinstance(cached, dict):
        out.update(cached.get("holidays") or {})
    return out


def known_years():
    years = set(EMBEDDED_YEARS)
    cached = read_json(cache_path("nse_holidays.json"), default=None)
    if isinstance(cached, dict):
        years.update(int(y) for y in cached.get("years") or [])
    return years


def refresh_holidays():
    """Fetch NSE's holiday master and cache it. Returns the number of holidays."""
    doc = json.loads(http_get(NSE + "holiday-master?type=trading").decode("utf-8"))
    rows = doc.get("CM") or []
    out = {}
    for r in rows:
        try:
            d = datetime.datetime.strptime(r["tradingDate"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        out[d.isoformat()] = r.get("description") or "holiday"
    years = sorted({int(k[:4]) for k in out})
    write_json(cache_path("nse_holidays.json"), {"fetched_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                  "years": years, "holidays": out})
    return len(out)


def trading_day(d):
    """(True, "") or (False, why)."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = holidays().get(d.isoformat())
    if name:
        return False, "NSE holiday: %s" % name
    return True, ""


def calendar_note(d):
    """A warning when the holiday list does not cover d's year, else ''."""
    if d.year in known_years():
        return ""
    return ("the NSE holiday list here covers %s only: check nseindia.com for %s (alerts.py clock --refresh "
            "updates it)" % (", ".join(map(str, sorted(known_years()))), d.year))


def session(d, listing=False):
    """(open, close) times of the normal market on d, or None when closed.
    listing=True: the stock's listing day, trading starts at 10:00."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return (LISTING_OPEN if listing else OPEN), CLOSE


def next_trading_day(d):
    d += datetime.timedelta(days=1)
    for _ in range(30):
        if trading_day(d)[0]:
            return d
        d += datetime.timedelta(days=1)
    return d


def prev_trading_day(d):
    d -= datetime.timedelta(days=1)
    for _ in range(30):
        if trading_day(d)[0]:
            return d
        d -= datetime.timedelta(days=1)
    return d


def trading_days_between(a, b):
    """Trading days in (a, b]: 0 when b is a, 1 when b is the next session."""
    n, d = 0, a
    while d < b:
        d += datetime.timedelta(days=1)
        if trading_day(d)[0]:
            n += 1
    return n


def market_status(now_utc):
    """{date, now_ist, open, phase, opens, closes, next_open, why} for the NSE equity market."""
    local = to_ist(now_utc)
    d, t = local.date(), local.time()
    ok, why = trading_day(d)
    out = {"date": d.isoformat(), "now_ist": local.strftime("%Y-%m-%d %H:%M"), "open": False, "phase": "closed",
           "opens": hm(OPEN), "closes": hm(CLOSE), "why": why, "calendar_note": calendar_note(d)}
    if not ok:
        out["next_open"] = "%s %s" % (next_trading_day(d).isoformat(), hm(OPEN))
        out["opens"] = out["closes"] = None
        return out
    if t < PRE_OPEN:
        out["phase"] = "before pre-open"
    elif t < OPEN:
        out["phase"] = "pre-open"
    elif t < CLOSE:
        out["phase"] = "open"
        out["open"] = True
    else:
        out["phase"] = "closed"
    if t >= CLOSE:
        out["next_open"] = "%s %s" % (next_trading_day(d).isoformat(), hm(OPEN))
    elif not out["open"]:
        out["next_open"] = "%s %s" % (d.isoformat(), hm(OPEN))
    return out


# ---------------------------------------------------------------- files

def path(*parts):
    return os.path.join(HOME, *parts)


def cache_path(name):
    return os.path.join(HOME, "cache", name)


def read_json(p, what=None, default=Exception):
    if not p or not os.path.exists(p):
        if default is Exception:
            raise DataError("no %s at %s" % (what or "file", p))
        return default
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        if default is Exception:
            raise DataError("%s is not valid JSON: %s" % (p, e))
        return default


def write_json(p, doc, private=False):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def load_config(p=None):
    """DEFAULTS overlaid with trader-in/config.json (if any)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    doc = read_json(p or path("config.json"), default=None)
    if isinstance(doc, dict):
        cfg.update({k: v for k, v in doc.items() if v is not None})
    return cfg


def watch_symbols(watchlist=None, portfolio=None, plan=None):
    """Symbols from the watchlist hits, the portfolio and the plan, deduped, in that order.
    watchlist=False leaves the watchlist out (holdings and plan only)."""
    out = []
    w = None if watchlist is False else read_json(watchlist or path("watchlist.json"), default=None)
    for h in (w or {}).get("hits", []) if isinstance(w, dict) else []:
        out.append(str(h.get("symbol", "")).upper())
    pf = read_json(portfolio or path("portfolio.json"), default=None)
    for p in (pf or {}).get("positions", []) if isinstance(pf, dict) else []:
        out.append(str(p.get("symbol") or p.get("ticker") or "").upper())
    pl = read_json(plan or path("plan.json"), default=None)
    if isinstance(pl, dict) and pl.get("symbol"):
        out.append(str(pl["symbol"]).upper())
    seen, res = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res


# ---------------------------------------------------------------- money and ticks

def D(x):
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x).strip().replace(",", "").lstrip("₹").replace("Rs.", "").strip())


def inr(x, paise=True):
    """Indian digit grouping: ₹1,23,456.78."""
    v = D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = v < 0
    s = format(abs(v), "f")
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = "₹" + whole + (("." + frac) if paise else "")
    return ("-" + out) if neg else out


def p2(x):
    """A price for people: 2 decimals, Indian grouping, no rupee sign."""
    return inr(x)[1:]


def tick_from_table(price):
    p = D(price)
    for upper, tick in TICK_TABLE:
        if upper is None or p < upper:
            return D(tick)
    return D("0.05")


def tick_for(price, symbol=None):
    """The stock's tick: Angel One's scrip master (cached) if it has it, else NSE's price table."""
    if symbol:
        info = scrip_info(symbol, fetch=False)
        if info and info.get("tick"):
            return D(info["tick"])
    return tick_from_table(price)


def to_tick(x, tick, mode="nearest"):
    """Round a price onto the tick grid: nearest (half up), down or up."""
    x, tick = D(x), D(tick)
    n = x / tick
    rounding = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
    return (n.quantize(Decimal("1"), rounding=rounding) * tick).quantize(Decimal("0.01"))


def on_tick(x, tick):
    x, tick = D(x), D(tick)
    return (x / tick) == (x / tick).to_integral_value()


def charges(buy_value, sell_value, cfg=None):
    """Intraday equity charges for one round trip (one buy order, one sell order) in rupees.
    Returns {"brokerage", "stt", "exchange", "sebi", "stamp", "gst", "total"}."""
    cfg = cfg or {}
    b = dict(BROKERS.get(str(cfg.get("broker", "angelone")).lower(), BROKERS["angelone"]))
    rates = dict(STATUTORY)
    over = cfg.get("charges") or {}
    b.update({k: v for k, v in over.items() if k.startswith("brokerage")})
    rates.update({k: v for k, v in over.items() if k in STATUTORY})

    def brok(v):
        if v <= 0:
            return 0.0
        x = min(float(b["brokerage_flat"]), v * float(b["brokerage_pct"]) / 100.0)
        return max(x, float(b.get("brokerage_min") or 0))
    bv, sv = float(buy_value), float(sell_value)
    out = {"brokerage": brok(bv) + brok(sv),
           "stt": sv * rates["stt_sell_pct"] / 100.0,
           "exchange": (bv + sv) * rates["exchange_pct"] / 100.0,
           "sebi": (bv + sv) * rates["sebi_pct"] / 100.0,
           "stamp": bv * rates["stamp_buy_pct"] / 100.0}
    out["gst"] = (out["brokerage"] + out["exchange"] + out["sebi"]) * rates["gst_pct"] / 100.0
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out


# ---------------------------------------------------------------- http

def http_get(url, timeout=30, headers=None, data=None, method=None, retries=1):
    """Bytes of the response. One retry after BACKOFF on a 429 or a 5xx. Raises DataError."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                SLEEP(BACKOFF)
                continue
            raise DataError("HTTP %d from %s" % (e.code, urllib.parse.urlsplit(url).netloc))
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            if attempt < retries:
                SLEEP(BACKOFF)
                continue
            raise DataError("could not reach %s (%s)" % (urllib.parse.urlsplit(url).netloc,
                                                         getattr(e, "reason", e)))
    raise DataError("no response from %s" % url)


def fetch_cached(url, name, max_age_hours=20.0, binary=False):
    """The cached copy if it is fresh, else download it. Falls back to a stale copy on a network error."""
    p = cache_path(name)
    fresh = os.path.exists(p) and (time.time() - os.path.getmtime(p)) < max_age_hours * 3600
    if not fresh:
        try:
            blob = http_get(url)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + ".tmp", "wb") as fh:
                fh.write(blob)
            os.replace(p + ".tmp", p)
        except DataError:
            if not os.path.exists(p):
                raise
    with open(p, "rb") as fh:
        blob = fh.read()
    return blob if binary else blob.decode("utf-8", "replace")


def _csv_rows(text):
    rd = csv.reader(io.StringIO(text))
    head = [h.strip().upper() for h in next(rd, [])]
    for row in rd:
        if row and any(c.strip() for c in row):
            yield dict(zip(head, [c.strip() for c in row]))


def _nse_date(s):
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- NSE files

def equity_list():
    """{SYMBOL: {"name", "series", "listed" (date), "isin"}} from NSE's EQUITY_L.csv (all series)."""
    out = {}
    for r in _csv_rows(fetch_cached(EQUITY_L, "EQUITY_L.csv")):
        sym = r.get("SYMBOL", "").upper()
        if not sym:
            continue
        out[sym] = {"name": r.get("NAME OF COMPANY", ""), "series": r.get("SERIES", ""),
                    "listed": _nse_date(r.get("DATE OF LISTING")), "isin": r.get("ISIN NUMBER", "")}
    return out


def nifty500():
    return [r["SYMBOL"].upper() for r in _csv_rows(fetch_cached(NIFTY500, "nifty500.csv", 24 * 7)) if r.get("SYMBOL")]


def price_bands():
    """{SYMBOL: {"series", "band" (percent, None = no band: F&O stocks)}} from NSE's sec_list.csv."""
    out = {}
    for r in _csv_rows(fetch_cached(SEC_LIST, "sec_list.csv")):
        sym = r.get("SYMBOL", "").upper()
        band = r.get("BAND", "")
        try:
            b = float(band)
        except ValueError:
            b = None
        out[sym] = {"series": r.get("SERIES", ""), "band": b}
    return out


def stock_info(symbol):
    """What NSE says about one stock: series, listing date, band. {} when NSE does not list it."""
    sym = symbol.upper()
    eq = equity_list().get(sym, {})
    band = price_bands().get(sym, {})
    if not eq and not band:
        return {}
    return {"symbol": sym, "name": eq.get("name"), "series": eq.get("series") or band.get("series"),
            "listed": eq.get("listed"), "band": band.get("band"), "band_known": bool(band)}


def bhavcopy(day, fetch=True):
    """{SYMBOL: {"series", "prev", "o", "h", "l", "c", "vol", "deliv_pct"}} for one session, or None
    (holiday, not published yet). Cached per day; series EQ, BE, BZ, SM, ST only."""
    name = "bhav/%s.csv" % day.isoformat()
    p = cache_path(name)
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        if not fetch:
            return None
        try:
            text = http_get(BHAV % day.strftime("%d%m%Y"), retries=0).decode("utf-8", "replace")
        except DataError as e:
            if "HTTP 404" in str(e):
                return None
            raise
        if not text.upper().startswith("SYMBOL"):
            return None
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        SLEEP(PAUSE)
    out = {}
    for r in _csv_rows(text):
        series = r.get("SERIES", "")
        if series not in ("EQ", "BE", "BZ", "SM", "ST"):
            continue
        try:
            out[r["SYMBOL"].upper()] = {
                "series": series, "prev": float(r["PREV_CLOSE"]), "o": float(r["OPEN_PRICE"]),
                "h": float(r["HIGH_PRICE"]), "l": float(r["LOW_PRICE"]), "c": float(r["CLOSE_PRICE"]),
                "vol": int(float(r["TTL_TRD_QNTY"])),
                "deliv_pct": float(r["DELIV_PER"]) if r.get("DELIV_PER", "-") not in ("-", "") else None}
        except (KeyError, ValueError):
            continue
    return out


def daily_history(sessions, end_day, progress=None):
    """The last `sessions` bhavcopies up to end_day: [(date, rows)], oldest first."""
    out, d, misses = [], end_day, 0
    while len(out) < sessions and misses < 15:
        if trading_day(d)[0]:
            rows = bhavcopy(d)
            if rows:
                out.append((d, rows))
                misses = 0
                if progress and len(out) % 10 == 0:
                    progress("  %d of %d daily files" % (len(out), sessions))
            else:
                misses += 1
        d -= datetime.timedelta(days=1)
    out.reverse()
    return out


def adjusted_daily(history, symbol):
    """[{date, o, h, l, c, vol, series}] of one stock from bhavcopy history, oldest first, adjusted for
    splits and bonuses: on an ex-date NSE's PREV_CLOSE is the adjusted close, so when it differs from the
    stored close of the day before, every earlier bar is scaled by prev / close. Returns (rows, actions)."""
    rows, actions = [], []
    for d, day_rows in history:
        r = day_rows.get(symbol)
        if not r:
            continue
        if rows and rows[-1]["c"] > 0 and r["prev"] > 0:
            f = r["prev"] / rows[-1]["c"]
            if abs(f - 1) > 0.002:
                for x in rows:
                    for k in ("o", "h", "l", "c"):
                        x[k] *= f
                    x["vol"] = int(x["vol"] / f) if f else x["vol"]
                actions.append({"date": d.isoformat(), "factor": round(f, 4)})
        rows.append({"date": d, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "vol": r["vol"],
                     "series": r["series"], "deliv_pct": r.get("deliv_pct")})
    return rows, actions


def nse_json(endpoint, name=None, max_age_hours=1.0):
    """GET nseindia.com/api/<endpoint> as JSON (cached when name is given)."""
    if name:
        return json.loads(fetch_cached(NSE + endpoint, name, max_age_hours))
    return json.loads(http_get(NSE + endpoint).decode("utf-8"))


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("Rs.", "").strip())
    except (TypeError, ValueError):
        return None


def past_issues():
    """{SYMBOL: {"company", "issue_price", "listing_date", "price_range", "series"}} of NSE mainboard IPOs."""
    out = {}
    for r in nse_json("public-past-issues", "ipo_past.json", 12):
        sym = str(r.get("symbol") or "").upper()
        if not sym:
            continue
        out[sym] = {"company": r.get("company"), "issue_price": _num(r.get("issuePrice")),
                    "listing_date": _nse_date(r.get("listingDate")), "price_range": r.get("priceRange"),
                    "series": r.get("securityType"), "ipo_end": _nse_date(r.get("ipoEndDate"))}
    return out


def listing_day_of(symbol):
    """The listing date from EQUITY_L (or the IPO list), or None."""
    sym = symbol.upper()
    d = (equity_list().get(sym) or {}).get("listed")
    if d:
        return d
    try:
        return (past_issues().get(sym) or {}).get("listing_date")
    except DataError:
        return None


def is_listing_day(symbol, day):
    try:
        return listing_day_of(symbol) == day
    except DataError:
        return False


# ---------------------------------------------------------------- Angel One SmartAPI

def angel_creds():
    keys = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
    vals = [os.environ.get(k, "").strip() for k in keys]
    return dict(zip(("api_key", "client", "pin", "totp_secret"), vals)) if all(vals) else None


def totp(secret, for_time=None, step=30, digits=6):
    """RFC 6238 TOTP (SHA-1), the code an authenticator app shows."""
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    counter = int((for_time if for_time is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _mac():
    n = uuid.getnode()
    return ":".join("%02x" % ((n >> s) & 0xFF) for s in range(40, -1, -8))


class Angel(object):
    """The few SmartAPI calls this pack uses: login, candles, quotes. Never orders."""

    INTERVALS = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "1d": "ONE_DAY"}
    MAX_DAYS = {"1m": 30, "5m": 100, "15m": 200, "1d": 2000}

    def __init__(self, creds):
        self.c = creds
        self.jwt = None
        self.session_file = path("state", "angel_session.json")

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER",
             "X-SourceID": "WEB", "X-ClientLocalIP": _local_ip(), "X-ClientPublicIP": _local_ip(),
             "X-MACAddress": _mac(), "X-PrivateKey": self.c["api_key"]}
        if self.jwt:
            h["Authorization"] = "Bearer " + self.jwt
        return h

    def _post(self, route, body):
        blob = http_get(ANGEL_ROOT + route, data=json.dumps(body).encode("utf-8"), headers=self._headers(),
                        method="POST")
        try:
            doc = json.loads(blob.decode("utf-8"))
        except ValueError:
            raise DataError("Angel One sent something that is not JSON")
        if not doc.get("status") and doc.get("message") != "SUCCESS":
            raise DataError("Angel One: %s (%s)" % (doc.get("message") or "error", doc.get("errorcode") or "-"))
        return doc.get("data")

    def login(self):
        today = to_ist(utc_now()).date().isoformat()
        cached = read_json(self.session_file, default=None)
        if isinstance(cached, dict) and cached.get("date") == today and cached.get("client") == self.c["client"]:
            self.jwt = cached.get("jwt")
            return
        self.jwt = None
        data = self._post("/rest/auth/angelbroking/user/v1/loginByPassword",
                          {"clientcode": self.c["client"], "password": self.c["pin"],
                           "totp": totp(self.c["totp_secret"])})
        if not data or not data.get("jwtToken"):
            raise DataError("Angel One login failed: check ANGEL_CLIENT_CODE, ANGEL_PIN and ANGEL_TOTP_SECRET")
        self.jwt = data["jwtToken"]
        write_json(self.session_file, {"date": today, "client": self.c["client"], "jwt": self.jwt}, private=True)

    def call(self, route, body):
        if not self.jwt:
            self.login()
        try:
            return self._post(route, body)
        except DataError as e:
            if "Invalid Token" in str(e) or "AG8001" in str(e) or "HTTP 401" in str(e) or "HTTP 403" in str(e):
                if os.path.exists(self.session_file):
                    os.remove(self.session_file)
                self.jwt = None
                self.login()
                return self._post(route, body)
            raise

    def candles(self, symbol, interval, start_ist, end_ist):
        """[{t, o, h, l, c, v}] bar starts as UTC epochs. start/end are naive IST datetimes."""
        info = scrip_info(symbol)
        if not info:
            raise DataError("%s is not in Angel One's NSE scrip master" % symbol)
        out, a = {}, start_ist
        span = datetime.timedelta(days=self.MAX_DAYS[interval])
        while a <= end_ist:
            b = min(end_ist, a + span)
            data = self.call("/rest/secure/angelbroking/historical/v1/getCandleData",
                             {"exchange": "NSE", "symboltoken": info["token"], "interval": self.INTERVALS[interval],
                              "fromdate": a.strftime("%Y-%m-%d %H:%M"), "todate": b.strftime("%Y-%m-%d %H:%M")}) or []
            for row in data:
                try:
                    dt = datetime.datetime.fromisoformat(str(row[0]))
                except ValueError:
                    continue
                if dt.tzinfo is not None:
                    dt = (dt - dt.utcoffset()).replace(tzinfo=None)
                else:
                    dt = ist_to_utc(dt)
                t = ts_of(dt)
                out[t] = {"t": t, "o": float(row[1]), "h": float(row[2]), "l": float(row[3]),
                          "c": float(row[4]), "v": int(row[5] or 0)}
            a = b + datetime.timedelta(minutes=1)
            SLEEP(PAUSE)
        return [out[k] for k in sorted(out)]

    def quotes(self, symbols):
        """{SYM: {price, prev_close, open, high, low, upper_circuit, lower_circuit, time}} (FULL mode, 50 a call)."""
        tokens = {}
        for s in symbols:
            info = scrip_info(s)
            if info:
                tokens[info["token"]] = s.upper()
        out, ids = {}, list(tokens)
        for i in range(0, len(ids), 50):
            data = self.call("/rest/secure/angelbroking/market/v1/quote/",
                             {"mode": "FULL", "exchangeTokens": {"NSE": ids[i:i + 50]}}) or {}
            for q in data.get("fetched") or []:
                sym = tokens.get(str(q.get("symbolToken")))
                if not sym:
                    continue
                out[sym] = {"price": _num(q.get("ltp")), "prev_close": _num(q.get("close")),
                            "open": _num(q.get("open")), "high": _num(q.get("high")), "low": _num(q.get("low")),
                            "upper_circuit": _num(q.get("upperCircuit")),
                            "lower_circuit": _num(q.get("lowerCircuit")),
                            "volume": _num(q.get("tradeVolume")), "time": q.get("exchFeedTime"),
                            "source": "angel"}
            SLEEP(PAUSE)
        return out


def scrip_info(symbol, fetch=True):
    """{token, series, tick} for an NSE stock from Angel One's scrip master (the 35 MB file is boiled
    down to the NSE equity rows once a day and kept in trader-in/cache/angel_nse.json)."""
    p = cache_path("angel_nse.json")
    fresh = os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 20 * 3600
    if not fresh and fetch:
        try:
            rows = json.loads(http_get(SCRIP_MASTER, timeout=120).decode("utf-8"))
            slim = {}
            for r in rows:
                if r.get("exch_seg") != "NSE" or r.get("instrumenttype"):
                    continue
                s = str(r.get("symbol") or "")
                if "-" not in s:
                    continue
                name, series = s.rsplit("-", 1)
                if series not in ("EQ", "BE", "BZ", "SM", "ST"):
                    continue
                tick = _num(r.get("tick_size"))
                slim[name.upper()] = {"token": str(r.get("token")), "series": series,
                                      "tick": ("%.2f" % (tick / 100.0)) if tick else None}
            write_json(p, slim)
        except DataError:
            if not os.path.exists(p):
                return None
    doc = read_json(p, default=None) or {}
    return doc.get(symbol.upper())


# ---------------------------------------------------------------- Yahoo (.NS) fallback

def yahoo_symbol(sym):
    return urllib.parse.quote(sym.upper() + ".NS", safe=".-")


def yahoo_chart(sym, interval, rng=None, start_ist=None, end_ist=None):
    """[{t, o, h, l, c, v}] from Yahoo's chart API; the live tick off the bar grid is dropped."""
    if rng:
        q = "range=%s&interval=%s" % (rng, interval)
    else:
        q = "period1=%d&period2=%d&interval=%s" % (ts_of(ist_to_utc(start_ist)), ts_of(ist_to_utc(end_ist)),
                                                   interval)
    last = None
    for host in YAHOO_HOSTS:
        try:
            blob = http_get("%s/v8/finance/chart/%s?%s" % (host, yahoo_symbol(sym), q), retries=0)
            break
        except DataError as e:
            last = e
            continue
    else:
        raise DataError("Yahoo: %s" % last)
    doc = json.loads(blob.decode("utf-8"))
    res = ((doc.get("chart") or {}).get("result") or [None])[0]
    if not res:
        err = (doc.get("chart") or {}).get("error") or {}
        raise DataError("Yahoo has no chart for %s.NS (%s)" % (sym, err.get("description") or "no data"))
    stamps = res.get("timestamp") or []
    q0 = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    step = {"1m": 60, "5m": 300, "15m": 900}.get(interval)
    out = []
    for i, t in enumerate(stamps):
        try:
            o, h, l, c = q0["open"][i], q0["high"][i], q0["low"][i], q0["close"][i]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c) or t is None:
            continue
        if step and int(t) % step:
            continue
        out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                    "v": int((q0.get("volume") or [0] * len(stamps))[i] or 0)})
    return out


# ---------------------------------------------------------------- one door for bars and quotes

def source_for(cfg):
    want = str((cfg or {}).get("data_source") or "auto").lower()
    creds = angel_creds()
    if want == "angel":
        if not creds:
            raise DataError("data_source is \"angel\" but ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and "
                            "ANGEL_TOTP_SECRET are not all set")
        return "angel"
    if want == "yahoo":
        return "yahoo"
    return "angel" if creds else "yahoo"


def bars(symbol, interval, start_day, end_day, cfg=None, notes=None):
    """Intraday (or daily) bars of one NSE stock from start_day to end_day (dates, IST), oldest first.
    Returns (bars, source). Angel first when configured; on an Angel failure it says so in notes
    and tries Yahoo."""
    src = source_for(cfg)
    start = datetime.datetime.combine(start_day, PRE_OPEN)
    end = datetime.datetime.combine(end_day, datetime.time(15, 59))
    if src == "angel":
        try:
            return Angel(angel_creds()).candles(symbol, interval, start, end), "angel"
        except DataError as e:
            if str((cfg or {}).get("data_source") or "auto").lower() == "angel":
                raise
            if notes is not None:
                notes.append("Angel One failed (%s): used Yahoo instead" % e)
    days = (to_ist(utc_now()).date() - start_day).days
    limit = {"1m": 7, "5m": 59, "15m": 59}.get(interval, 3650)
    if days > limit:
        raise DataError("Yahoo keeps only about %d days of %s bars; %s is %d days ago (Angel One keeps more: "
                        "set the ANGEL_* variables)" % (limit, interval, start_day, days))
    out = yahoo_chart(symbol, interval, start_ist=start, end_ist=end)
    return out, "yahoo"


def session_bars(all_bars, day, interval="5m", listing=False, cutoff_ist=None):
    """The bars of one session on the grid, keyed by slot: [{t, o, h, l, c, v, start (ist), end, slot}].
    Slot 0 is the 9:15 bar (10:00 on a listing day). Bars not finished by cutoff_ist are left out."""
    step = {"1m": 60, "5m": 300, "15m": 900}[interval]
    s = session(day, listing)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    out = {}
    for b in all_bars:
        if b["t"] % step:
            continue
        st = ist_of_ts(b["t"])
        if st.date() != day or not (open_dt <= st < close_dt):
            continue
        end = st + datetime.timedelta(seconds=step)
        if cutoff_ist is not None and end > cutoff_ist:
            continue
        slot = int((st - open_dt).total_seconds()) // step
        out[slot] = dict(b, start=st, end=end, slot=slot)
    return [out[k] for k in sorted(out)]


def quotes(symbols, cfg=None, notes=None):
    """{SYM: {price, prev_close, open, high, low, upper_circuit, lower_circuit, time, source}}."""
    syms = [s.upper() for s in symbols if s]
    if not syms:
        return {}
    if source_for(cfg) == "angel":
        try:
            return Angel(angel_creds()).quotes(syms)
        except DataError as e:
            if notes is not None:
                notes.append("Angel One quotes failed (%s): used Yahoo instead" % e)
    out = {}
    for s in syms:
        try:
            blob = http_get("%s/v8/finance/chart/%s?range=1d&interval=1d" % (YAHOO_HOSTS[0], yahoo_symbol(s)),
                            retries=0)
            res = json.loads(blob.decode("utf-8"))["chart"]["result"][0]
            m = res.get("meta") or {}
            t = m.get("regularMarketTime")
            out[s] = {"price": m.get("regularMarketPrice"), "prev_close": m.get("chartPreviousClose"),
                      "open": None, "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
                      "upper_circuit": None, "lower_circuit": None, "volume": m.get("regularMarketVolume"),
                      "time": to_ist(utc_of(t)).strftime("%Y-%m-%d %H:%M:%S") if t else None, "source": "yahoo"}
        except (DataError, KeyError, IndexError, TypeError, ValueError) as e:
            if notes is not None:
                notes.append("no quote for %s (%s)" % (s, e))
        SLEEP(PAUSE)
    return out


def circuit_limits(symbol, prev_close, quote=None):
    """(lower, upper, how): exact from an Angel quote, else prev close +- NSE's band from sec_list.csv."""
    if quote and quote.get("upper_circuit") and quote.get("lower_circuit"):
        return quote["lower_circuit"], quote["upper_circuit"], "Angel One quote"
    try:
        band = price_bands().get(symbol.upper(), {}).get("band")
    except DataError:
        band = None
    if band and prev_close:
        tick = tick_for(prev_close, symbol)
        lo = to_tick(D(prev_close) * (1 - D(band) / 100), tick, "up")
        hi = to_tick(D(prev_close) * (1 + D(band) / 100), tick, "down")
        return float(lo), float(hi), "%g%% band on the previous close %s (NSE sec_list)" % (band, p2(prev_close))
    return None, None, "no band (F&O stocks have dynamic limits, about 10% at a time)" if band is None else "unknown"
