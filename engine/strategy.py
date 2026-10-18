"""Transparent daily-bar research signals and a conservative paper backtest.

Prices supplied by the data layer are forward-adjusted (qfq).  This module never
sends orders.  Scores are rules-based ranks, not probabilities or promises.
"""
from __future__ import annotations

import math
from statistics import mean, median


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _bars(rows):
    """Validate, deduplicate and sort without modifying the caller's records."""
    result = {}
    for row in rows or []:
        date = str(row.get("date", ""))[:10]
        close = _number(row.get("close"))
        if len(date) != 10 or close <= 0:
            continue
        opening = _number(row.get("open"), close)
        high = _number(row.get("high"), close)
        low = _number(row.get("low"), close)
        if min(opening, high, low) <= 0 or high < max(opening, close, low) or low > min(opening, close):
            continue
        item = dict(row)
        item.update(date=date, open=opening, high=high, low=low, close=close,
                    volume=max(0.0, _number(row.get("volume"))))
        result[date] = item
    return [result[key] for key in sorted(result)]


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = mean(max(0.0, value) for value in changes[:period])
    loss = mean(max(0.0, -value) for value in changes[:period])
    for value in changes[period:]:
        gain = (gain * (period - 1) + max(0.0, value)) / period
        loss = (loss * (period - 1) + max(0.0, -value)) / period
    if gain == 0 and loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def _atr(rows, period=14):
    if not rows:
        return 0.0
    ranges = []
    for i, row in enumerate(rows):
        previous = rows[i - 1]["close"] if i else row["close"]
        ranges.append(max(row["high"] - row["low"], abs(row["high"] - previous),
                          abs(row["low"] - previous)))
    if len(ranges) <= period:
        return mean(ranges)
    value = mean(ranges[:period])
    for current in ranges[period:]:
        value = (value * (period - 1) + current) / period
    return value


def _index(symbol):
    return str(symbol).lower() in {"sh000001", "000001.sh", "sse", "sse000001", "沪指"}


def _restricted(symbol, name):
    normalized = str(name).upper().replace(" ", "")
    return _index(symbol) or "ST" in normalized or "退" in normalized


def _frame(rows, symbol, name, benchmark=None):
    if not rows:
        raise ValueError("没有有效日线数据，不能计算推荐分。")
    closes = [row["close"] for row in rows]
    close = closes[-1]
    ma5, ma20, ma60 = [mean(closes[-span:]) for span in (5, 20, 60)]
    previous_ma20 = mean(closes[-25:-5]) if len(rows) >= 25 else ma20
    momentum20 = (close / closes[-21] - 1) * 100 if len(rows) >= 21 else 0.0
    momentum5 = (close / closes[-6] - 1) * 100 if len(rows) >= 6 else 0.0
    change = (close / closes[-2] - 1) * 100 if len(rows) >= 2 else 0.0
    rsi = _rsi(closes)
    atr = _atr(rows)
    atr_pct = atr / close * 100
    average_volume = mean(row["volume"] for row in rows[-21:-1]) if len(rows) >= 2 else 0.0
    volume_ratio = rows[-1]["volume"] / average_volume if average_volume else 0.0
    trend = (10 if close > ma20 else 0) + (8 if ma5 > ma20 else 0) + (8 if ma20 > ma60 else 0) + (4 if ma20 > previous_ma20 else 0)
    momentum = max(0.0, min(12.0, momentum20 * 1.2))
    momentum += 8 if 0 < momentum5 <= 5 else (4 if 5 < momentum5 <= 10 else (2 if momentum5 > 10 else 0))
    rsi_score = 15 if 45 <= rsi <= 65 else (10 if 35 <= rsi < 45 or 65 < rsi <= 72 else (4 if 25 <= rsi < 35 or 72 < rsi < 80 else 0))
    relative, relative_detail, excess = 7.5, "缺少可对齐的基准日线，给予中性分。", None
    if benchmark:
        by_date = {row["date"]: row["close"] for row in benchmark}
        if len(rows) >= 21 and rows[-1]["date"] in by_date and rows[-21]["date"] in by_date:
            benchmark_momentum = (by_date[rows[-1]["date"]] / by_date[rows[-21]["date"]] - 1) * 100
            excess = momentum20 - benchmark_momentum
            relative = max(0.0, min(15.0, 7.5 + excess * 0.75))
            relative_detail = f"近20个交易日相对沪指收益 {excess:+.2f} 个百分点。"
    if _index(symbol):
        relative = 7.5
        relative_detail = "指数不与自身比较，给予中性分。"
    volume_score = (10 if 1.1 <= volume_ratio <= 2.5 else (6 if 0.8 <= volume_ratio < 1.1 else 3)) if change > 0 else 2
    if not average_volume or not rows[-1]["volume"]:
        volume_score = 0
    risk = max(0.0, min(10.0, 12.0 - atr_pct * 2))
    factors = [
        {"name": "趋势", "score": trend, "max": 30, "detail": f"MA5 {ma5:.2f} / MA20 {ma20:.2f} / MA60 {ma60:.2f}；均线位置与方向。"},
        {"name": "动量", "score": round(momentum, 2), "max": 20, "detail": f"20日涨跌 {momentum20:+.2f}%，5日涨跌 {momentum5:+.2f}%；过热不加分。"},
        {"name": "RSI", "score": rsi_score, "max": 15, "detail": f"14日 RSI {rsi:.1f}；45至65区间得分较高。"},
        {"name": "相对强度", "score": round(relative, 2), "max": 15, "detail": relative_detail},
        {"name": "量价", "score": volume_score, "max": 10, "detail": f"当日成交量为前20日均量的 {volume_ratio:.2f} 倍。"},
        {"name": "波动风险", "score": round(risk, 2), "max": 10, "detail": f"14日 ATR 占收盘价 {atr_pct:.2f}%；波动越大，分数越低。"},
    ]
    score = round(sum(factor["score"] for factor in factors), 1)
    eligible = len(rows) >= 120 and rows[-1]["volume"] > 0 and score >= 70 and not _restricted(symbol, name)
    warnings = ["推荐分是公开规则评分，不是上涨概率；点位仅为收盘后研究参考，不能保证成交或收益。"]
    if len(rows) < 120:
        warnings.append(f"有效日线仅 {len(rows)} 条，至少120条才进入可选股票池。")
    if _index(symbol):
        warnings.append("沪指为不可直接买卖的指数；所列点位仅用于市场趋势观察。")
    elif _restricted(symbol, name):
        warnings.append("名称含 ST 或退市标记，不进入可选股票池。")
    if not rows[-1]["volume"]:
        warnings.append("最新日线没有成交量，按停牌或无成交数据处理。")
    if rsi >= 80:
        warnings.append("RSI处于高位，存在短期追涨风险。")
    if atr_pct >= 5:
        warnings.append("近期波动较大，止损可能因跳空或跌停无法按参考价执行。")
    if abs(change) > 25:
        warnings.append("单日价格变化异常，请先核对复权、除权或上市日期。")
        eligible = False
    # Preserve a nonzero range even for perfectly flat histories.
    unit = max(atr, close * 0.005, 0.01)
    low = max(0.01, min(close, ma5) - unit * 0.35)
    high = max(low + 0.01, min(close + unit * 0.15, max(close, ma5) + unit * 0.1))
    stop = max(0.01, low - unit * 1.5)
    target = high + 2 * (high - stop)
    position = min(10.0, 0.005 / ((high - stop) / high) * 100) if high > stop else 0.0
    if not eligible:
        position = 0.0
    signal = "重点观察" if score >= 75 else ("观察候选" if score >= 65 else ("中性观察" if score >= 45 else "谨慎回避"))
    if not eligible and not _index(symbol):
        signal = "暂不纳入"
    raw_close = _number(rows[-1].get("raw_close"), close)
    ratio = raw_close / close if raw_close > 0 else 1.0
    if rows[-1].get("raw_close") is None:
        warnings.append("缺少未复权收盘价；参考点位暂按最新前复权价格，交易前必须核对。")
    reasons = [factor["detail"] for factor in factors if factor["score"] >= factor["max"] * 0.7]
    if not reasons:
        reasons = ["当前没有足够强的趋势、动量与量价共振。"]
    return {
        "symbol": symbol, "name": name, "date": rows[-1]["date"], "bar_count": len(rows),
        "close": round(close, 4), "raw_close": round(raw_close, 4),
        "change_pct": round(change, 2), "score": score, "signal": signal,
        "factors": factors, "ma5": round(ma5, 4), "ma20": round(ma20, 4),
        "ma60": round(ma60, 4), "rsi14": round(rsi, 2), "atr14": round(atr, 4),
        "momentum20": round(momentum20, 2), "volume_ratio": round(volume_ratio, 2),
        "entry_low": round(low * ratio, 2) if eligible else None,
        "entry_high": round(high * ratio, 2) if eligible else None,
        "stop_loss": round(stop * ratio, 2) if eligible else None,
        "take_profit": round(target * ratio, 2) if eligible else None,
        "max_position_pct": round(position, 1), "reasons": reasons, "warnings": warnings,
        "eligible": eligible, "price_basis": "均线、ATR与close为前复权研究价；买入/止损/止盈点位按raw_close比例转换为最新未复权基准，交易前仍须核对实时报价",
    }


def _state(rows, end):
    closes = [row["close"] for row in rows[end - 59:end + 1]]
    ma20, ma60 = mean(closes[-20:]), mean(closes)
    trend = 1 if closes[-1] > ma20 > ma60 else (-1 if closes[-1] < ma20 < ma60 else 0)
    momentum = 1 if closes[-1] > closes[-21] else (-1 if closes[-1] < closes[-21] else 0)
    return trend, momentum


def _forecast(rows, score):
    output = {
        "horizon": "两交易日买卖周期（D1开盘至D2收盘）", "label": "偏强观察" if score >= 65 else ("震荡观察" if score >= 45 else "偏弱观察"),
        "method": "六因子规则给出方向观察；同类历史按均线排列与20日动量方向匹配，仅统计已完成下一交易日开盘至再下一交易日收盘的样本。",
        "entry_basis": "信号后下一交易日开盘", "exit_basis": "再下一交易日收盘，非14:50精确成交价",
        "sample_count": 0, "up_rate_pct": None, "median_return_pct": None,
        "note": "不输出未经校准的预测概率；重叠历史窗口并非独立样本，统计不能证明未来收益。",
        "as_of_date": rows[-1]["date"] if rows else None,
    }
    if len(rows) < 62:
        return output
    state = _state(rows, len(rows) - 1)
    returns = []
    # Every outcome ends on or before the as-of bar.  No future labels enter.
    for end in range(59, len(rows) - 2):
        if _state(rows, end) == state:
            returns.append((rows[end + 2]["close"] / rows[end + 1]["open"] - 1) * 100)
    output["sample_count"] = len(returns)
    if len(returns) >= 30:
        output["up_rate_pct"] = round(sum(value > 0 for value in returns) / len(returns) * 100, 1)
        output["median_return_pct"] = round(median(returns), 2)
    else:
        output["note"] = "同类已成熟样本不足30条，不展示历史上涨占比。" + output["note"]
    return output


def analyze(bars, symbol, name, benchmark_bars=None):
    """Analyze only supplied bars; caller must supply completed daily sessions."""
    rows = _bars(bars)
    result = _frame(rows, symbol, name, _bars(benchmark_bars))
    result["forecast"] = _forecast(rows, result["score"])
    return result


def _board(symbol, name=""):
    code = "".join(character for character in str(symbol) if character.isdigit())
    if "ST" in str(name).upper():
        return 0.05, 100
    if code.startswith("688"):
        return 0.20, 200
    if code.startswith(("300", "301")):
        return 0.20, 100
    if str(symbol).lower().startswith("bj") or code.startswith(("4", "8", "920")):
        return 0.30, 100
    return 0.10, 100


def backtest(bars, symbol, initial_cash=100000, commission_rate=0.0003,
             stamp_tax_rate=0.0005, slippage=0.001, position_pct=20.0,
             entry_score=70, exit_score=45, lot_size=None, name="",
             max_holding_days=20, transfer_fee_rate=0.00001):
    """Close-generated signals execute at the next open, with T+1 and fees.

    This is an illustrative single-position, long-only strategy, not a broker
    simulator.  It does not trade indices or ST/delisting-marked instruments.
    Stops and targets are checked at the close and can only exit the next open.
    """
    initial_cash = _number(initial_cash)
    if initial_cash <= 0:
        raise ValueError("初始资金必须大于0。")
    for label, value in (("佣金", commission_rate), ("印花税", stamp_tax_rate),
                         ("滑点", slippage), ("过户费", transfer_fee_rate)):
        if not math.isfinite(float(value)) or not 0 <= float(value) < 1:
            raise ValueError(f"{label}参数必须在0（含）到1之间。")
    if not 0 < position_pct <= 100 or max_holding_days < 1:
        raise ValueError("仓位必须大于0且不超过100%；持仓期限至少1个交易日。")
    rows = _bars(bars)
    if len(rows) < 121:
        raise ValueError("回测至少需要121条有效日线（前120条用于指标预热与新股过滤）。")
    limit, default_lot = _board(symbol, name)
    lot = default_lot if lot_size is None else int(lot_size)
    if lot < default_lot or lot % default_lot:
        raise ValueError(f"本策略交易单位必须为 {default_lot} 股的整数倍。")
    cash, shares, cost_basis, entry_index = initial_cash, 0, 0.0, -1
    stop, target, total_fees, slippage_cost = 0.0, 0.0, 0.0, 0.0
    trades, curve, closed_pnls = [], [], []
    blocked_buys, blocked_sells, held_days = 0, 0, 0
    curve.append({"date": rows[119]["date"], "equity": round(cash, 2), "cash": round(cash, 2), "shares": 0})
    for i in range(120, len(rows)):
        today, previous = rows[i], rows[i - 1]
        signal = _frame(rows[:i], symbol, name)
        sold_today = False
        # Only the opening quote and information known by yesterday are used.
        upper_locked = today["open"] >= previous["close"] * (1 + limit) - 0.005
        lower_locked = today["open"] <= previous["close"] * (1 - limit) + 0.005
        tradable = previous["volume"] > 0 and today["volume"] > 0
        if shares and i > entry_index:
            reason = ("前一收盘触发止损" if previous["close"] <= stop else
                      "前一收盘触发止盈" if previous["close"] >= target else
                      "评分跌破退出阈值" if signal["score"] < exit_score else
                      "达到持仓期限" if i - entry_index >= max_holding_days else "")
            if reason:
                if lower_locked or not tradable:
                    blocked_sells += 1
                else:
                    price = today["open"] * (1 - slippage)
                    amount = shares * price
                    commission = max(5.0, amount * commission_rate)
                    tax, transfer = amount * stamp_tax_rate, amount * transfer_fee_rate
                    fees = commission + tax + transfer
                    pnl = amount - fees - cost_basis
                    cash += amount - fees
                    total_fees += fees
                    slippage_cost += shares * today["open"] * slippage
                    closed_pnls.append(pnl)
                    trades.append({"date": today["date"], "signal_date": previous["date"], "symbol": symbol,
                                   "side": "SELL", "price": round(price, 4), "quantity": shares,
                                   "amount": round(amount, 2), "commission": round(commission, 2),
                                   "stamp_tax": round(tax, 2), "transfer_fee": round(transfer, 2),
                                   "fees": round(fees, 2), "pnl": round(pnl, 2),
                                   "holding_days": i - entry_index, "reason": reason})
                    shares, sold_today = 0, True
        if not shares and not sold_today and signal["eligible"] and signal["score"] >= entry_score:
            if upper_locked or not tradable:
                blocked_buys += 1
            else:
                price = today["open"] * (1 + slippage)
                allocation = cash * min(position_pct, signal["max_position_pct"]) / 100
                quantity = max(0, math.floor((allocation - 5) / (price * (1 + commission_rate + transfer_fee_rate)) / lot) * lot)
                if quantity:
                    amount = quantity * price
                    commission = max(5.0, amount * commission_rate)
                    transfer = amount * transfer_fee_rate
                    fees = commission + transfer
                    if amount + fees <= cash:
                        shares, cost_basis, entry_index = quantity, amount + fees, i
                        cash -= cost_basis
                        total_fees += fees
                        slippage_cost += shares * today["open"] * slippage
                        # Stop distances are fixed using yesterday's ATR and the
                        # executable opening price, never today's high or low.
                        distance = max(signal["atr14"], price * 0.005, 0.01)
                        stop, target = max(0.01, price - 1.85 * distance), price + 3.7 * distance
                        trades.append({"date": today["date"], "signal_date": previous["date"], "symbol": symbol,
                                       "side": "BUY", "price": round(price, 4), "quantity": shares,
                                       "amount": round(amount, 2), "commission": round(commission, 2),
                                       "stamp_tax": 0.0, "transfer_fee": round(transfer, 2),
                                       "fees": round(fees, 2), "signal_score": signal["score"],
                                       "reason": "前一收盘评分达到入场阈值"})
        if shares:
            held_days += 1
        curve.append({"date": today["date"], "equity": round(cash + shares * today["close"], 2),
                      "cash": round(cash, 2), "shares": shares})
    peak, max_drawdown = initial_cash, 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        max_drawdown = max(max_drawdown, (peak - point["equity"]) / peak)
    final_equity = cash + shares * rows[-1]["close"]
    warnings = [
        "前复权价格的理论研究回测：不完整模拟分红、送配股、除权记账及真实成交，不能视作实盘收益。",
        "使用当前下载的前复权历史，未重建每个历史时点当时可见的复权版本；价格事后修订可能影响历史指标。",
        "收盘形成信号、下一交易日开盘模拟成交；止损止盈只在收盘确认，跳空或跌停会扩大损失。",
        "用前复权开盘/前收近似涨跌停，触及涨停拒买、触及跌停拒卖；未完整模拟除权参考价与历史规则变更。",
        "当日无成交量时订单视为无法成交；成交量用于可成交性检查，不用于当日开盘信号计算。",
        "包含佣金（单笔最低5元）、卖出印花税、过户费和滑点；默认参数为研究假设，请按账户实际费率调整。",
        "单股与固定股票池回测存在选择偏差；未纳入退市股票全集、资金容量、盘口冲击与排队，历史表现不代表未来。",
        "期末持仓按收盘价计值，未假设强制平仓，未计未发生的卖出费用。",
    ]
    if len(closed_pnls) < 10:
        warnings.append("已完成交易不足10笔，无法据此判断策略稳定性。")
    if _restricted(symbol, name):
        warnings.append("指数或 ST/退市标记股票不进行模拟交易。")
    return {
        "symbol": symbol, "name": name, "start_date": curve[0]["date"], "end_date": rows[-1]["date"],
        "initial_cash": round(initial_cash, 2), "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_cash - 1) * 100, 2),
        "benchmark_return_pct": round((rows[-1]["close"] / rows[119]["close"] - 1) * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "win_rate_pct": round(sum(value > 0 for value in closed_pnls) / len(closed_pnls) * 100, 2) if closed_pnls else None,
        "trade_count": len(trades), "closed_trade_count": len(closed_pnls),
        "fees": round(total_fees, 2), "slippage_cost": round(slippage_cost, 2),
        "open_shares": shares, "cash": round(cash, 2), "blocked_buys": blocked_buys,
        "blocked_sells": blocked_sells, "exposure_pct": round(held_days / (len(rows) - 120) * 100, 2),
        "trades": trades, "equity_curve": curve, "warnings": warnings,
        "price_basis": "qfq_theoretical", "strategy_version": "transparent-daily-v1",
        "parameters": {"commission_rate": commission_rate, "minimum_commission": 5,
                       "stamp_tax_rate": stamp_tax_rate, "transfer_fee_rate": transfer_fee_rate,
                       "slippage": slippage, "position_pct": position_pct, "entry_score": entry_score,
                       "exit_score": exit_score, "lot_size": lot, "max_holding_days": max_holding_days},
    }


def backtest_cycle(bars, symbol, name="", initial_cash=100000, entry_score=70,
                   position_pct=10, commission_rate=0.0003, stamp_tax_rate=0.0005,
                   slippage=0.001, transfer_fee_rate=0.00001, excluded=False):
    """Independent rule-only D1-open/D2-close research, never a model backtest.

    A score computed after S may buy at the next supplied session's open inside
    a +/-0.3% MA5/MA20 support band. D2 is the next available daily session after
    purchase, including a weekend/holiday gap. The scheduled exit uses its close
    as a disclosed daily-data approximation, never an exact intraday fill.
    """
    rows = _bars(bars)
    if len(rows) < 121:
        raise ValueError("两交易日规则回测至少需要121条有效日线。")
    if _number(initial_cash) <= 0 or not 0 < position_pct <= 100:
        raise ValueError("资金与仓位参数无效。")
    for rate in (commission_rate, stamp_tax_rate, slippage, transfer_fee_rate):
        if not math.isfinite(float(rate)) or not 0 <= float(rate) < 1:
            raise ValueError("费率与滑点必须在0至1之间。")
    limit, lot = _board(symbol, name)
    cash, shares, entry_index, cost_basis = float(initial_cash), 0, -1, 0.0
    total_fees, slippage_cost = 0.0, 0.0
    trades, closed_pnls = [], []
    curve = [{"date": rows[119]["date"], "equity": round(cash, 2), "cash": round(cash, 2), "shares": 0}]
    blocked_buys = blocked_sells = outside_entry_range = 0
    for i in range(120, len(rows)):
        today, previous = rows[i], rows[i - 1]
        held_at_open = bool(shares)
        if held_at_open and i > entry_index:
            locked_down = today["close"] <= previous["close"] * (1 - limit) + 0.005
            if locked_down or today["volume"] <= 0:
                blocked_sells += 1
            else:
                fill = today["close"] * (1 - slippage)
                amount = fill * shares
                commission = max(5.0, amount * commission_rate)
                tax, transfer = amount * stamp_tax_rate, amount * transfer_fee_rate
                fee = commission + tax + transfer
                pnl = amount - fee - cost_basis
                cash += amount - fee
                total_fees += fee
                slippage_cost += today["close"] * slippage * shares
                closed_pnls.append(pnl)
                trades.append({"date": today["date"], "signal_date": rows[entry_index - 1]["date"],
                               "symbol": symbol, "side": "SELL", "price": round(fill, 4), "quantity": shares,
                               "amount": round(amount, 2), "fees": round(fee, 2), "commission": round(commission, 2),
                               "stamp_tax": round(tax, 2), "transfer_fee": round(transfer, 2), "pnl": round(pnl, 2),
                               "holding_sessions": i - entry_index + 1, "entry_date": rows[entry_index]["date"],
                               "fill_basis": "D2或顺延可交易日收盘近似，非盘中精确成交",
                               "reason": "两交易日周期退出" if i == entry_index + 1 else "受限后顺延退出"})
                shares = 0
        # A close sale cannot finance a purchase at that same day's earlier open.
        if not held_at_open and not excluded and not _restricted(symbol, name):
            signal = _frame(rows[:i], symbol, name)
            if signal["eligible"] and signal["score"] >= entry_score:
                if today["volume"] <= 0 or today["open"] >= previous["close"] * (1 + limit) - 0.005:
                    blocked_buys += 1
                else:
                    supports = [signal[key] for key in ("ma5", "ma20") if signal[key] <= previous["close"]]
                    support = max(supports) if supports else previous["close"]
                    low, high = support * 0.997, support * 1.003
                    fill = today["open"] * (1 + slippage)
                    if not low <= fill <= high:
                        outside_entry_range += 1
                    else:
                        allocation = cash * min(position_pct, signal["max_position_pct"]) / 100
                        quantity = max(0, math.floor((allocation - 5) / (fill * (1 + commission_rate + transfer_fee_rate)) / lot) * lot)
                        if quantity:
                            amount = fill * quantity
                            commission = max(5.0, amount * commission_rate)
                            transfer = amount * transfer_fee_rate
                            fee = commission + transfer
                            if amount + fee <= cash:
                                shares, entry_index, cost_basis = quantity, i, amount + fee
                                cash -= cost_basis
                                total_fees += fee
                                slippage_cost += today["open"] * slippage * shares
                                trades.append({"date": today["date"], "signal_date": previous["date"], "symbol": symbol,
                                               "side": "BUY", "price": round(fill, 4), "quantity": shares,
                                               "amount": round(amount, 2), "fees": round(fee, 2), "commission": round(commission, 2),
                                               "stamp_tax": 0.0, "transfer_fee": round(transfer, 2),
                                               "signal_score": signal["score"], "fill_basis": "D1开盘加滑点",
                                               "reason": "前一收盘六因子通过且开盘触及均线支撑窄区间"})
        curve.append({"date": today["date"], "equity": round(cash + shares * today["close"], 2),
                      "cash": round(cash, 2), "shares": shares})
    peak, drawdown = float(initial_cash), 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = max(drawdown, (peak - point["equity"]) / peak)
    final_equity = cash + shares * rows[-1]["close"]
    return {"symbol": symbol, "name": name, "strategy_version": "two-session-rules-v1",
            "uses_prediction_model": False, "horizon_days": 2, "cycle_includes_buy_day": True,
            "start_date": rows[119]["date"], "end_date": rows[-1]["date"],
            "initial_cash": initial_cash, "final_equity": round(final_equity, 2), "cash": round(cash, 2),
            "total_return_pct": round((final_equity / initial_cash - 1) * 100, 2),
            "benchmark_return_pct": round((rows[-1]["close"] / rows[119]["close"] - 1) * 100, 2),
            "max_drawdown_pct": round(drawdown * 100, 2), "trade_count": len(trades),
            "closed_trade_count": len(closed_pnls), "open_shares": shares,
            "win_rate_pct": round(sum(pnl > 0 for pnl in closed_pnls) / len(closed_pnls) * 100, 2) if closed_pnls else None,
            "fees": round(total_fees, 2), "slippage_cost": round(slippage_cost, 2),
            "blocked_buys": blocked_buys, "blocked_sells": blocked_sells, "outside_entry_range": outside_entry_range,
            "trades": trades, "equity_curve": curve,
            "warnings": ["独立的六因子+均线窄区间两交易日规则回测，不含岭回归预测筛选，不能代表主预测组合策略收益。",
                         "D1下一交易日开盘买，D2下一可交易日收盘退出；两天包含买入日，周末与假期不算持有交易日。",
                         "只有日线，D2收盘仅作周期卖出价格近似，不是14:50或09:00的可验证成交；当天买入不能当天卖。",
                         "涨停开盘不买，收盘跌停或无量不卖并顺延；缺失停牌日线按下一可交易证券日线顺延。",
                         "采用当前前复权历史，未重建历史复权版本，也未完整记账分红送配股；收费与滑点为可配置研究假设。",
                         "是否排除指数权重股由调用者excluded参数决定；期末未平仓按收盘价计值，未计未来退出费用。"]}
