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

    def test_protocol_version_exists_even_when_unavailable_or_insufficient_folds(self):
        for history in ([], bars(246), bars(365)):
            result = forecast.predict(history)
            for value in (result, result['next_day']):
                self.assertEqual(value['model_version'], 'ridge-cycle-v2')
                self.assertEqual(value['validation_protocol'], 'purged-expanding-3x60-v1')
            self.assertFalse(result['validation_protocol_complete'])
            self.assertFalse(result['eligible'])
        boundary = forecast.predict(bars(366))
        self.assertTrue(boundary['available'])
        self.assertTrue(boundary['validation_protocol_complete'])
        self.assertEqual(boundary['validation_folds'][0]['training_samples'], 120)

    def test_three_folds_are_disjoint_expanding_and_purged_with_matured_dates(self):
        result = forecast.predict(bars(500))
        for value in (result, result['next_day']):
            folds = value['validation_folds']
            self.assertEqual(len(folds), 3)
            self.assertEqual(value['validation_aggregate']['samples'], 180)
            previous = None
            for fold in folds:
                self.assertEqual(fold['samples'], 60)
                self.assertEqual(fold['embargo_samples'], 5)
                self.assertLess(fold['training_label_end'], fold['feature_start'])
                self.assertLessEqual(fold['label_end'], value['as_of_date'])
                self.assertGreaterEqual(fold['training_samples'], 120)
                if previous:
                    self.assertLess(previous['feature_end'], fold['feature_start'])
                    self.assertGreater(fold['training_samples'], previous['training_samples'])
                previous = fold

    def test_each_fold_fit_is_invariant_to_its_validation_and_future_prices(self):
        history = bars(500)
        # Model fit order is latest, current, middle, oldest. Current fit may
        # consume all matured outcomes; the three OOS fits must not do so.
        original_fit = forecast._fit
        def fits_for(rows):
            captured = []
            def capture(samples):
                captured.append(copy.deepcopy(samples))
                return original_fit(samples)
            with patch.object(forecast, '_fit', side_effect=capture):
                prediction = forecast.predict(rows)
            return prediction, captured
        original, captures = fits_for(history)
        for fold, fit_position in zip(original['validation_folds'], (3, 2, 0)):
            altered = copy.deepcopy(history)
            for row in altered:
                if row['date'] >= fold['feature_start']:
                    row['close'] *= 1.15
                    row['open'] *= 1.05
            _, changed_captures = fits_for(altered)
            self.assertEqual(captures[fit_position], changed_captures[fit_position])

    def test_nonoverlap_diagnostic_uses_disjoint_holding_sessions(self):
        observations = [{'index': i} for i in range(20)]
        selected = forecast._nonoverlapping(observations, 2)
        self.assertEqual([item['index'] for item in selected], list(range(0, 20, 2)))
        for left, right in zip(selected, selected[1:]):
            self.assertGreater(right['index'] + 1, left['index'] + 2)
        # Consecutive close-to-close intervals share no daily return increment.
        self.assertEqual(forecast._nonoverlapping(observations, 1), observations)
        result = forecast.predict(bars(500))
        self.assertEqual(result['validation_nonoverlapping']['samples'], 90)
        self.assertEqual(result['next_day']['validation_nonoverlapping']['samples'], 180)

    def test_interval_coverage_uses_only_already_mature_earlier_fold_residuals(self):
        result = forecast.predict(bars(500))
        for value in (result, result['next_day']):
            coverage = value['interval_validation']
            self.assertEqual(coverage['calibration_samples'], 120 - value['horizon_days'])
            self.assertLess(coverage['calibration_label_end'], coverage['evaluation_feature_start'])
            self.assertEqual(coverage['evaluation_samples'], 60)
            self.assertTrue(0 <= coverage['empirical_coverage_pct'] <= 100)
            self.assertEqual(coverage['evaluation_label_end'], value['as_of_date'])

    def test_good_latest_fold_alone_cannot_pass_bad_earlier_regimes(self):
        history = bars(500)
        for i, row in enumerate(history):
            row.update(open=1.0, close=1.01 if i % 2 else 0.99, volume=10000)
        rows = forecast._prepare(history, require_open=True)
        samples = len(rows) - forecast.FEATURE_START - 2
        latest_train_count = samples - forecast.HOLDOUT - forecast.EMBARGO
        features = {i: [i] for i in range(forecast.FEATURE_START, len(rows))}
        def fit(items):
            return {'count': len(items), 'center': [250.0], 'scale': [1000.0]}
        def estimate(model, values):
            i = values[0]
            if model['count'] == samples:
                return 3.0
            actual = (rows[i + 2]['close'] - 1) * 100
            return actual if model['count'] == latest_train_count else -actual
        with patch.object(forecast, '_fit', side_effect=fit), patch.object(forecast, '_estimate', side_effect=estimate):
            result = forecast._horizon(rows, features, False, 2)
        self.assertTrue(result['validation_folds'][-1]['beats_both_baselines'])
        self.assertEqual(sum(f['beats_both_baselines'] for f in result['validation_folds']), 1)
        self.assertGreater(result['raw_rank_value'], 0)
        self.assertFalse(result['eligible'])
        self.assertIn('稳定性检查未通过', result['reason'])
        self.assertIn('合并时间验证误差', result['reason'])

    def test_cost_stress_is_explicit_and_subtracted_from_rank_without_relabeling_target(self):
        result = forecast.predict(bars(500))
        for value in (result, result['next_day']):
            self.assertEqual(value['round_trip_cost_stress_pct'], 0.30)
            self.assertAlmostEqual(value['cost_stress_return_pct'], value['expected_return_pct'] - 0.30, places=3)
            self.assertAlmostEqual(value['raw_rank_value'], value['gross_raw_rank_value'] - 0.30, places=3)
            self.assertIn('不是实际佣金税费', value['cost_stress_method'])
        self.assertEqual(result['target_kind'], 'next_open_to_following_close')

    def test_gap_in_current_feature_window_prevents_recommendation(self):
        benchmark = bars(500)
        history = copy.deepcopy(benchmark[:480] + benchmark[481:])
        result = forecast.predict(history, benchmark)
        self.assertTrue(result['available'])
        self.assertTrue(result['validation_protocol_complete'])
        self.assertFalse(result['latest_feature_window_aligned'])
        self.assertFalse(result['eligible'])
        self.assertIn('最近60根特征日线未连续对齐', result['reason'])
        old_gap = copy.deepcopy(benchmark[:150] + benchmark[151:])
        self.assertTrue(forecast.predict(old_gap, benchmark)['latest_feature_window_aligned'])


if __name__ == "__main__":
    unittest.main()
