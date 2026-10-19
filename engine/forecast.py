"""Standard-library two-session-cycle return research.

Primary target after signal session S: buy D1 open, exit D2 close, where D1
and D2 are the next two benchmark sessions, including the buy session. Without
a benchmark, callers explicitly assume their supplied rows are consecutive.
This is not S-close to D2-close, not 48 hours, and not an intraday sale price.

Three consecutive 60-observation blocks are evaluated with expanding training
windows. Five feature dates are discarded before each block, so no fitting
outcome extends into its validation. The protocol and hyperparameters are fixed,
not selected on these blocks. These retrospective checks cannot establish future
accuracy, independence, or significance after selecting across many stocks.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import mean

MIN_TRAIN = 120
HOLDOUT = 60
EMBARGO = 5
RIDGE_ALPHA = 10.0
MODEL_VERSION = "ridge-cycle-v2"
VALIDATION_PROTOCOL = "purged-expanding-3x60-v1"
VALIDATION_FOLDS = 3
# An explicit research stress assumption, not a brokerage fee quotation. Small
# orders, slippage, taxes, price limits and unfilled orders can cost more.
ROUND_TRIP_COST_STRESS_PCT = 0.30
FEATURE_START = 59
BASE_FEATURES = ["5日涨跌幅", "20日涨跌幅", "收盘偏离MA20", "MA20偏离MA60", "20日波动率", "成交量对数比"]
METHOD = "固定参数岭回归；末3段各60个成熟样本按时间递增训练验证，每段训练至少120条并隔离5条；参数不按验证结果搜索。不足3段时仅显示研究。当前预测使用截至报告日已成熟样本拟合。"
WARNINGS = [
    "预期涨幅是统计模型估计，不是承诺、目标收益或上涨概率；价格预测准确性不能保证。",
    "预测范围来自历史验证误差的10%至90%分位数，未做独立概率校准，不是保证覆盖未来的置信区间。",
    "两交易日周期包含买入日：D1下一交易日开盘买入，D2再下一交易日处理；不表示持满48小时。",
    "主目标使用D1开盘至D2收盘，D2收盘仅作日线研究终点，不是09:00或其他盘中时刻的精确卖出价；实际买卖价格、窄区间未触及与不可成交会改变实际收益。",
    "相邻周期与市场状态相关；不重叠持有期也不等于独立样本。3段时间验证与52%方向筛选阈值不能证明统计显著性、未来上涨概率或长期有效。",
    "全市场比较大量股票会产生偶然优胜者；排名第一仅表示本模型当前相对得分最高，不能证明最值得买。",
    "模型使用当前下载的前复权历史；历史复权修订、股票选择偏差和市场状态变化可能降低实际效果。",
    "主预测和历史标签均为未扣成本收益；成本压力项固定减去0.30个百分点作研究筛选，不是实际收费或保证可成交净收益，小额佣金及滑点可能更高。",
]


def _target_metadata(horizon):
    if horizon == 2:
        return {"horizon_days": 2,
                "horizon": "两交易日买卖周期（下一交易日开盘至再下一交易日收盘）",
                "entry_basis": "D1：信号后下一交易日开盘价",
                "exit_basis": "D2：再下一交易日收盘价，仅作日线研究终点",
                "target_kind": "next_open_to_following_close",
                "return_definition": "close[i+2] / open[i+1] - 1",
                "entry_offset_sessions": 1, "exit_offset_sessions": 2,
                "holding_session_count": 2, "cycle_includes_buy_day": True}
    return {"horizon_days": horizon, "horizon": "下一交易日收盘方向（辅助参考）",
            "entry_basis": "信号日收盘，仅作方向参考",
            "exit_basis": "下一交易日收盘，仅作方向参考",
            "target_kind": "close_to_close_reference",
            "return_definition": f"close[i+{horizon}] / close[i] - 1",
            "entry_offset_sessions": 0, "exit_offset_sessions": horizon}


def _empty(horizon, reason, as_of=None, count=0):
    return {"available": False, "eligible": False, **_target_metadata(horizon),
            "model_version": MODEL_VERSION, "validation_protocol": VALIDATION_PROTOCOL,
            "validation_protocol_complete": False, "validation_folds": [],
            "as_of_date": as_of, "expected_return_pct": None,
            "interval_low_pct": None, "interval_high_pct": None,
            "sample_count": count, "training_samples": 0, "validation_samples": 0,
            "validation_mae_pct": None, "validation_direction_accuracy_pct": None,
            "rank_value": None, "confidence": "数据不足或不可用", "method": METHOD,
            "reason": reason, "warnings": [reason] + WARNINGS}


def _prepare(bars, require_open=False):
    output, seen = [], set()
    for bar in bars or []:
        day = dt.date.fromisoformat(str(bar.get("date", ""))[:10]).isoformat()
        close = float(bar["close"])
        opening = float(bar["open"]) if require_open else None
        volume = float(bar.get("volume", 0))
        if day in seen or not math.isfinite(close) or close <= 0 or not math.isfinite(volume) or volume < 0 or (require_open and (not math.isfinite(opening) or opening <= 0)):
            raise ValueError("日线包含重复日期、无效开收盘价或无效成交量")
        seen.add(day)
        output.append({"date": day, "open": opening, "close": close, "volume": volume})
    return sorted(output, key=lambda row: row["date"])


def _percentile(values, fraction):
    values = sorted(values)
    if not values:
        return 0.0
    location = (len(values) - 1) * fraction
    lower = int(location)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (location - lower)


def _features(rows, benchmark):
    closes = [row["close"] for row in rows]
    volume = [row["volume"] for row in rows]
    daily = [0.0] + [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(rows))]
    prefixes = []
    for values in (closes, volume, daily, [value * value for value in daily]):
        acc = [0.0]
        for value in values:
            acc.append(acc[-1] + value)
        prefixes.append(acc)
    pc, pv, pr, pr2 = prefixes
    by_date = {row["date"]: row["close"] for row in benchmark if row["date"] <= rows[-1]["date"]}
    use_benchmark = bool(benchmark) and all(rows[i]["date"] in by_date and rows[i - 20]["date"] in by_date
                                            for i in range(FEATURE_START, len(rows)))
    output = {}
    for i in range(FEATURE_START, len(rows)):
        ma20 = (pc[i + 1] - pc[i - 19]) / 20
        ma60 = (pc[i + 1] - pc[i - 59]) / 60
        average_volume = (pv[i] - pv[i - 20]) / 20
        average_return = (pr[i + 1] - pr[i - 19]) / 20
        variance = max(0.0, (pr2[i + 1] - pr2[i - 19]) / 20 - average_return * average_return)
        r5 = (closes[i] / closes[i - 5] - 1) * 100
        r20 = (closes[i] / closes[i - 20] - 1) * 100
        ratio = max(0.05, min(20.0, volume[i] / average_volume)) if average_volume else 1.0
        values = [r5, r20, (closes[i] / ma20 - 1) * 100,
                  (ma20 / ma60 - 1) * 100, math.sqrt(variance), math.log(ratio)]
        if use_benchmark:
            index_return = (by_date[rows[i]["date"]] / by_date[rows[i - 20]["date"]] - 1) * 100
            values.append(r20 - index_return)
        output[i] = values
    return output, use_benchmark


def _solve(matrix, vector):
    """Pivoted Gaussian elimination for the tiny positive-definite ridge system."""
    size = len(vector)
    augmented = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        denominator = augmented[column][column]
        if abs(denominator) < 1e-12:
            raise ValueError("回归矩阵无法稳定求解")
        for row in range(column + 1, size):
            factor = augmented[row][column] / denominator
            for other in range(column, size + 1):
                augmented[row][other] -= factor * augmented[column][other]
    result = [0.0] * size
    for row in range(size - 1, -1, -1):
        result[row] = (augmented[row][-1] - sum(augmented[row][j] * result[j] for j in range(row + 1, size))) / augmented[row][row]
    return result


def _fit(samples):
    dimension = len(samples[0]["x"])
    count = len(samples)
    centers = [sum(item["x"][j] for item in samples) / count for j in range(dimension)]
    scales = [max(1e-8, math.sqrt(sum((item["x"][j] - centers[j]) ** 2 for item in samples) / count)) for j in range(dimension)]
    target_center = mean(item["y"] for item in samples)
    matrix = [[0.0] * dimension for _ in range(dimension)]
    vector = [0.0] * dimension
    for sample in samples:
        x = [(sample["x"][j] - centers[j]) / scales[j] for j in range(dimension)]
        target = sample["y"] - target_center
        for j in range(dimension):
            vector[j] += x[j] * target
            for k in range(j, dimension):
                matrix[j][k] += x[j] * x[k]
    for j in range(dimension):
        for k in range(j):
            matrix[j][k] = matrix[k][j]
        matrix[j][j] += RIDGE_ALPHA
    targets = [sample["y"] for sample in samples]
    return {"center": centers, "scale": scales, "target_center": target_center,
            "weights": _solve(matrix, vector),
            "floor": _percentile(targets, 0.01), "ceiling": _percentile(targets, 0.99)}


def _estimate(model, values):
    estimate = model["target_center"] + sum(weight * (value - center) / scale
                                            for weight, value, center, scale in
                                            zip(model["weights"], values, model["center"], model["scale"]))
    # A fixed, training-only safeguard against far-out-of-domain extrapolation.
    return max(model["floor"], min(model["ceiling"], estimate))


def _direction(value):
    return 1 if value > 1e-10 else (-1 if value < -1e-10 else 0)


def _alignment_metadata(benchmark, gaps=0, untradable=0):
    strict = benchmark is not None
    return {"session_alignment": "benchmark_sessions" if strict else "assumed_contiguous_input",
            "session_alignment_note": "成熟标签逐日对齐沪指后续交易日；缺失D1/D2或零成交量标签已剔除。" if strict else "未提供交易日基准，假定输入日线连续；未核验缺失交易日。",
            "dropped_nonconsecutive_samples": gaps, "dropped_untradable_samples": untradable}


def _validation_metrics(observations):
    """Summarize genuine out-of-fit estimates, with training-only baselines."""
    mae = mean(abs(item["residual"]) for item in observations)
    median_mae = mean(abs(item["y"] - item["baseline"]) for item in observations)
    zero_mae = mean(abs(item["y"]) for item in observations)
    return {"samples": len(observations), "mae_pct": round(mae, 3),
            "direction_accuracy_pct": round(mean(_direction(item["y"]) == _direction(item["estimate"]) for item in observations) * 100, 2),
            "baseline_mae_pct": round(median_mae, 3), "zero_baseline_mae_pct": round(zero_mae, 3),
            "baseline_direction_accuracy_pct": round(mean(_direction(item["y"]) == _direction(item["baseline"]) for item in observations) * 100, 2),
            "mae_skill_pct": round((1 - mae / median_mae) * 100, 2) if median_mae > 1e-10 else 0.0,
            "beats_both_baselines": median_mae > 1e-10 and zero_mae > 1e-10 and mae < median_mae and mae < zero_mae}


def _evaluate_fold(train, validation, rows, horizon):
    model = _fit(train)
    baseline = _percentile([item["y"] for item in train], 0.5)
    observations = []
    for item in validation:
        estimate = _estimate(model, item["x"])
        observations.append({**item, "estimate": estimate, "baseline": baseline,
                             "residual": item["y"] - estimate})
    return observations, {**_validation_metrics(observations),
        "training_samples": len(train), "embargo_samples": EMBARGO,
        "training_feature_start": rows[train[0]["index"]]["date"],
        "training_feature_end": rows[train[-1]["index"]]["date"],
        "training_label_end": rows[train[-1]["index"] + horizon]["date"],
        "feature_start": rows[validation[0]["index"]]["date"],
        "feature_end": rows[validation[-1]["index"]]["date"],
        "label_end": rows[validation[-1]["index"] + horizon]["date"]}


def _nonoverlapping(observations, horizon):
    """Greedily retain cycles that do not share a holding-session return.

    This removes direct target overlap, not serial or cross-market dependence.
    For D1/D2, an entry must occur after the previous D2 close. Auxiliary
    close-to-close returns may share the endpoint price, but no return interval.
    """
    selected, previous_exit = [], -1
    for item in observations:
        entry = item["index"] + (1 if horizon == 2 else 0)
        allowed = entry > previous_exit if horizon == 2 else entry >= previous_exit
        if allowed:
            selected.append(item)
            previous_exit = item["index"] + horizon
    return selected


def _latest_window_aligned(rows, benchmark):
    if benchmark is None:
        return None
    dates = [row["date"] for row in benchmark]
    try:
        end = dates.index(rows[-1]["date"]) + 1
    except ValueError:
        return False
    return end >= 60 and [row["date"] for row in rows[-60:]] == dates[end - 60:end]


def _horizon(rows, features, use_benchmark, horizon, benchmark=None):
    sessions = {row["date"]: i for i, row in enumerate(benchmark or [])}
    samples, gaps, untradable = [], 0, 0
    for i in range(FEATURE_START, len(rows) - horizon):
        if benchmark is not None:
            position = sessions.get(rows[i]["date"])
            if position is None or any(position + step >= len(benchmark) or
                    rows[i + step]["date"] != benchmark[position + step]["date"] for step in range(1, horizon + 1)):
                gaps += 1
                continue
            if any(rows[i + step]["volume"] <= 0 for step in range(1, horizon + 1)):
                untradable += 1
                continue
        samples.append({"index": i, "x": features[i],
                        "y": (rows[i + horizon]["close"] / (rows[i + 1]["open"] if horizon == 2 else rows[i]["close"]) - 1) * 100})
    alignment = _alignment_metadata(benchmark, gaps, untradable)
    minimum = MIN_TRAIN + EMBARGO + HOLDOUT
    if len(samples) < minimum:
        return {**_empty(horizon, f"{horizon}日预测至少需要{minimum}个有效成熟周期，以满足120训练样本、5个隔离样本与60验证样本；当前{len(samples)}个（{len(rows)}根日线；日期不连续剔除{gaps}、无成交剔除{untradable}）。",
                         rows[-1]["date"], len(samples)), **alignment}
    validation_start = len(samples) - HOLDOUT
    train = samples[:validation_start - EMBARGO]
    validation = samples[validation_start:]
    # Evaluate the most recent block first. The final fit is never passed into
    # any historical fold; each older fold gets only its own earlier outcomes.
    latest_observations, latest_fold = _evaluate_fold(train, validation, rows, horizon)
    final_model = _fit(samples)
    expected = _estimate(final_model, features[len(rows) - 1])
    evaluated = [(latest_observations, latest_fold)]
    for offset in range(2, VALIDATION_FOLDS + 1):
        start = len(samples) - HOLDOUT * offset
        if start - EMBARGO < MIN_TRAIN:
            continue
        earlier_train = samples[:start - EMBARGO]
        earlier_validation = samples[start:start + HOLDOUT]
        evaluated.append(_evaluate_fold(earlier_train, earlier_validation, rows, horizon))
    evaluated.reverse()
    observations = [item for values, _ in evaluated for item in values]
    folds = [dict(metrics, fold=number + 1) for number, (_, metrics) in enumerate(evaluated)]
    complete = len(folds) == VALIDATION_FOLDS
    aggregate = _validation_metrics(observations)
    nonoverlap_observations = _nonoverlapping(observations, horizon)
    nonoverlap = _validation_metrics(nonoverlap_observations)
    nonoverlap.update({"feature_start": rows[nonoverlap_observations[0]["index"]]["date"],
                       "feature_end": rows[nonoverlap_observations[-1]["index"]]["date"],
                       "label_end": rows[nonoverlap_observations[-1]["index"] + horizon]["date"],
                       "method": "按时间顺序取不重叠持有期；仅诊断标签重叠，未消除市场相关或多股票筛选偏差"})
    residuals = [item["residual"] for item in observations]
    latest_residuals = [item["residual"] for item in latest_observations]
    mae = mean(abs(value) for value in latest_residuals)
    accuracy = latest_fold["direction_accuracy_pct"]
    baseline_mae = mean(abs(item["y"] - item["baseline"]) for item in latest_observations)
    baseline_accuracy = latest_fold["baseline_direction_accuracy_pct"]
    skill = 1 - mae / baseline_mae if baseline_mae > 1e-10 else 0.0
    residual_low, residual_high = _percentile(residuals, 0.1), _percentile(residuals, 0.9)
    interval_low, interval_high = expected + residual_low, expected + residual_high
    # Older residuals are available for this diagnostic only when their labels
    # mature strictly before the final validation block begins. Do not calibrate
    # on the same 60 outcomes whose empirical coverage is reported.
    latest_first_index = latest_observations[0]["index"]
    calibration = [item for item in observations if item["index"] + horizon < latest_first_index]
    coverage = None
    if calibration:
        low = _percentile([item["residual"] for item in calibration], 0.1)
        high = _percentile([item["residual"] for item in calibration], 0.9)
        coverage = {"calibration_samples": len(calibration),
                    "calibration_feature_start": rows[calibration[0]["index"]]["date"],
                    "calibration_label_end": rows[calibration[-1]["index"] + horizon]["date"],
                    "evaluation_feature_start": latest_fold["feature_start"],
                    "evaluation_label_end": latest_fold["label_end"],
                    "evaluation_samples": len(latest_observations),
                    "empirical_coverage_pct": round(mean(low <= item["residual"] <= high for item in latest_observations) * 100, 2),
                    "interval_width_pct": round(high - low, 3),
                    "method": "仅用末段开始前已成熟的较早段残差10%至90%分位数，衡量末60条经验覆盖；非未来覆盖概率，非推荐门槛"}
    effective_samples = max(1, len(samples) // horizon)
    sampling_penalty = 2.0 / math.sqrt(effective_samples)
    gross_rank_score = expected - mae * 0.75 - max(0.0, -interval_low) * 0.25 - sampling_penalty
    rank_score = gross_rank_score - ROUND_TRIP_COST_STRESS_PCT
    current_z = max(abs((value - center) / scale) for value, center, scale in
                    zip(features[len(rows) - 1], final_model["center"], final_model["scale"]))
    failures = []
    if not complete:
        failures.append(f"完整{VALIDATION_FOLDS}段时间验证至少需要{MIN_TRAIN + EMBARGO + HOLDOUT * VALIDATION_FOLDS}个成熟周期；当前仅有{len(folds)}段，数值仅供研究")
    if expected <= 0:
        failures.append("模型预期收益不为正")
    if not latest_fold["beats_both_baselines"]:
        failures.append("最新60条验证误差未同时优于训练期中位数和零收益基准")
    if not aggregate["beats_both_baselines"]:
        failures.append("合并时间验证误差未同时优于训练期中位数和零收益基准")
    if sum(fold["beats_both_baselines"] for fold in folds) < 2:
        failures.append("至少2段验证同时优于两个简单基准的稳定性检查未通过")
    if accuracy < 52 or accuracy < baseline_accuracy:
        failures.append("时间验证方向准确率未达到52%或低于简单基准")
    if nonoverlap["direction_accuracy_pct"] < 52 or nonoverlap["direction_accuracy_pct"] < nonoverlap["baseline_direction_accuracy_pct"]:
        failures.append("不重叠持有期验证方向命中率未达到52%或低于逐段训练中位数基准；该阈值不代表统计显著性")
    if rank_score <= 0:
        failures.append("扣除误差、样本惩罚及0.30个百分点成本压力后的排名值不为正")
    if current_z > 4:
        failures.append("当前特征偏离训练分布超过4个标准差")
    if rows[-1]["volume"] <= 0:
        failures.append("最新日线无成交量")
    latest_window_aligned = _latest_window_aligned(rows, benchmark)
    if latest_window_aligned is False:
        failures.append("最近60根特征日线未连续对齐基准交易日，暂停推荐")
    eligible = not failures
    return {
        "available": True, "eligible": eligible, **_target_metadata(horizon), **alignment,
        "model_version": MODEL_VERSION, "validation_protocol": VALIDATION_PROTOCOL,
        "validation_protocol_complete": complete,
        "validation_folds": folds, "validation_aggregate": aggregate,
        "validation_nonoverlapping": nonoverlap, "interval_validation": coverage,
        "latest_feature_window_aligned": latest_window_aligned,
        "as_of_date": rows[-1]["date"],
        "expected_return_pct": round(expected, 3), "interval_low_pct": round(interval_low, 3),
        "interval_high_pct": round(interval_high, 3), "sample_count": len(samples),
        "training_samples": len(train), "validation_samples": len(validation), "embargo_samples": EMBARGO,
        "validation_mae_pct": round(mae, 3), "validation_direction_accuracy_pct": round(accuracy, 2),
        "baseline_mae_pct": round(baseline_mae, 3), "baseline_direction_accuracy_pct": round(baseline_accuracy, 2),
        "zero_baseline_mae_pct": latest_fold["zero_baseline_mae_pct"],
        "baseline_method": "固定使用训练样本未来收益的中位数；没有读取验证标签拟合基准",
        "validation_mae_skill_pct": round(skill * 100, 2),
        "validation_feature_start": rows[validation[0]["index"]]["date"],
        "validation_feature_end": rows[validation[-1]["index"]]["date"],
        "validation_label_end": rows[validation[-1]["index"] + horizon]["date"],
        "training_label_end": rows[train[-1]["index"] + horizon]["date"],
        "rank_value": round(rank_score, 3) if eligible else None,
        "raw_rank_value": round(rank_score, 3), "sampling_penalty_pct": round(sampling_penalty, 3),
        "gross_raw_rank_value": round(gross_rank_score, 3),
        "cost_stress_return_pct": round(expected - ROUND_TRIP_COST_STRESS_PCT, 3),
        "round_trip_cost_stress_pct": ROUND_TRIP_COST_STRESS_PCT,
        "cost_stress_method": "固定每回合0.30个百分点研究压力，不是实际佣金税费，未模拟成交、小额最低佣金或涨跌停；实际成本可能更高",
        "rank_formula": "未扣成本预期涨幅 − 0.75×最新段验证MAE − 0.25×历史误差范围下沿亏损幅度 − 2/√(成熟样本数/期限) − 0.30个百分点成本压力",
        "confidence": "通过研究筛选，仍非确定预测" if eligible else "未通过预测筛选", "reason": "；".join(failures) if failures else "通过3段时间验证、双基准、方向、正预期及成本压力研究筛选；不代表未来有效",
        "method": METHOD, "features": BASE_FEATURES + (["相对沪指20日收益"] if use_benchmark else []),
        "benchmark_used": use_benchmark, "ridge_alpha": RIDGE_ALPHA,
        "prediction_clipping": "预测限制在拟合样本收益的1%至99%分位数内，不能消除模型错误",
        "interval_method": f"全部{len(observations)}个已成熟时间验证样本的残差10%至90%分位数，加到当前预测上；未校准未来覆盖概率",
        "warnings": failures + WARNINGS,
    }


def predict(bars, benchmark_bars=None):
    """Forecast D1-open to D2-close return, plus auxiliary ``next_day`` direction.

    ``available`` means the model can report a number. Only ``eligible`` entries
    may be used as research candidates, and callers must additionally enforce
    freshness, security exclusions, and execution restrictions. No probability
    or guarantee is represented by ``expected_return_pct`` or ``confidence``.
    A supplied benchmark defines exact D1/D2 sessions; missing rows or zero-volume
    trade legs are omitted. Without one, input continuity is an explicit assumption.
    """
    try:
        rows = _prepare(bars, require_open=True)
        # Future benchmark rows cannot affect either features or matured labels.
        benchmark = _prepare([bar for bar in (benchmark_bars or []) if not rows or str(bar.get("date", ""))[:10] <= rows[-1]["date"]]) if benchmark_bars is not None else None
    except (TypeError, KeyError, ValueError, OverflowError) as exc:
        result = _empty(2, "日线校验失败（两交易日周期要求真实开盘价）：" + str(exc))
        result["next_day"] = _empty(1, result["reason"])
        result.update(_alignment_metadata([] if benchmark_bars is not None else None))
        result["next_day"].update(_alignment_metadata([] if benchmark_bars is not None else None))
        return result
    if len(rows) <= FEATURE_START:
        reason = "有效日线不足60根，无法形成模型特征。"
        result = _empty(2, reason, rows[-1]["date"] if rows else None)
        result["next_day"] = _empty(1, reason, result["as_of_date"])
        result.update(_alignment_metadata(benchmark))
        result["next_day"].update(_alignment_metadata(benchmark))
        return result
    try:
        features, use_benchmark = _features(rows, benchmark or [])
        result = _horizon(rows, features, use_benchmark, 2, benchmark)
        result["next_day"] = _horizon(rows, features, use_benchmark, 1, benchmark)
        return result
    except (ArithmeticError, ValueError) as exc:
        result = _empty(2, "模型无法稳定计算：" + str(exc), rows[-1]["date"])
        result["next_day"] = _empty(1, result["reason"], result["as_of_date"])
        result.update(_alignment_metadata(benchmark))
        result["next_day"].update(_alignment_metadata(benchmark))
        return result
