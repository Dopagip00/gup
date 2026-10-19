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
                     "close": close, "raw_close": close, "volume": 100000 + (i % 4) * 10000})
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
        # A common adjustment factor is a valid basis conversion. Changing
        # only the latest factor instead is now an explicit data-risk veto.
        for row in rows:
            row["raw_close"] = row["close"] * 1.5
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

    def risk_history(self, final_closes=None, count=140):
        rows = history(count)
        for row in rows:
            row.update(open=10, high=10.1, low=9.9, close=10, raw_close=10, volume=10000)
        if final_closes:
            for row, close in zip(rows[-len(final_closes):], final_closes):
                row.update(open=close, high=close + .1, low=close - .1,
                           close=close, raw_close=close)
        return rows

    def risk(self, rows, symbol="sh600298", name="测试", benchmark=None):
        return strategy.analyze(rows, symbol, name, benchmark)["chase_risk"]

    def risk_codes(self, result):
        return {item["code"] for item in result["reasons"]}

    def test_two_limits_remain_blocked_after_one_down_session(self):
        rows = self.risk_history([11, 12.10, 11.50])
        result = strategy.analyze(rows, "sh600298", "测试")
        risk = result["chase_risk"]
        self.assertIn("recent_limit_streak", self.risk_codes(risk))
        self.assertEqual(risk["metrics"]["recent_max_streak"], 2)
        self.assertEqual(risk["metrics"]["last5_limit_up_count"], 2)
        self.assertTrue(risk["blocked"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["max_position_pct"], 0)
        for key in ("entry_low", "entry_high", "stop_loss", "take_profit"):
            self.assertIsNone(result[key])

    def test_recent_streak_endpoint_in_first_of_last_five_is_still_seen(self):
        rows = self.risk_history([11, 12.10, 11.5, 11.4, 11.3, 11.2])
        risk = self.risk(rows)
        self.assertEqual(risk["metrics"]["last5_limit_up_count"], 1)
        self.assertIn("recent_limit_streak", self.risk_codes(risk))

    def test_nonconsecutive_cluster_of_three_limits_is_blocked(self):
        rows = self.risk_history([11, 10, 11, 10, 11])
        risk = self.risk(rows)
        self.assertEqual(risk["metrics"]["last5_limit_up_count"], 3)
        self.assertIn("clustered_limit_ups", self.risk_codes(risk))

    def test_board_limits_and_tick_rounding_are_distinguished(self):
        ten = self.risk_history([11])
        self.assertEqual(self.risk(ten)["metrics"]["last5_limit_up_count"], 1)
        self.assertEqual(self.risk(ten, "sh688001")["metrics"]["last5_limit_up_count"], 0)
        twenty = self.risk_history([12])
        self.assertEqual(self.risk(twenty, "sh688001")["metrics"]["last5_limit_up_count"], 1)
        low = self.risk_history()
        for row in low:
            row.update(open=1.01, high=1.02, low=1, close=1.01, raw_close=1.01)
        low[-1].update(open=1.11, high=1.11, low=1.1, close=1.11, raw_close=1.11)
        self.assertEqual(self.risk(low)["metrics"]["last5_limit_up_count"], 1)
        low[-1].update(open=1.10, high=1.11, low=1.09, close=1.10, raw_close=1.10)
        self.assertEqual(self.risk(low)["metrics"]["last5_limit_up_count"], 0)

    def test_main_board_st_historical_five_and_current_ten_both_excluded(self):
        old = self.risk_history([10.50])  # Synthetic date is in 2025.
        old_risk = self.risk(old, "sh600298", "ST测试")
        self.assertEqual(old_risk["metrics"]["limit_pct"], 5)
        self.assertEqual(old_risk["metrics"]["last5_limit_up_count"], 1)
        self.assertIn("restricted_security", self.risk_codes(old_risk))
        new = self.risk_history([11])
        for i, row in enumerate(new):
            row["date"] = str(date(2026, 7, 6) + timedelta(days=i))
        new_risk = self.risk(new, "sh600298", "*ST测试")
        self.assertEqual(new_risk["metrics"]["limit_pct"], 10)
        self.assertEqual(new_risk["metrics"]["last5_limit_up_count"], 1)
        self.assertFalse(new_risk["gate_passed"])
        star_risk = self.risk(self.risk_history([12]), "sh688001", "ST测试")
        self.assertEqual(star_risk["metrics"]["limit_pct"], 20)
        self.assertEqual(star_risk["metrics"]["last5_limit_up_count"], 1)

    def test_adjustment_factor_break_is_not_counted_as_confirmed_limit(self):
        rows = self.risk_history()
        rows[-1]["raw_close"] = 11  # qfq remains flat: may be reference change.
        risk = self.risk(rows)
        self.assertEqual(risk["metrics"]["last5_limit_up_count"], 0)
        self.assertIn("adjustment_reference_changed", self.risk_codes(risk))
        self.assertEqual(risk["status"], "insufficient")
        self.assertIsNone(risk["entry_ceiling"]["price"])

    def test_missing_raw_and_known_session_gap_fail_closed(self):
        rows = history()
        rows[-7].pop("raw_close")
        self.assertIn("raw_price_missing", self.risk_codes(self.risk(rows)))
        complete = history()
        benchmark = [dict(row) for row in complete]
        del complete[-5]
        risk = self.risk(complete, benchmark=benchmark)
        self.assertFalse(risk["metrics"]["benchmark_aligned"])
        self.assertIn("session_gap", self.risk_codes(risk))
        self.assertIsNone(risk["entry_ceiling"]["price"])

    def test_future_benchmark_does_not_change_risk_or_input(self):
        rows = history()
        benchmark = history(150)
        before = [dict(row) for row in rows]
        self.assertEqual(self.risk(rows, benchmark=benchmark), self.risk(rows, benchmark=benchmark[:140]))
        self.assertEqual(rows, before)

    def test_single_elevated_move_warns_without_upgrading_low_score(self):
        rows = self.risk_history([10.8])
        risk = self.risk(rows)
        self.assertIn("elevated_return_3d", self.risk_codes(risk))
        self.assertNotIn("rapid_return_3d", self.risk_codes(risk))
        flat = strategy.analyze(self.risk_history(), "sh600298", "测试")
        self.assertTrue(flat["chase_risk"]["gate_passed"])
        self.assertFalse(flat["eligible"])

    def test_rapid_rise_ma_extension_and_heavy_volume_decline_block(self):
        rise = self.risk(self.risk_history([10.4, 11, 11.6]))
        self.assertIn("rapid_return_3d", self.risk_codes(rise))
        extension = self.risk(self.risk_history([12] * 3))
        self.assertIn("ma20_overextended", self.risk_codes(extension))
        falling = self.risk_history([8.5])
        falling[-1].update(open=10, high=10.1, low=8.4, volume=40000)
        decline = self.risk(falling, "sh688001")
        self.assertIn("heavy_volume_decline", self.risk_codes(decline))
        self.assertNotIn("recent_limit_streak", self.risk_codes(decline))

    def test_completed_gap_and_hot_volume_upper_shadow_are_explained(self):
        rows = self.risk_history([10.9])
        rows[-1].update(open=10.7, high=11.5, low=10.6, volume=25000)
        risk = self.risk(rows)
        self.assertIn("heated_gap", self.risk_codes(risk))
        self.assertIn("volume_upper_shadow", self.risk_codes(risk))
        self.assertIn("不是待买交易日开盘价", next(item["detail"] for item in risk["reasons"] if item["code"] == "completed_bar_gap"))

    def test_entry_ceiling_uses_only_last_raw_close_and_ma20(self):
        rows = history()
        result = strategy.analyze(rows, "sh600298", "测试", rows)
        risk = result["chase_risk"]
        ceiling = risk["entry_ceiling"]
        self.assertEqual(ceiling["reference_date"], rows[-1]["date"])
        self.assertTrue(ceiling["requires_live_quote"])
        self.assertLessEqual(ceiling["price"], rows[-1]["raw_close"] * 1.02)
        self.assertLessEqual(ceiling["price"], sum(row["close"] for row in rows[-20:]) / 20 * 1.08)
        self.assertTrue(result["eligible"])
        self.assertLessEqual(result["entry_high"], ceiling["price"])
        self.assertLess(result["stop_loss"], result["entry_low"])

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
