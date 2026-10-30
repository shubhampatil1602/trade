#!/usr/bin/env python3
"""Time Machine for india-timemachine: replay an NSE day trade before you risk a rupee. Standard library only.

    python3 timemachine.py run                         # trader-in/plan.json, 10,000 replays, seed 19
    python3 timemachine.py run --write                 # also save trader-in/timemachine.json
    python3 timemachine.py run --until 15:05           # stop each replay at your flat-by time
    python3 timemachine.py run --symbol RELIANCE --date 2026-10-29 --entry 1174.30 \\
        --stop 1163.30 --target 1196.30 --entry-time "9:40 AM"
    python3 timemachine.py chart                       # ASCII fan chart of the replays

How it works (the original Time Machine's algorithm, on NSE's clock):
  1. 5-minute bars of the stock for about 60 sessions before the plan's date
     (Angel One SmartAPI, or Yahoo .NS: one request). Only FULL sessions
     before the plan's day are kept: 75 bars, 9:15 to 15:30 IST. Yahoo drops
     a bar here and there, so a session with the 9:15 bar and at most 4
     missing bars still counts; each missing bar is filled flat at the
     previous close and the run says how many. Listing days (trading from
     10:00) and sessions with bigger gaps are left out.
  2. The pool: for every bar slot 1..74, each past session's bar as ratios to
     the previous bar's close: (open, high, low, close) / previous close.
  3. One replay starts at the entry and walks the rest of the session; for
     bar i it draws slot j = i + randint(-2, 2), kept in 1..74, and one past
     bar from pool[j], scaled to the current price. The stop is checked
     before the target in every bar (a bar touching both counts as the stop).
  4. Outcomes: target, stop, or neither by --until (default 15:30; your
     flat_by is the honest choice: you will be out by then). R = the target's
     R, -1 at the stop, else (price at --until - entry) / risk.
  5. History: the plan's own rule (an opening-range breakout, same range
     length, stop rule and target R) on every past full session.

New listings: a stock with 20+ full sessions runs normally. With 5 to 19 it
still runs, marked THIN: the pool is small and the odds are rough. Under 5,
or on the listing day itself, it refuses: there is nothing to replay.

The verdict compares the target's share of the replays that hit a side,
target / (target + stop), with the break-even share 1 / (1 + R): under 5
points "close to a coin flip / break-even", 5 to 15 "leans", 15+ "strongly".
A simulation built from the stock's own recent bars is not a prediction:
results, news, a circuit or a block deal can do what those sessions never did.
"""

import argparse
import datetime
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "india-lib"))
import indialib as L  # noqa: E402

SESSION_BARS = L.SESSION_BARS    # 75
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
MIN_SESSIONS = 20
MIN_THIN = 5
MAX_FILL = 4         # missing 5-minute bars a session may have (filled flat) and still count as full
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP, STRONG, MANY_NEITHER = 0.05, 0.15, 0.40


class TMError(Exception):
    pass


def clock12(minutes):
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


def parse_clock(text, what):
    t = str(text).strip().upper()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            x = datetime.datetime.strptime(t, fmt).time()
            return x.hour * 60 + x.minute
        except ValueError:
            pass
    raise TMError("%s wants a time like \"10:15 AM\" or 10:15, got %r" % (what, text))


def split_sessions(bars):
    """{date iso: {slot: bar}} on the 9:15 grid."""
    days = {}
    for b in bars:
        st = L.ist_of_ts(b["t"])
        mins = st.hour * 60 + st.minute - OPEN_MIN
        if b["t"] % 300 or mins < 0 or mins >= SESSION_BARS * 5:
            continue
        days.setdefault(st.date().isoformat(), {})[mins // 5] = dict(b, start=st.strftime("%H:%M"))
    return days


def full_sessions(days, before, max_fill=MAX_FILL):
    """Sessions before the plan's day with the 9:15 bar and at most max_fill missing bars; a missing bar
    (Yahoo drops a few) is filled flat at the previous close, a "no move" bar. Returns (full, short, filled)."""
    full, short, filled = [], [], 0
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        gaps = SESSION_BARS - len(s)
        if 0 not in s or gaps > max_fill:
            short.append({"date": d, "bars": len(s)})
            continue
        row = []
        for i in range(SESSION_BARS):
            if i in s:
                row.append(s[i])
            else:
                pc = row[-1]["c"]
                row.append({"t": None, "o": pc, "h": pc, "l": pc, "c": pc, "v": 0, "filled": True})
        filled += gaps
        full.append((d, row))
    return full, short, filled


def build_pool(sessions):
    pool = [[] for _ in range(SESSION_BARS)]
    for _, D in sessions:
        for i in range(1, SESSION_BARS):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2):
    risk = entry - stop
    r_target = round((target - entry) / risk, 4)

    def slot(rng, i):
        return i if window == 0 else min(SESSION_BARS - 1, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p, outcome, keep = entry, None, n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            if outcome is None:
                if p * l <= stop:
                    outcome = "stop"
                elif p * h >= target:
                    outcome = "target"
            p = p * c
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes) if o == "target" or (o == "close" and p > entry))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else (p - entry) / risk for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p, path = entry, [entry]
        for i in range(k + 1, last + 1):
            p = p * choice(pool[slot(rng, i)])[3]
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {"replays": replays, "seed": seed, "window": window, "bars_left": last - k,
            "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
            "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
            "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
            "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
            "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
            "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins}}


def floor_tick(x, tick):
    return math.floor(round(x / tick, 6)) * tick


def history_replay(sessions, rule, last, tick):
    n = rule["or_minutes"] // 5
    trades = []
    for d, Dd in sessions:
        oh = max(b["h"] for b in Dd[:n])
        ol = min(b["l"] for b in Dd[:n])
        kk = next((i for i in range(n, last + 1) if Dd[i]["c"] > oh), None)
        if kk is None:
            continue
        e = round(Dd[kk]["c"], 2)
        if rule["stop_rule"] == "or_mid":
            s = floor_tick((oh + ol) / 2, tick)
        elif rule["stop_rule"] == "or_low":
            s = floor_tick(ol, tick)
        else:
            s = floor_tick(e * (1 - rule["stop_pct"] / 100.0), tick)
        risk = e - s
        if risk <= 0:
            continue
        t = round(e + rule["target_r"] * risk, 2)
        oc = None
        for b in Dd[kk + 1:last + 1]:
            if b["l"] <= s:
                oc = "stop"
                break
            if b["h"] >= t:
                oc = "target"
                break
        cl = Dd[last]["c"]
        R = (t - e) / risk if oc == "target" else -1.0 if oc == "stop" else (cl - e) / risk
        trades.append({"date": d, "time": clock12(OPEN_MIN + 5 * (kk + 1)), "entry": e, "stop": round(s, 2),
                       "target": t, "outcome": oc or "close", "R": round(R, 2)})
    return {"sessions": len(sessions), "triggered": len(trades),
            "target": sum(1 for x in trades if x["outcome"] == "target"),
            "stop": sum(1 for x in trades if x["outcome"] == "stop"),
            "close": sum(1 for x in trades if x["outcome"] == "close"),
            "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
            "rule": rule, "trades": trades}


def lean(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    until = clock12(parse_clock(mc["until"], "until"))
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (until, until)
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * mc["p_target"], 100 * mc["p_stop"])
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; it did in "
            "%.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc):
    notes = []
    until = clock12(parse_clock(mc["until"], "until"))
    lv = lean(mc)
    if mc.get("p_neither", 0) > MANY_NEITHER and lv is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * mc["p_neither"], until, until))
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


def load_plan(a):
    if a.symbol:
        need = {"--entry": a.entry, "--stop": a.stop, "--target": a.target, "--date": a.date}
        miss = [k for k, v in need.items() if v is None]
        if miss:
            raise TMError("with --symbol also give %s" % ", ".join(miss))
        p = {"symbol": a.symbol.upper(), "side": "buy", "date": a.date, "entry": a.entry, "stop": a.stop,
             "target": a.target, "entry_time": a.entry_time, "entry_bar": None, "setup": a.setup,
             "stop_rule": "auto", "listing_day": False,
             "opening_range": {"minutes": a.or_minutes} if a.setup == "orb" else None}
    else:
        p = L.read_json(a.plan, "plan (run /india-exit first, or give --symbol ...)")
    for k in ("entry", "stop", "target"):
        if not isinstance(p.get(k), (int, float)) or p[k] <= 0:
            raise TMError("the plan needs a positive %s" % k)
    if p["stop"] >= p["entry"] or p["target"] <= p["entry"]:
        raise TMError("a long plan needs stop < entry < target")
    if p.get("listing_day"):
        raise TMError("%s listed on %s, the plan's own day: there are no past sessions to replay. On a stock's "
                      "first days, size it small instead." % (p["symbol"], p["date"]))
    if p.get("entry_bar") is None:
        if not p.get("entry_time"):
            raise TMError("the plan needs the entry time (--entry-time \"10:15 AM\")")
        mins = parse_clock(p["entry_time"], "the entry time")
        if (mins - OPEN_MIN) % 5 or mins <= OPEN_MIN:
            raise TMError("the entry time %s is not a 5-minute bar close after 9:15" % p["entry_time"])
        p["entry_bar"] = (mins - OPEN_MIN) // 5 - 1
    if not p.get("entry_time"):
        p["entry_time"] = clock12(OPEN_MIN + 5 * (p["entry_bar"] + 1))
    p["risk_per_share"] = round(p["entry"] - p["stop"], 2)
    p["target_r"] = round((p["target"] - p["entry"]) / p["risk_per_share"], 4)
    return p


def resolve_rule(plan, entry_day, tick):
    if str(plan.get("setup", "orb")).lower() != "orb" or not plan.get("opening_range"):
        return None
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "target_r": plan["target_r"],
            "stop_rule": plan.get("stop_rule") or "auto",
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    if rule["stop_rule"] not in ("or_mid", "or_low"):
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh = max(entry_day[i]["h"] for i in range(n))
            ol = min(entry_day[i]["l"] for i in range(n))
            if abs(floor_tick((oh + ol) / 2, tick) - plan["stop"]) < 0.005:
                rule["stop_rule"] = "or_mid"
            elif abs(floor_tick(ol, tick) - plan["stop"]) < 0.005:
                rule["stop_rule"] = "or_low"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


def time_machine(raw, plan, now_utc, replays, seed, window, until, source):
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    sym = plan["symbol"]
    days = split_sessions(raw)
    k = plan["entry_bar"]
    until_min = CLOSE_MIN if until is None else parse_clock(until, "--until")
    if (until_min - OPEN_MIN) % 5 or until_min > CLOSE_MIN:
        raise TMError("--until must be on the 5-minute grid and no later than 3:30 PM")
    last = (until_min - OPEN_MIN) // 5 - 1
    if last <= k:
        raise TMError("--until %s leaves nothing to replay after the %s entry" % (clock12(until_min),
                                                                             plan["entry_time"]))
    entry_day = days.get(plan["date"], {})
    sessions, short, filled = full_sessions(days, plan["date"])
    thin = len(sessions) < MIN_SESSIONS
    if len(sessions) < MIN_THIN:
        raise TMError("only %d full session(s) of %s before %s; the time machine needs at least %d (new listings "
                      "get there in a week)" % (len(sessions), sym, plan["date"], MIN_THIN))
    warn = []
    if k in entry_day:
        eb = entry_day[k]
        if not eb["l"] - 0.05 <= plan["entry"] <= eb["h"] + 0.05:
            warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f" % (plan["entry"], eb["l"], eb["h"]))
    else:
        warn.append("the entry bar (closing %s) is not in the data; the replay starts from the plan's price"
                    % plan["entry_time"])
    if thin:
        warn.append("THIN: only %d full sessions (a young stock). The pool is small: treat the odds as rough"
                    % len(sessions))
    if filled:
        warn.append("%d missing 5-minute bar(s) across those sessions were filled flat at the previous close "
                    "(%s data has small gaps)" % (filled, source))
    pool = build_pool(sessions)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed, window)
    mc.update(until=clock24(until_min), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0], thin=thin,
              band_times=[clock24(OPEN_MIN + 5 * (j + 1)) for j in range(k, last + 1)])
    tick = float(L.tick_for(plan["entry"], sym))
    rule = resolve_rule(plan, entry_day, tick)
    if rule:
        history = history_replay(sessions, rule, last, tick)
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) only"}
    return {"created_by": "india-timemachine", "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source, "plan": plan, "mc": mc, "history": history,
            "short_sessions_left_out": short, "verdict": ("THIN DATA. " if thin else "") + verdict(mc),
            "verdict_notes": verdict_notes(mc), "warnings": warn,
            "note": ("A simulation built from %s's own last %d full sessions. It is not a prediction: results, "
                     "news, a circuit or a block deal can do what those sessions never did. Same seed, same "
                     "numbers." % (sym, len(sessions)))}


def pc(x):
    return "%.1f %%" % (100.0 * x)


def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    until = clock12(parse_clock(m["until"], "until"))
    lines = [
        "%s buy, %s: entry %s at %s (bar %d), stop %s, target %s (%gR, risk %s a share)."
        % (p["symbol"], p["date"], L.p2(p["entry"]), p["entry_time"], p["entry_bar"], L.p2(p["stop"]),
           L.p2(p["target"]), p["target_r"], L.p2(p["risk_per_share"])),
        "%s replays of the rest of the session, %s to %s (%d five-minute bars), seed %d."
        % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"], m["seed"]),
        "Each bar is drawn whole from %s's own 5-minute bars at the same time of day (+-%d bars) over its last %d "
        "full sessions (%s to %s)." % (p["symbol"], m["window"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or above the entry at %s" % (pc(m["p_green"]), until),
        "",
        "  Price at %s: median %s, 90 %% of replays between %s and %s." % (until, L.p2(m["median_close"]),
                                                                          L.p2(m["p5_close"]), L.p2(m["p95_close"])),
        "  Average R: %+.3f (R = the %s risk: +%gR at the target, -1R at the stop)."
        % (m["avg_R"], L.p2(p["risk_per_share"]), m["r_at_target"]),
        ""]
    if h.get("rule"):
        ru = h["rule"]
        words = {"or_mid": "the opening-range midpoint", "or_low": "the opening-range low"}.get(
            ru["stop_rule"], "%.2f %% under the entry" % ru.get("stop_pct", 0))
        if h["triggered"]:
            lines.append("History: the same rule (%d-min range, stop at %s, %gR target) on those %d real sessions "
                         "fired %d times: %d target, %d stop, %d neither. Average R %+.3f."
                         % (ru["or_minutes"], words, ru["target_r"], h["sessions"], h["triggered"], h["target"],
                            h["stop"], h["close"], h["avg_R"]))
        else:
            lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc["verdict_notes"]) + [""]
    for w in doc["warnings"]:
        lines.append("Note: " + w + ".")
    if doc["short_sessions_left_out"]:
        lines.append("Left out %d short session(s) (listing day, half days or gaps): %s."
                     % (len(doc["short_sessions_left_out"]), ", ".join(x["date"] for x in doc["short_sessions_left_out"])))
    lines.append(doc["note"])
    return lines


def render_chart(doc, height=17):
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d)" % (p["symbol"], p["date"], "{:,}".format(BAND_PATHS),
                                                                  p["entry_time"], m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        hh, mm = int(times[j][:2]), times[j][3:]
        lab = "%d:%s" % (hh % 12 or 12, mm)
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


def compute(a):
    now = L.parse_now(a.now)
    plan = load_plan(a)
    cfg = L.load_config(a.config)
    day = datetime.date.fromisoformat(plan["date"])
    src = L.source_for(cfg)
    back = 90 if src == "angel" else 58
    raw, source = L.bars(plan["symbol"], "5m", day - datetime.timedelta(days=back), day, cfg)
    if not raw:
        raise TMError("no 5-minute bars for %s" % plan["symbol"])
    return time_machine(raw, plan, now, a.replays, a.seed, a.window, a.until, source)


def cmd_run(a):
    doc = compute(a)
    if a.write:
        L.write_json(a.write, doc)
    if a.json:
        print(json.dumps(dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"}), indent=2,
                         ensure_ascii=False))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("Next: /india-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        doc = L.read_json(a.from_file, "time machine result (run timemachine.py run --write)")
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc["verdict"]] + list(doc["verdict_notes"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE time machine (india-timemachine)")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("run", "chart"):
        p = sub.add_parser(name)
        p.add_argument("--plan", default=L.path("plan.json"))
        p.add_argument("--symbol")
        p.add_argument("--date")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time")
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2)
        p.add_argument("--until")
        p.add_argument("--config", default=L.path("config.json"))
        p.add_argument("--now")
        p.add_argument("--json", action="store_true")
        if name == "run":
            p.add_argument("--write", nargs="?", const=L.path("timemachine.json"))
        else:
            p.add_argument("--from", dest="from_file")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except (TMError, L.DataError, ValueError) as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
