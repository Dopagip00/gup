import copy
import datetime as dt
import math
import random
import unittest
from unittest.mock import patch

import forecast


def bars(count=400, seed=21):
    randomizer = random.Random(seed)
    price = 20.0
    rows = []
    momentum = 0.0
    for i in range(count):
        opening = price * (1 + randomizer.gauss(0, 0.002))
        momentum = 0.55 * momentum + randomizer.gauss(0, 0.004)
        price *= 1 + 0.0003 + momentum
        rows.append({"date": (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(),
                     "open": opening, "close": price, "volume": 100000 + randomizer.randrange(80000)})
    return rows


class ForecastTests(unittest.TestCase):
    def test_minimum_training_and_validation_requirements(self):
        unavailable = forecast.predict(bars(245))
        self.assertFalse(unavailable["available"])
        self.assertFalse(unavailable["eligible"])
        self.assertIsNone(unavailable["rank_value"])
        self.assertIsNone(unavailable["expected_return_pct"])
        result = forecast.predict(bars(246))
        self.assertTrue(result["available"])
        self.assertEqual(result["training_samples"], 120)
        self.assertEqual(result["validation_samples"], 60)
        self.assertEqual(result["embargo_samples"], 5)

    def test_matured_labels_and_embargo_dates(self):
        history = bars()
        result = forecast.predict(history)
        for value in (result, result["next_day"]):
            self.assertTrue(value["available"])
            self.assertLess(value["training_label_end"], value["validation_feature_start"])
            self.assertLessEqual(value["validation_label_end"], value["as_of_date"])
            self.assertEqual(value["as_of_date"], history[-1]["date"])
            self.assertEqual(value["sample_count"], len(history) - 59 - value["horizon_days"])

    def test_validation_future_labels_cannot_change_training_fit(self):
        history = bars(320)
        original_fit = forecast._fit
        captured = []

        def record(samples):
            captured.append(copy.deepcopy(samples))
            return original_fit(samples)

        with patch.object(forecast, "_fit", side_effect=record):
            initial = forecast.predict(history)
        self.assertTrue(initial["available"])
        initial_training = captured[0]
        captured.clear()
        validation_start = initial["validation_feature_start"]
        changed = copy.deepcopy(history)
        for i, row in enumerate(changed):
            if row["date"] >= validation_start:
                row["close"] *= 1.0 + 0.001 * (i + 1)
        with patch.object(forecast, "_fit", side_effect=record):
            altered = forecast.predict(changed)
        self.assertTrue(altered["available"])
        self.assertEqual(initial_training, captured[0])
        self.assertNotEqual(initial["validation_mae_pct"], altered["validation_mae_pct"])

    def test_future_benchmark_values_are_ignored(self):
        history = bars(320)
        benchmark = bars(400, seed=90)
        initial = forecast.predict(history, benchmark)
        self.assertTrue(initial["benchmark_used"])
        for row in benchmark[320:]:
            row["close"] *= 30
        altered = forecast.predict(history, benchmark)
        self.assertEqual(initial, altered)

    def test_constant_history_is_finite_and_never_recommended(self):
        history = bars(300)
        for row in history:
            row["open"] = 10.0
            row["close"] = 10.0
            row["volume"] = 10000
        result = forecast.predict(history)
        for value in (result, result["next_day"]):
            self.assertTrue(value["available"])
            self.assertEqual(value["expected_return_pct"], 0)
            self.assertFalse(value["eligible"])
            self.assertIsNone(value["rank_value"])
            for key in ("validation_mae_pct", "interval_low_pct", "interval_high_pct"):
                self.assertTrue(math.isfinite(value[key]))

    def test_metrics_intervals_and_selection_are_consistent(self):
        result = forecast.predict(bars(500))
        for value in (result, result["next_day"]):
            self.assertTrue(value["available"])
            self.assertLessEqual(value["interval_low_pct"], value["interval_high_pct"])
            self.assertGreaterEqual(value["validation_mae_pct"], 0)
            self.assertTrue(0 <= value["validation_direction_accuracy_pct"] <= 100)
            if value["eligible"]:
                self.assertGreater(value["expected_return_pct"], 0)
                self.assertGreater(value["rank_value"], 0)
                self.assertGreaterEqual(value["validation_direction_accuracy_pct"], 52)
                self.assertLessEqual(value["validation_mae_pct"], value["baseline_mae_pct"])
            else:
                self.assertIsNone(value["rank_value"])

    def test_zero_volume_prevents_selection(self):
        history = bars(400)
        history[-1]["volume"] = 0
        result = forecast.predict(history)
        self.assertTrue(result["available"])
        self.assertFalse(result["eligible"])
        self.assertIn("最新日线无成交量", result["reason"])

    def test_invalid_or_duplicate_bars_do_not_become_predictions(self):
        history = bars(300)
        for invalid in ([], history + [history[-1]], [{"date": "bad", "close": 10}],
                        [{"date": "2024-01-01", "close": float("nan")} ]):
            result = forecast.predict(invalid)
            self.assertFalse(result["available"])
            self.assertFalse(result["eligible"])
            self.assertIsNone(result["expected_return_pct"])

    def test_primary_target_is_next_open_to_following_close_not_signal_close(self):
        history = bars(300)
        for row in history:
            row.update(open=20.0, close=10.0, volume=10000)
        result = forecast.predict(history)
        self.assertTrue(result["available"])
        self.assertEqual(result["horizon_days"], 2)
        self.assertEqual(result["expected_return_pct"], -50)
        self.assertEqual(result["next_day"]["expected_return_pct"], 0)
        self.assertEqual(result["return_definition"], "close[i+2] / open[i+1] - 1")
        self.assertEqual(result["entry_offset_sessions"], 1)
        self.assertEqual(result["exit_offset_sessions"], 2)
        self.assertTrue(result["cycle_includes_buy_day"])

    def test_missing_open_never_silently_uses_close(self):
        history = bars(300)
        history[100].pop("open")
        result = forecast.predict(history)
        self.assertFalse(result["available"])
        self.assertFalse(result["eligible"])
        self.assertIn("真实开盘价", result["reason"])

    def test_cycle_labels_use_next_two_sessions_across_weekend(self):
        history = bars(300)
        current = dt.date(2025, 1, 2)
        for row in history:
            while current.weekday() >= 5:
                current += dt.timedelta(days=1)
            row["date"] = current.isoformat()
            current += dt.timedelta(days=1)
        original_fit = forecast._fit
        captured = []

        def record(samples):
            captured.append(copy.deepcopy(samples))
            return original_fit(samples)

        with patch.object(forecast, "_fit", side_effect=record):
            result = forecast.predict(history)
        self.assertTrue(result["available"])
        sample = next(s for s in captured[1] if dt.date.fromisoformat(history[s["index"]]["date"]).weekday() == 3)
        i = sample["index"]
        self.assertEqual(dt.date.fromisoformat(history[i + 1]["date"]).weekday(), 4)
        self.assertEqual(dt.date.fromisoformat(history[i + 2]["date"]).weekday(), 0)
        self.assertAlmostEqual(sample["y"], (history[i + 2]["close"] / history[i + 1]["open"] - 1) * 100)

    def test_next_session_open_changes_label_but_not_signal_features(self):
        history = bars(300)
        prepared = forecast._prepare(history, require_open=True)
        original_features, _ = forecast._features(prepared, [])
        altered = copy.deepcopy(prepared)
        altered[100]["open"] *= 1.2
        changed_features, _ = forecast._features(altered, [])
        self.assertEqual(original_features, changed_features)
        original_label = history[101]["close"] / history[100]["open"] - 1
        changed_label = altered[101]["close"] / altered[100]["open"] - 1
        self.assertNotEqual(original_label, changed_label)

    def test_continuous_benchmark_keeps_all_original_labels(self):
        history = bars(320)
        original_fit, captured = forecast._fit, []
        def record(samples):
            captured.append(copy.deepcopy(samples))
            return original_fit(samples)
        with patch.object(forecast, '_fit', side_effect=record):
            result = forecast.predict(history, copy.deepcopy(history))
        for value in (result, result['next_day']):
            self.assertTrue(value['available'])
            self.assertEqual(value['session_alignment'], 'benchmark_sessions')
            self.assertEqual(value['sample_count'], len(history)-59-value['horizon_days'])
            self.assertEqual(value['dropped_nonconsecutive_samples'], 0)
        for sample in captured[1]:
            i = sample['index']
            self.assertAlmostEqual(sample['y'], (history[i+2]['close']/history[i+1]['open']-1)*100)

    def test_missing_d1_or_d2_cannot_turn_later_stock_rows_into_two_day_labels(self):
        benchmark = bars(400)
        history = copy.deepcopy(benchmark[:150] + benchmark[151:])
        original_fit, captured = forecast._fit, []
        def record(samples):
            captured.append(copy.deepcopy(samples))
            return original_fit(samples)
        with patch.object(forecast, '_fit', side_effect=record):
            result = forecast.predict(history, benchmark)
        self.assertTrue(result['available'])
        self.assertEqual(result['dropped_nonconsecutive_samples'], 2)
        self.assertEqual(result['next_day']['dropped_nonconsecutive_samples'], 1)
        self.assertEqual(result['sample_count'], len(history)-59-2-2)
        signal_dates = {history[s['index']]['date'] for s in captured[1]}
        self.assertNotIn(benchmark[148]['date'], signal_dates)  # missing D2
        self.assertNotIn(benchmark[149]['date'], signal_dates)  # missing D1
        self.assertIn(benchmark[151]['date'], signal_dates)

    def test_long_suspension_and_zero_volume_placeholders_never_form_mature_trade_labels(self):
        benchmark = bars(450)
        history = copy.deepcopy(benchmark[:150] + benchmark[200:])
        result = forecast.predict(history, benchmark)
        self.assertEqual(result['dropped_nonconsecutive_samples'], 2)
        self.assertEqual(result['next_day']['dropped_nonconsecutive_samples'], 1)
        placeholders = copy.deepcopy(benchmark)
        placeholders[150]['volume'] = 0
        result = forecast.predict(placeholders, benchmark)
        self.assertEqual(result['dropped_nonconsecutive_samples'], 0)
        self.assertEqual(result['dropped_untradable_samples'], 2)
        self.assertEqual(result['next_day']['dropped_untradable_samples'], 1)

    def test_explicit_empty_benchmark_fails_closed_but_omitted_benchmark_preserves_api(self):
        history = bars(320)
        assumed = forecast.predict(history)
        self.assertTrue(assumed['available'])
        self.assertEqual(assumed['session_alignment'], 'assumed_contiguous_input')
        strict = forecast.predict(history, [])
        self.assertFalse(strict['available'])
        self.assertFalse(strict['eligible'])
        self.assertEqual(strict['session_alignment'], 'benchmark_sessions')
        self.assertEqual(strict['sample_count'], 0)

    def test_gap_alignment_ignores_future_benchmark_rows_even_invalid_future_prices(self):
        benchmark = bars(450)
        history = copy.deepcopy(benchmark[:150] + benchmark[151:400])
        initial = forecast.predict(history, benchmark[:400])
        for row in benchmark[400:]:
            row['close'] = float('nan')
        self.assertEqual(initial, forecast.predict(history, benchmark))


if __name__ == "__main__":
    unittest.main()
