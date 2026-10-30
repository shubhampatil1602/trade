"""Offline tests for the India pack: the math and the rules, no network.

    python3 -m unittest discover -s tests -v
"""

import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("india-lib", "india-scanner", "india-entry", "india-exit", "india-timemachine", "india-budget",
          "india-broker", "india-alerts", "india-ipo", "india-news"):
    sys.path.insert(0, os.path.join(ROOT, d))

import indialib as L  # noqa: E402

L.SLEEP = lambda s: None


def bar(day, hh, mm, o, h, l, c, v=1000):
    ist = datetime.datetime.combine(day, datetime.time(hh, mm))
    return {"t": L.ts_of(L.ist_to_utc(ist)), "o": o, "h": h, "l": l, "c": c, "v": v}


class Lib(unittest.TestCase):
    def test_totp_rfc6238(self):
        self.assertEqual(L.totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59), "287082")

    def test_indian_grouping(self):
        self.assertEqual(L.inr(1234567.891), "₹12,34,567.89")
        self.assertEqual(L.inr(999), "₹999.00")
        self.assertEqual(L.inr(-52.5), "-₹52.50")

    def test_ticks(self):
        self.assertEqual(str(L.tick_from_table(249.99)), "0.01")
        self.assertEqual(str(L.tick_from_table(250)), "0.05")
        self.assertEqual(str(L.tick_from_table(1167.4)), "0.10")
        self.assertEqual(str(L.to_tick(1163.35, "0.10", "down")), "1163.30")
        self.assertEqual(str(L.to_tick(1167.45, "0.10")), "1167.50")
        self.assertTrue(L.on_tick(1167.4, "0.10"))
        self.assertFalse(L.on_tick(1163.33, "0.10"))

    def test_charges_angelone(self):
        c = L.charges(36403.30, 36062.30, {"broker": "angelone"})
        self.assertAlmostEqual(c["total"], 38.38, places=2)
        self.assertAlmostEqual(c["stt"], 9.02, places=2)

    def test_charges_groww_minimum(self):
        c = L.charges(1000, 1000, {"broker": "groww"})
        self.assertEqual(c["brokerage"], 10.0)    # Rs 5 minimum per order, 0.1% would be Re 1

    def test_ist_and_calendar(self):
        self.assertEqual(L.to_ist(datetime.datetime(2026, 10, 29, 3, 45)).time(), datetime.time(9, 15))
        self.assertFalse(L.trading_day(datetime.date(2026, 11, 10))[0])     # Diwali-Balipratipada
        self.assertFalse(L.trading_day(datetime.date(2026, 10, 31))[0])     # Saturday
        self.assertEqual(L.next_trading_day(datetime.date(2026, 11, 6)), datetime.date(2026, 11, 9))
        self.assertEqual(L.session(datetime.date(2026, 10, 28), listing=True)[0], datetime.time(10, 0))
        s = L.market_status(L.parse_now("2026-10-29 09:05"))
        self.assertEqual(s["phase"], "pre-open")

    def test_split_adjustment(self):
        d = [datetime.date(2026, 10, i) for i in (26, 27, 28)]
        hist = [(d[0], {"X": {"series": "EQ", "prev": 990, "o": 1000, "h": 1010, "l": 990, "c": 1000, "vol": 100}}),
                (d[1], {"X": {"series": "EQ", "prev": 1000, "o": 1000, "h": 1020, "l": 995, "c": 1010, "vol": 100}}),
                (d[2], {"X": {"series": "EQ", "prev": 505, "o": 506, "h": 510, "l": 500, "c": 508, "vol": 200}})]
        rows, actions = L.adjusted_daily(hist, "X")
        self.assertEqual(len(actions), 1)
        self.assertAlmostEqual(rows[1]["c"], 505.0)      # 1010 scaled by 505 / 1010
        self.assertAlmostEqual(rows[0]["c"], 500.0)


class Scanner(unittest.TestCase):
    def test_setups(self):
        import scan
        closes = [100 + i * 0.1 for i in range(29)] + [110]
        tags, _ = scan.find_setups(closes)
        self.assertIn("breakout_20d", tags)

    def test_orb_hit_listing_day(self):
        import scan
        day = datetime.date(2026, 10, 28)
        raw = [bar(day, 10, 0, 100, 102, 99, 101), bar(day, 10, 5, 101, 103, 100, 102),
               bar(day, 10, 10, 102, 103, 101, 102.5), bar(day, 10, 15, 102.5, 104, 102, 103.5)]
        bars = L.session_bars(raw, day, "5m", listing=True)
        h = scan.orb_hit(bars, 3)
        self.assertEqual((h["price"], h["or_high"], h["at"]), (103.5, 103, "10:20 AM"))


class Entry(unittest.TestCase):
    cfg = dict(L.DEFAULTS)

    def test_trigger_and_forming(self):
        import entry
        day = datetime.date(2026, 10, 29)
        raw = [bar(day, 9, 15, 100, 101, 99, 100.5), bar(day, 9, 20, 100.5, 101.5, 100, 101),
               bar(day, 9, 25, 101, 101.2, 100.2, 100.8), bar(day, 9, 30, 100.8, 102, 100.7, 101.9)]
        r = entry.evaluate(raw, "X", L.parse_now("2026-10-29 09:34"), cfg=self.cfg)
        self.assertEqual(r["status"], "waiting")          # the 9:30 bar is still forming
        r = entry.evaluate(raw, "X", L.parse_now("2026-10-29 09:36"), cfg=self.cfg)
        self.assertEqual((r["status"], r["trigger"]["price"], r["trigger"]["time"]), ("triggered", 101.9, "9:35 AM"))

    def test_listing_day_window(self):
        import entry
        day = datetime.date(2026, 10, 28)
        r = entry.evaluate([], "NEW", L.parse_now("2026-10-28 09:50"), cfg=self.cfg, listing=True)
        self.assertEqual(r["phase"], "pre-open")
        self.assertEqual(r["opens"], "10:00")

    def test_t2t_is_not_a_day_trade(self):
        import entry
        r = entry.evaluate([], "ABC", L.parse_now("2026-10-29 10:00"), cfg=self.cfg, series="BE")
        self.assertEqual(r["phase"], "t2t")


class Exit(unittest.TestCase):
    def test_compute_on_ticks(self):
        import exit as ex
        c = ex.compute(1174.30, "or_mid", None, "r", L.D(2), "0.10", 1170.50, 1156.20)
        self.assertEqual((str(c["stop"]), str(c["target"]), str(c["risk"])), ("1163.30", "1196.30", "11.00"))

    def test_too_tight(self):
        import exit as ex
        with self.assertRaises(ex.PlanError):
            ex.compute(100.00, "price", L.D("99.99"), "r", L.D(2), "0.01")


class TimeMachine(unittest.TestCase):
    def test_gap_fill_and_thin(self):
        import timemachine as tm
        days = {}
        for n in range(6):
            d = "2026-10-%02d" % (19 + n)
            days[d] = {i: {"o": 100, "h": 100.5, "l": 99.5, "c": 100, "v": 1} for i in range(75) if i not in (40, 41)}
        full, short, filled = tm.full_sessions(days, "2026-10-30")
        self.assertEqual((len(full), filled), (6, 12))
        self.assertTrue(full[0][1][40].get("filled"))

    def test_simulate_is_seeded(self):
        import timemachine as tm
        sess = [("d%d" % k, [{"o": 100, "h": 100.6, "l": 99.4, "c": 100 + (0.1 if i % 2 else -0.1), "v": 1}
                             for i in range(75)]) for k in range(20)]
        pool = tm.build_pool(sess)
        a = tm.simulate(pool, 100, 99, 102, 4, 70, replays=500, seed=7)
        b = tm.simulate(pool, 100, 99, 102, 4, 70, replays=500, seed=7)
        self.assertEqual(a["p_target"], b["p_target"])
        self.assertAlmostEqual(a["p_target"] + a["p_stop"] + a["p_neither"], 1.0, places=3)


class Budget(unittest.TestCase):
    def test_sizes_inside_max_loss_with_charges(self):
        import size
        plan = {"symbol": "RELIANCE", "entry": 1174.30, "stop": 1163.30, "target": 1196.30, "date": "2026-10-29"}
        cfg = dict(L.DEFAULTS, budget=500000, max_order_inr=10 ** 7, max_position_pct=100)
        s = size.size_trade(plan, cfg, today="2026-10-29")
        self.assertEqual(s["bound_by"], "max loss")
        self.assertLessEqual(s["max_loss"], 500)
        self.assertGreater(L.charges(s["qty"] * 1174.3 + 1174.3, (s["qty"] + 1) * 1163.3, cfg)["total"]
                           + (s["qty"] + 1) * 11, 500)

    def test_refuses_t2t(self):
        import size
        plan = {"symbol": "ABC", "series": "BE", "entry": 10, "stop": 9.5, "target": 11}
        with self.assertRaises(size.SizeError):
            size.size_trade(plan, dict(L.DEFAULTS, budget=50000))


class Broker(unittest.TestCase):
    order = {"symbol": "RELIANCE", "side": "buy", "qty": "31", "type": "market", "product": "MIS",
             "stop_loss": "1163.30", "target": "1196.30", "plan": {"max_loss": 379.38}}

    def test_yes_rules(self):
        import ticket
        self.assertTrue(ticket.parse_yes("yes", self.order)[0])
        self.assertTrue(ticket.parse_yes("yes, 31 shares", self.order)[0])
        self.assertFalse(ticket.parse_yes("ok", self.order)[0])
        self.assertFalse(ticket.parse_yes("yes to all", self.order)[0])
        self.assertFalse(ticket.parse_yes("yes 40", self.order)[0])
        self.assertFalse(ticket.parse_yes("yes but wait", self.order)[0])
        self.assertFalse(ticket.parse_yes("yes TCS", self.order, ["RELIANCE", "TCS"])[0])

    def test_steps_never_rest_two_sells(self):
        import ticket
        steps = " ".join(ticket.app_steps(self.order, "angelone"))
        self.assertIn("SL-M", steps)
        self.assertIn("Do NOT also rest a target sell", steps)


class Alerts(unittest.TestCase):
    def test_rings_once(self):
        import alerts
        rules = [{"id": "x-stop", "symbol": "X", "type": "stop", "price": 99.0}]
        st = L.market_status(L.parse_now("2026-10-29 11:00"))
        rings, state = alerts.evaluate(rules, {"X": {"price": 98.9}}, st, {}, L.parse_now("2026-10-29 11:00"))
        self.assertEqual(len(rings), 1)
        rings, state = alerts.evaluate(rules, {"X": {"price": 98.0}}, st, state, L.parse_now("2026-10-29 11:01"))
        self.assertEqual(len(rings), 0)


class News(unittest.TestCase):
    def test_tags(self):
        import news
        t = lambda cat, sub: news.tag({"category": cat, "subject": sub})
        self.assertEqual(t("Trading Window-XBRL", "Trading Window closure pursuant to SEBI (PIT) Regulations")[0],
                         "routine")
        self.assertEqual(t("Bagging/Receiving of orders/contracts", "X has informed the Exchange about Bagging")[0],
                         "order-win")
        self.assertEqual(t("General Updates", "Rubicon Research Limited has informed the Exchange")[0], "other")
        self.assertEqual(t("Price movement", "Significant movement in price ... in order to ensure")[0],
                         "clarification")
        self.assertEqual(t("Outcome of Board Meeting", "submitted the financial results for the quarter")[0],
                         "results")


class IPO(unittest.TestCase):
    def test_setups(self):
        import ipo
        rows = [{"o": 110, "h": 120, "l": 105, "c": 112}, {"o": 112, "h": 118, "l": 108, "c": 109},
                {"o": 109, "h": 125, "l": 108, "c": 123}]
        tags = ipo.new_listing_setups(rows, 100)
        self.assertIn("listing_high_breakout", tags)
        self.assertIn("ipo_closing_high", tags)
        self.assertNotIn("below_issue", tags)

    def test_same_company(self):
        import ipo
        self.assertTrue(ipo.same_company("Jio Platforms Limited", "Jio Platforms Ltd"))
        self.assertFalse(ipo.same_company("Parin Enterprises Limited", "Zeta Furniture Limited"))


if __name__ == "__main__":
    unittest.main()
