"""Fundamental watchlist from dated quotes and already-published statements.

No return forecasts, invented earnings, target prices, or live order routing.
TTM P/E is computed from market capitalization and disclosed attributable profit.
The caller owns persistence and scheduling. All valuation-qualified names are
researched by default; a failed source is stopped after three consecutive errors.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import threading
import time
from urllib.parse import urlencode

import market_data as md

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT = "RPT_F10_FINANCE_MAINFINADATA"
FIELDS = "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,UPDATE_DATE,ROEJQ,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,PARENTNETPROFIT,TOTALOPERATEREVE,KCFJCXSYJLR,NETCASH_OPERATE_PK,ZCFZL"
MAX_CANDIDATES = 40
_NETWORK_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
HEAVYWEIGHTS = {"sh600519", "sh600900", "sh601318", "sh601628", "sh601601", "sh601319",
               "sh601336", "sh600030", "sh600837", "sh601688", "sh601211", "sh601066",
               "sh600028", "sh601857", "sh600938", "sh600941", "sh601728", "sh600050",
               "sh601088", "sh601899", "sh600309", "sh601668", "sh601390", "sh601186"}
WARNINGS = [
    "长线分数是估值、已披露盈利质量与增长的规则评分，不预测回报、目标股价或公司未来盈利。低PE不等于前景好。",
    "对估值预筛合格股票逐只核验财报；未通过估值筛选者并未进行完整公司研究，未比较同行估值分布。",
    "PE(TTM)按报价总市值除以财报累计归母净利还原的近12个月利润计算；不同股本、A/H价格和市场口径可能导致差异。",
    "财报期末、公告日与供应商更新日分别核验，不读取快照日后的记录；供应商历史修订仍不等于完整的点时财务数据库。",
    "公告日期为日级粒度，同日盘后公告的具体发布时间需另行核查。行业新闻、订单、竞争格局和治理风险尚待人工审核。",
]


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _fetch_reports(symbol):
    global _LAST_REQUEST
    if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
        raise ValueError("无效股票代码")
    params = {"reportName": REPORT, "columns": FIELDS,
              "filter": f'(SECURITY_CODE="{symbol[2:]}")',
              "sortColumns": "REPORT_DATE", "sortTypes": "-1",
              "pageSize": 12, "pageNumber": 1, "source": "WEB", "client": "WEB"}
    with _NETWORK_LOCK:
        delay = 0.25 - (time.monotonic() - _LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()
        payload = md._json(API + "?" + urlencode(params), referer="https://data.eastmoney.com/", timeout=8, retries=1)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("财务数据源未成功返回报告")
    records = (payload.get("result") or {}).get("data")
    if not isinstance(records, list) or not records:
        raise ValueError("财务数据源返回空报告")
    return records


def _published(records, symbol, snapshot):
    """Select only versions known by snapshot, keeping the latest valid version."""
    by_period = {}
    for row in records:
        if str(row.get("SECURITY_CODE", "")) != symbol[2:]:
            continue
        period = _date(row.get("REPORT_DATE"))
        published = _date(row.get("NOTICE_DATE"))
        updated = _date(row.get("UPDATE_DATE"))
        if not period or not published or not updated:
            continue
        if period > snapshot or published > snapshot or updated > snapshot or published < period:
            continue
        if period[5:] not in ("03-31", "06-30", "09-30", "12-31"):
            continue
        record = dict(row, _period=period, _published=published, _updated=updated)
        old = by_period.get(period)
        if old is None or (updated, published) > (old["_updated"], old["_published"]):
            by_period[period] = record
    return by_period


def _components(by_period, latest):
    period = latest["_period"]
    if period.endswith("12-31"):
        return [(latest, 1)]
    year = int(period[:4])
    annual = by_period.get(f"{year - 1}-12-31")
    prior_same = by_period.get(f"{year - 1}{period[4:]}")
    if not annual or not prior_same:
        return []
    return [(latest, 1), (annual, 1), (prior_same, -1)]


def _ttm(parts, field):
    values = [(_number(row.get(field)), sign) for row, sign in parts]
    if not values or any(value is None for value, _ in values):
        return None
    return sum(value * sign for value, sign in values)


def _round(value, places=2):
    return round(value, places) if value is not None else None


def _base(stock, quote, snapshot):
    symbol = stock["symbol"]
    return {"symbol": symbol, "name": stock["name"], "date": snapshot,
            "quote_date": _date(quote.get("date") or quote.get("datetime") or quote.get("timestamp")),
            "pe_ttm": None, "pe_ttm_basis": "报价总市值 / 由已公告累计归母净利润还原的TTM利润",
            "pb": _number(quote.get("pb")), "market_cap_100m": _number(quote.get("market_cap_100m")),
            "roe": None, "roe_report_date": None, "roe_basis": "最近已公告完整年度的加权ROE，非中报ROE年化",
            "revenue_growth": None, "profit_growth": None, "score": None, "eligible": False,
            "report_date": None, "publication_date": None, "provider_update_date": None,
            "source": f"https://data.eastmoney.com/stockdata/{symbol[2:]}.html",
            "financial_api_source": API, "financial_report": REPORT,
            "valuation_source": quote.get("source"), "thesis": [], "risks": [], "factors": []}


def _analyze(stock, quote, snapshot, records):
    result = _base(stock, quote, snapshot)
    periods = _published(records, stock["symbol"], snapshot)
    if not periods:
        result["risks"].append("没有同时满足报告期、公告日和供应商更新日不晚于快照日的财报，不能推荐。")
        return result
    latest = periods[max(periods)]
    result.update(report_date=latest["_period"], publication_date=latest["_published"],
                  provider_update_date=latest["_updated"],
                  revenue_growth=_number(latest.get("TOTALOPERATEREVETZ")),
                  profit_growth=_number(latest.get("PARENTNETPROFITTZ")))
    annuals = [row for key, row in periods.items() if key.endswith("12-31")]
    annual = max(annuals, key=lambda row: row["_period"]) if annuals else None
    if annual:
        result["roe"] = _number(annual.get("ROEJQ"))
        result["roe_report_date"] = annual["_period"]
        result["roe_publication_date"] = annual["_published"]
    parts = _components(periods, latest)
    result["ttm_profit_components"] = [{"report_date": row["_period"], "publication_date": row["_published"],
                                       "provider_update_date": row["_updated"], "parent_netprofit": _number(row.get("PARENTNETPROFIT")),
                                       "sign": sign} for row, sign in parts]
    profit = _ttm(parts, "PARENTNETPROFIT")
    cash = _ttm(parts, "NETCASH_OPERATE_PK")
    recurring = _ttm(parts, "KCFJCXSYJLR")
    result["ttm_parent_netprofit_100m"] = _round(profit / 1e8 if profit is not None else None, 4)
    result["ttm_formula"] = " + ".join(("- " if sign < 0 else "") + row["_period"] for row, sign in parts).replace("+ -", "−")
    cap = result["market_cap_100m"]
    if profit is not None and profit > 0 and cap is not None and cap > 0:
        result["pe_ttm"] = _round(cap * 1e8 / profit)
    result["operating_cash_to_profit"] = _round(cash / profit if cash is not None and profit is not None and profit > 0 else None, 3)
    result["recurring_profit_ratio"] = _round(recurring / profit if recurring is not None and profit is not None and profit > 0 else None, 3)
    result["debt_ratio"] = _number(latest.get("ZCFZL"))
    missing = [label for key, label in (("pe_ttm", "可还原TTM利润的财报与总市值"), ("roe", "完整年度ROE"),
                                      ("revenue_growth", "营收同比"), ("profit_growth", "归母净利同比"),
                                      ("operating_cash_to_profit", "TTM经营现金流"), ("recurring_profit_ratio", "TTM扣非归母净利"),
                                      ("debt_ratio", "资产负债率")) if result.get(key) is None]
    if missing:
        result["risks"].append("缺少" + "、".join(missing) + "，不生成长线推荐。")
        result["thesis"] = [f"已取得截至{result['report_date']}、于{result['publication_date']}公告的部分财务数据；仍不足以判断盈利质量。"]
        return result
    pe, pb, roe = result["pe_ttm"], result["pb"], result["roe"]
    revenue, growth = result["revenue_growth"], result["profit_growth"]
    cash_ratio, recurring_ratio = result["operating_cash_to_profit"], result["recurring_profit_ratio"]
    valuation = (8 if pe < 5 else 25 if pe <= 20 else 18 if pe <= 30 else 10 if pe <= 40 else 0)
    valuation += 10 if pb <= 1 else 8 if pb <= 3 else 4
    quality = max(0.0, min(15.0, roe)) + max(0.0, min(10.0, cash_ratio * 10)) + (10 if recurring_ratio >= 0.8 else 6 if recurring_ratio >= 0.6 else 0)
    growth_score = max(0.0, min(15.0, revenue * 0.75)) + max(0.0, min(15.0, growth * 0.5))
    debt_penalty = 5 if result["debt_ratio"] >= 65 else 0
    prior_same = periods.get(f"{int(latest['_period'][:4]) - 1}{latest['_period'][4:]}")
    low_base = bool(prior_same and (_number(prior_same.get("PARENTNETPROFIT")) or 0) <= 0)
    if low_base:
        growth_score = min(growth_score, 10)
        result["risks"].append("上年同期利润基数为零或亏损，同比增幅不能直接外推。")
    result["factors"] = [
        {"name": "估值", "score": round(valuation, 2), "max": 35, "detail": "PE与PB区间评分；极低PE不获最高分。"},
        {"name": "盈利质量", "score": round(quality, 2), "max": 35, "detail": "完整年度ROE、TTM经营现金流/净利与扣非净利占比。"},
        {"name": "已披露增长", "score": round(growth_score, 2), "max": 30, "detail": "最近累计期间营收与归母净利同比，不代表未来增速。"},
        {"name": "杠杆扣分", "score": -debt_penalty, "max": 0, "detail": "资产负债率达到65%扣5分。"},
    ]
    result["score"] = round(max(0.0, valuation + quality + growth_score - debt_penalty), 1)
    failures = []
    if not 0 < pe <= 40:
        failures.append("重新计算的TTM市盈率未通过0至40倍筛选")
    if roe < 8 or revenue <= 0 or growth <= 0:
        failures.append("年度ROE不足8%或最近营收/归母净利未同时增长")
    if cash_ratio < 0.5 or recurring_ratio < 0.6:
        failures.append("现金流或扣非利润不足以支持盈利质量筛选")
    if result["debt_ratio"] >= 75:
        failures.append("资产负债率达到75%，需要单独审查偿债风险")
    if (dt.date.fromisoformat(snapshot) - dt.date.fromisoformat(latest["_period"])).days > 200:
        failures.append("最近财报期末距快照日超过200天，财务数据偏旧")
    if result["score"] < 65:
        failures.append("综合研究分未达到65分")
    if low_base:
        failures.append("低基数利润增长须人工复核后再考虑")
    result["eligible"] = not failures
    result["risks"].extend(failures)
    if pe < 5:
        result["risks"].append("TTM市盈率极低，需排查行业景气顶点、一次性收益或结构性衰退。")
    if revenue > 100 or growth > 100:
        result["risks"].append("同比增幅超过100%，需复核基数、并购与一次性因素。")
    result["risks"].extend(["尚未核查最新行业新闻、订单可持续性、竞争与公司治理；财务趋势不是前景保证。",
                             "不同财报期间和行业会影响可比性；估值评分未替代逐份阅读公司公告。"])
    result["thesis"] = [
        f"截至{snapshot}，总市值{cap:.2f}亿元 / 已披露TTM归母净利{profit/1e8:.2f}亿元，推算PE(TTM){pe:.2f}倍；PB{pb:.2f}倍。低估值只是进一步研究的起点。",
        f"最近报告期{latest['_period']}（公告{latest['_published']}）：营收同比{revenue:+.2f}%，归母净利同比{growth:+.2f}%。",
        f"完整年度{result['roe_report_date']}加权ROE为{roe:.2f}%；TTM经营现金流/归母净利{cash_ratio:.2f}，扣非归母净利占比{recurring_ratio:.2f}。",
        ("若需求、利润率和现金回收持续，已披露的营收与利润共同增长支持中长线跟踪；仍需核查行业与公司后续公告。"
         if revenue > 0 and growth > 0 else "已披露的营收与利润尚未同时改善，当前不足以形成积极的长期增长判断。"),
    ]
    return result


def refresh(universe, quotes, snapshot_date, max_candidates=None):
    """Research every valuation-qualified stock, or an explicit bounded batch."""
    snapshot = _date(snapshot_date)
    if not snapshot:
        raise ValueError("快照日期必须为YYYY-MM-DD")
    if isinstance(quotes, dict) and isinstance(quotes.get("quotes"), list):
        quotes = {row.get("symbol"): row for row in quotes["quotes"]}
    elif isinstance(quotes, list):
        quotes = {row.get("symbol"): row for row in quotes}
    quotes = quotes or {}
    candidates, seen = [], set()
    coverage = {"universe": len(universe), "excluded": 0, "valuation_missing": 0,
                "outside_threshold": 0, "valuation_qualified": 0, "financial_requested": 0,
                "financial_complete": 0, "eligible": 0, "not_researched": 0}
    for stock in universe:
        symbol, name = str(stock.get("symbol", "")), str(stock.get("name", ""))
        if symbol in seen:
            continue
        seen.add(symbol)
        quote = quotes.get(symbol) or {}
        text = " ".join((name, str(stock.get("industry", "")), str(quote.get("industry", ""))))
        cap = _number(quote.get("market_cap_100m"))
        if not re.fullmatch(r"(?:sh|sz)\d{6}", symbol) or not name or name == symbol or "ST" in name.upper() or "退" in name or any(word in text for word in ("银行", "证券", "保险", "石油", "石化")) or symbol in HEAVYWEIGHTS or (cap is not None and cap >= 5000):
            coverage["excluded"] += 1
            continue
        # An unclassified provider PE is allowed only as a preliminary screen;
        # it is never copied into the final calculated pe_ttm result.
        screening_pe = _number(quote.get("pe_ttm"))
        if screening_pe is None:
            screening_pe = _number(quote.get("pe"))
        pb = _number(quote.get("pb"))
        quote_date = _date(quote.get("date") or quote.get("datetime") or quote.get("timestamp"))
        if screening_pe is None or pb is None or cap is None or cap <= 0 or quote_date != snapshot:
            coverage["valuation_missing"] += 1
            continue
        if not 0 < screening_pe <= 40 or not 0 < pb <= 6:
            coverage["outside_threshold"] += 1
            continue
        candidates.append((screening_pe, pb, {"symbol": symbol, "name": name}, quote))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]["symbol"]))
    coverage["valuation_qualified"] = len(candidates)
    selected = candidates if max_candidates is None else candidates[:max_candidates]
    coverage["not_researched"] = len(candidates) - len(selected)
    stocks, consecutive_errors = [], 0
    deadline = time.monotonic() + max(90, len(selected) * 10)
    for _, _, stock, quote in selected:
        try:
            if consecutive_errors >= 3 or time.monotonic() > deadline:
                raise RuntimeError("连续财务源失败或本轮获取预算用完，暂停后续请求")
            coverage["financial_requested"] += 1
            records = _fetch_reports(stock["symbol"])
            item = _analyze(stock, quote, snapshot, records)
            consecutive_errors = 0
        except (ValueError, OSError, RuntimeError) as exc:
            consecutive_errors += 1
            item = _base(stock, quote, snapshot)
            item["risks"] = ["财务数据不可核验，暂不可推荐：" + str(exc)[:200]]
        stocks.append(item)
        coverage["financial_complete"] += item["score"] is not None
        coverage["eligible"] += bool(item["eligible"])
    stocks.sort(key=lambda item: (item["eligible"], item["score"] is not None, item["score"] or 0), reverse=True)
    warnings = list(WARNINGS)
    if coverage["valuation_missing"]:
        warnings.insert(0, f"{coverage['valuation_missing']}只股票缺少快照日同日估值、总市值或日期信息，未进入筛选；未用较新报价回填历史估值。")
    if coverage["financial_complete"] < len(stocks):
        warnings.insert(0, f"本轮{len(stocks)-coverage['financial_complete']}只候选财务缺失或无法核验，展示缺失项并停止推荐。")
    if not coverage["eligible"]:
        warnings.insert(0, "当前没有同时通过估值、盈利质量、增长与数据完整性检查的长线候选。")
    return {"as_of": snapshot, "stocks": stocks, "warnings": warnings, "coverage": coverage,
            "method": "先按同日普通PE/TTM PE与PB预筛，再对全部合格股以已公布财报重算TTM PE，结合完整年度ROE、TTM现金流/扣非利润和最新同比增长评分。",
            "screen_order": "估值预筛PE升序；默认核验全部估值合格股。未通过筛选的股票不代表较差。",
            "return_forecast": None, "source": API}
