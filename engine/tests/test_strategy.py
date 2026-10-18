import math
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import strategy


def history(count=140, change=0.01):
    rows = []
    start = date(2025, 1, 1)
    for i in range(count):
        close = 10 + change * i + (0.08 if i % 3 == 0 else 0)
        rows.append({"date": str(start + timedelta(days=i)), "open": close,
                     "high": close + 0.1, "low": close - 0.1,
                     "close": close, "volume": 100000 + (i % 4) * 10000})
    return rows


class StrategyTests(unittest.TestCase):
    def test_constant_series_is_finite_and_ineligible(self):
        rows = history()
        for row in rows:
            row.update(open=10, high=10, low=10, close=10, volume=1000)
        result = strategy.analyze(rows, "sh600000", "浦发银行")
        for key in ("score", "rsi14", "atr14"):
            self.assertTrue(math.isfinite(result[key]), key)
        self.assertEqual(result["rsi14"], 50)
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["entry_low"])

    def test_eligible_ranges_use_current_raw_price_basis(self):
        rows = history()
        result = strategy.analyze(rows, "sh600000", "测试")
        self.assertTrue(result["eligible"])
        self.assertLess(result["stop_loss"], result["entry_low"])
        self.assertLessEqual(result["entry_low"], result["entry_high"])
        self.assertGreater(result["take_profit"], result["entry_high"])
        rows[-1]["raw_close"] = rows[-1]["close"] * 1.5
        raw = strategy.analyze(rows, "sh600000", "测试")
        for key in ("entry_low", "entry_high", "stop_loss", "take_profit"):
            self.assertAlmostEqual(raw[key], result[key] * 1.5, delta=0.015)
        self.assertEqual(raw["ma20"], result["ma20"])
        self.assertLessEqual(raw["max_position_pct"], 10)

    def test_analysis_ignores_future_benchmark_rows(self):
        rows = history(100)
        benchmark = history(140)
        first = strategy.analyze(rows, "sh600000", "测试", benchmark)
        for row in benchmark[100:]:
            for key in ("open", "high", "low", "close"):
                row[key] *= 10
        second = strategy.analyze(rows, "sh600000", "测试", benchmark)
        self.assertEqual(first, second)

    def test_backtest_future_changes_do_not_change_past_orders(self):
        rows = history(180)
        first = strategy.backtest(rows, "sh600000", entry_score=65)
        self.assertTrue(first["trades"], "The lookahead test must exercise actual orders.")
        altered = [dict(row) for row in rows]
        for row in altered[140:]:
            for key in ("open", "high", "low", "close"):
                row[key] *= 1.4
        second = strategy.backtest(altered, "sh600000", entry_score=65)
        cutoff = rows[140]["date"]
        self.assertEqual([t for t in first["trades"] if t["date"] < cutoff],
                         [t for t in second["trades"] if t["date"] < cutoff])
        self.assertEqual(first["equity_curve"][:21], second["equity_curve"][:21])

    def test_matured_forecast_samples_only(self):
        rows = history(100, 0)
        for row in rows:
            row.update(open=10, high=10.1, low=9.9, close=10)
        result = strategy.analyze(rows, "sh600000", "测试")
        self.assertEqual(result["forecast"]["sample_count"], 39)
        self.assertEqual(result["forecast"]["up_rate_pct"], 0)
        short = strategy.analyze(rows[:80], "sh600000", "测试")
        self.assertIsNone(short["forecast"]["up_rate_pct"])

    def test_t_plus_one_fees_and_lot_size(self):
        rows = history(135)
        fixed = {"score": 80, "eligible": True, "max_position_pct": 20, "atr14": 0.2}
        with patch.object(strategy, "_frame", return_value=fixed):
            result = strategy.backtest(rows, "sh600000", max_holding_days=1)
        self.assertGreater(len(result["trades"]), 1)
        held = None
        for trade in result["trades"]:
            self.assertLess(trade["signal_date"], trade["date"])
            self.assertEqual(trade["quantity"] % 100, 0)
            self.assertGreaterEqual(trade["commission"], 5)
            if trade["side"] == "BUY":
                held = trade["date"]
                self.assertEqual(trade["stamp_tax"], 0)
            else:
                self.assertGreater(trade["date"], held)
                self.assertGreaterEqual(trade["holding_days"], 1)
                self.assertAlmostEqual(trade["stamp_tax"], trade["amount"] * 0.0005, delta=0.011)
        self.assertAlmostEqual(result["fees"], sum(t["fees"] for t in result["trades"]), delta=0.1)

    def test_locked_limit_blocks_purchase(self):
        rows = history(121)
        close = rows[-2]["close"] * 1.1
        rows[-1].update(open=close, high=close, low=close, close=close)
        fixed = {"score": 80, "eligible": True, "max_position_pct": 20, "atr14": 0.2}
        with patch.object(strategy, "_frame", return_value=fixed):
            result = strategy.backtest(rows, "sh600000")
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["blocked_buys"], 1)

    def test_current_intraday_high_low_cannot_trigger_same_day_stop(self):
        rows = history(121)
        rows[-1]["low"] = 1
        rows[-1]["high"] = 100
        fixed = {"score": 80, "eligible": True, "max_position_pct": 20, "atr14": 0.2}
        with patch.object(strategy, "_frame", return_value=fixed):
            result = strategy.backtest(rows, "sh600000")
        self.assertEqual([t["side"] for t in result["trades"]], ["BUY"])

    def test_locked_limit_delays_sale_until_a_later_session(self):
        rows = history(123)
        limit_price = rows[120]["close"] * 0.9
        rows[121].update(open=limit_price, high=limit_price, low=limit_price, close=limit_price)
        fixed = {"score": 80, "eligible": True, "max_position_pct": 10, "atr14": 0.2}
        with patch.object(strategy, "_frame", return_value=fixed):
            result = strategy.backtest(rows, "sh600000", max_holding_days=1)
        self.assertEqual(result["blocked_sells"], 1)
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY", "SELL"])
        self.assertEqual(result["trades"][1]["date"], rows[122]["date"])
        self.assertEqual(result["trades"][1]["holding_days"], 2)

    def test_insufficient_history_removes_execution_levels(self):
        result = strategy.analyze(history(119), "sh600000", "测试")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["max_position_pct"], 0)
        for key in ("entry_low", "entry_high", "stop_loss", "take_profit"):
            self.assertIsNone(result[key])

    def test_st_and_index_excluded(self):
        rows = history()
        self.assertFalse(strategy.analyze(rows, "sh000001", "上证指数")["eligible"])
        self.assertFalse(strategy.analyze(rows, "sh600000", "*ST测试")["eligible"])
        self.assertEqual(strategy.backtest(rows, "sh000001")["trade_count"], 0)
        self.assertEqual(strategy.backtest(rows, "sh600000", name="ST测试")["trade_count"], 0)

    def test_star_minimum_200(self):
        rows = history(122)
        fixed = {"score": 80, "eligible": True, "max_position_pct": 20, "atr14": 0.2}
        with patch.object(strategy, "_frame", return_value=fixed):
            result = strategy.backtest(rows, "sh688001")
        self.assertTrue(result["trades"])
        self.assertGreaterEqual(result["trades"][0]["quantity"], 200)
        self.assertEqual(result["trades"][0]["quantity"] % 200, 0)

    def test_insufficient_or_invalid_input(self):
        with self.assertRaises(ValueError):
            strategy.analyze([], "sh600000", "测试")
        with self.assertRaises(ValueError):
            strategy.backtest(history(30), "sh600000")
        with self.assertRaises(ValueError):
            strategy.backtest(history(), "sh600000", initial_cash=-1)

    def cycle_history(self, count=130):
        rows = history(count)
        day = date(2026, 4, 30)
        for row in rows:
            while day.weekday() >= 5:
                day += timedelta(days=1)
            row.update(date=day.isoformat(), open=10, high=10.2, low=9.8, close=10, volume=10000)
            day += timedelta(days=1)
        return rows

    def cycle_signal(self):
        return {"score": 80, "eligible": True, "max_position_pct": 10, "ma5": 10, "ma20": 10}

    def test_two_session_cycle_buys_friday_and_sells_monday_not_buy_day(self):
        rows = self.cycle_history(122)
        # Choose calendar labels explicitly; S is Thursday, D1 Friday, D2 Monday.
        for i, row in enumerate(rows):
            row["date"] = (date(2026, 6, 18) + timedelta(days=i)).isoformat()
        rows[119]["date"], rows[120]["date"], rows[121]["date"] = "2026-10-15", "2026-10-16", "2026-10-19"
        # Earlier synthetic rows end before S and remain strictly ordered.
        with patch.object(strategy, "_frame", return_value=self.cycle_signal()):
            result = strategy.backtest_cycle(rows, "sh600298", name="测试")
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY", "SELL"])
        self.assertEqual(result["trades"][0]["date"], "2026-10-16")
        self.assertEqual(result["trades"][1]["date"], "2026-10-19")
        self.assertEqual(result["trades"][1]["holding_sessions"], 2)
        self.assertFalse(result["uses_prediction_model"])
        self.assertGreater(result["trades"][1]["stamp_tax"], 0)

    def test_cycle_cannot_sell_on_buy_session_even_with_large_intraday_move(self):
        rows = self.cycle_history(121)
        rows[-1].update(high=13, low=8, close=9)
        with patch.object(strategy, "_frame", return_value=self.cycle_signal()):
            result = strategy.backtest_cycle(rows, "sh600298")
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY"])
        self.assertGreater(result["open_shares"], 0)

    def test_cycle_limit_down_defers_exit_to_next_tradable_session(self):
        rows = self.cycle_history(123)
        rows[121].update(open=9, high=9, low=9, close=9)
        with patch.object(strategy, "_frame", return_value=self.cycle_signal()):
            result = strategy.backtest_cycle(rows, "sh600298")
        self.assertEqual(result["blocked_sells"], 1)
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY", "SELL"])
        self.assertEqual(result["trades"][1]["date"], rows[122]["date"])
        self.assertEqual(result["trades"][1]["holding_sessions"], 3)

    def test_cycle_future_changes_do_not_change_past_orders(self):
        rows = self.cycle_history(135)
        changed = [dict(row) for row in rows]
        for row in changed[130:]:
            row.update(open=12, high=12.2, low=11.8, close=12)
        with patch.object(strategy, "_frame", return_value=self.cycle_signal()):
            original = strategy.backtest_cycle(rows, "sh600298")
            altered = strategy.backtest_cycle(changed, "sh600298")
        prior = [trade for trade in original["trades"] if trade["date"] < rows[130]["date"]]
        self.assertTrue(prior)
        self.assertEqual(prior, [trade for trade in altered["trades"] if trade["date"] < rows[130]["date"]])

    def test_cycle_does_not_buy_above_support_band_or_excluded_stock(self):
        rows = self.cycle_history(121)
        rows[-1].update(open=10.2, high=10.3, close=10.2)
        with patch.object(strategy, "_frame", return_value=self.cycle_signal()):
            wide = strategy.backtest_cycle(rows, "sh600298")
            excluded = strategy.backtest_cycle(self.cycle_history(123), "sh600298", excluded=True)
        self.assertEqual(wide["trade_count"], 0)
        self.assertEqual(wide["outside_entry_range"], 1)
        self.assertEqual(excluded["trade_count"], 0)


if __name__ == "__main__":
    unittest.main()
