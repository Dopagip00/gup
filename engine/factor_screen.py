"""Dated, conjunctive factor checks; not a fitted ranking or buy signal."""
import datetime as dt
import math

PROTOCOL = 'multi-factor-evidence-v1'


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def financial_dates_valid(record, snapshot, target, moment):
    """Annual ROE and latest operating facts must both have been disclosed."""
    try:
        day = dt.date.fromisoformat(target)
        original = dt.date.fromisoformat(snapshot['as_of'])
        fetched = dt.datetime.fromisoformat(snapshot['fetched_at'].replace('Z', '+00:00'))
        report, published, updated, annual, annual_published = [dt.date.fromisoformat(record[k]) for k in
            ('report_date', 'publication_date', 'provider_update_date', 'roe_report_date', 'roe_publication_date')]
        return bool(moment.tzinfo and fetched.tzinfo and fetched <= moment
            and fetched.astimezone(moment.tzinfo).date() >= original
            and record['date'] == record['quote_date'] == original.isoformat()
            and report <= published <= original <= day and report <= updated <= original
            and annual <= report and annual <= annual_published <= original
            and (annual.month, annual.day) == (12, 31) and (day - report).days <= 200)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def conjunction(rows):
    states = [row['status'] for row in rows]
    return 'fail' if 'fail' in states else 'unknown' if not states or 'unknown' in states else 'pass'


def check(key, label, value, predicate, rule, *, known=True, unit=''):
    valid = known and finite(value)
    return {'key': key, 'label': label, 'value': value if valid else None, 'unit': unit,
            'rule': rule, 'status': ('pass' if predicate(value) else 'fail') if valid else 'unknown'}


def report(stock, wave, snapshot, moment):
    """Show existing conditions separately; no high score can hide a failed row."""
    target, symbol = stock.get('date'), stock.get('symbol')
    current = stock.get('stale') is False and wave.get('as_of') == target
    metrics, sector = wave.get('metrics') or {}, wave.get('sector') or {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    source_rows = snapshot.get('stocks') if isinstance(snapshot.get('stocks'), list) else []
    rows = [r for r in source_rows if isinstance(r, dict) and r.get('symbol') == symbol]
    record = rows[0] if len(rows) == 1 else {}
    disclosed = current and financial_dates_valid(record, snapshot, target, moment)
    same_session = disclosed and snapshot.get('as_of') == target
    risk = stock.get('chase_risk') or {}

    def flag(key, label, value, rule, known=True):
        return {'key': key, 'label': label, 'value': None, 'unit': '', 'rule': rule,
                'status': ('pass' if value else 'fail') if known and type(value) is bool else 'unknown'}

    technical = [
        flag('market', '市场趋势', (wave.get('market') or {}).get('regime') == 'strong', '指数强趋势',
             current and (wave.get('market') or {}).get('regime') in ('strong', 'weak', 'range')),
        flag('trend', '均线排列', metrics.get('trend_aligned'), '收盘≥MA5>MA10>MA20>MA60，MA20上行', current),
        flag('setup', '量价形态', wave.get('phase') in ('volume_breakout', 'dry_pullback'), '放量突破或缩量回踩',
             current and bool(metrics)),
        flag('anti_chase', '追高控制', risk.get('gate_passed'), '现有反追高规则',
             current and risk.get('as_of_date') == target and risk.get('version') == 'anti-chase-v1'),
    ]
    financial = [check(key, label, record.get(key), predicate, rule, known=known, unit=unit)
        for key, label, predicate, rule, known, unit in (
            ('pe_ttm', 'PE(TTM)', lambda x: 0 < x <= 40, '0 < PE ≤ 40', same_session, '倍'),
            ('ttm_parent_netprofit_100m', 'TTM归母净利润', lambda x: x > 0, '>0亿元', disclosed, '亿元'),
            ('roe', '年度ROE', lambda x: x >= 8, '≥8%', disclosed, '%'),
            ('revenue_growth', '营收同比', lambda x: x > 0, '>0%', disclosed, '%'),
            ('profit_growth', '净利润同比', lambda x: x > 0, '>0%', disclosed, '%'),
            ('operating_cash_to_profit', '现金流/净利润', lambda x: x >= .5, '≥0.50', disclosed, ''),
            ('recurring_profit_ratio', '扣非利润占比', lambda x: x >= .6, '≥0.60', disclosed, ''),
            ('debt_ratio', '资产负债率', lambda x: 0 <= x < 75, '0% ≤ 负债率 < 75%', disclosed, '%'))]
    industry = [flag('industry', '行业相对强度', sector.get('passed'), '行业前20%、上涨均线广度≥60%',
                     current and sector.get('basis') == 'official_industry_proxy')]
    groups = [{'name': name, 'status': conjunction(items), 'items': items} for name, items in
              (('趋势与量价', technical), ('财务与估值', financial), ('行业共振', industry))]
    return {'protocol': PROTOCOL, 'symbol': symbol, 'as_of': target, 'screening_only': True,
            'ranking_applied': False, 'groups': groups, 'status': conjunction(groups),
            'financial_snapshot_date': snapshot.get('as_of') if disclosed else None,
            'financial_report_date': record.get('report_date') if disclosed else None,
            'financial_publication_date': record.get('publication_date') if disclosed else None,
            'roe_report_date': record.get('roe_report_date') if disclosed else None,
            'industry': sector.get('industry') if current else None,
            'unavailable_dimensions': ['实时资金流', '机构持仓变化', '真实题材龙头'],
            'note': '逐项条件核对，不改变已发布排序；行业使用官方分类代理。'}
