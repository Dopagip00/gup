"""Zero-dependency market feeds. No fabricated prices or partial-universe fallback.

History: Tencent daily OHLCV (volume normalized to lots of 100 shares); qfq for indicators,
raw_* for actual traded prices. Shanghai membership: SSE official securities list.
Public endpoints can be delayed/unavailable and are not exchange-grade execution feeds.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import random
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CHINA = dt.timezone(dt.timedelta(hours=8))
CALENDAR_SOURCE = 'https://www.sse.com.cn/disclosure/dealinstruc/closed/'
SSE_LIST_SOURCE = 'https://www.sse.com.cn/assortment/stock/list/share/'
SSE_LIST_APIS = ('https://query.sse.com.cn/sseQuery/commonQuery.do',
                 'https://query.sse.com.cn/commonQuery.do')
# Current public Tencent route also used by AKShare stock_hist_tx.py.
HISTORY_SOURCE = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'
EASTMONEY_HISTORY_SOURCE = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
QUOTE_SOURCE = 'https://qt.gtimg.cn/'
SYMBOL_RE = re.compile(r'^(?:sh|sz|bj)\d{6}$')
_CALENDAR_LOCK = threading.Lock()
_CALENDAR_CACHE = {}
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_EM_LOCK = threading.Lock()
_EM_LAST_REQUEST = 0.0


def _em_throttle():
    """Called under _EM_LOCK: serialize fallback requests, including retries."""
    global _EM_LAST_REQUEST
    delay = 1.0 + random.uniform(0.05, 0.25) - (time.monotonic() - _EM_LAST_REQUEST)
    if delay > 0:
        time.sleep(delay)
    _EM_LAST_REQUEST = time.monotonic()


def _throttle():
    """One process-wide limiter shared by every worker and public feed."""
    global _LAST_REQUEST
    with _REQUEST_LOCK:
        delay = 0.25 - (time.monotonic() - _LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()


class MarketDataError(RuntimeError):
    """A provider/network/data-quality failure safe to display to the user."""


class MarketNetworkError(MarketDataError):
    """Transport/HTTP failure, distinct from invalid provider data."""


class CalendarUnavailable(MarketDataError):
    pass


def _now():
    return dt.datetime.now(CHINA)


def _request(url, *, encoding='utf-8', referer=None, retries=2, timeout=15):
    host = urllib.parse.urlsplit(url).hostname or ''
    if host == 'eastmoney.com' or host.endswith('.eastmoney.com'):
        with _EM_LOCK:
            return _request_inner(url, encoding=encoding, referer=referer, retries=retries, timeout=timeout, eastmoney=True)
    return _request_inner(url, encoding=encoding, referer=referer, retries=retries, timeout=timeout)


def _request_inner(url, *, encoding, referer, retries, timeout, eastmoney=False):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
               'Accept': '*/*', 'Referer': referer or 'https://finance.qq.com/'}
    host = urllib.parse.urlsplit(url).hostname or 'market provider'
    error = None
    network_only = True
    for attempt in range(retries + 1):
        try:
            if eastmoney:
                _em_throttle()
            _throttle()
            # urllib default SSL context verifies certificates and hostnames.
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
            if len(payload) > 16 * 1024 * 1024:
                raise MarketDataError(f'{host}: response exceeds size limit')
            return payload.decode(encoding)
        except (OSError, urllib.error.URLError, ValueError, UnicodeError) as exc:
            error = f'HTTP {exc.code}' if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            network_only = network_only and isinstance(exc, (OSError, urllib.error.URLError))
            if attempt < retries:
                time.sleep(2 ** attempt)
    error_type = MarketNetworkError if network_only else MarketDataError
    raise error_type(f'{host}: request failed after {retries + 1} attempts ({error})')


def _json(url, **kwargs):
    try:
        return json.loads(_request(url, **kwargs))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise MarketDataError('Market provider returned invalid JSON') from exc


def _symbol(value):
    value = str(value).strip().lower()
    if not SYMBOL_RE.fullmatch(value):
        raise ValueError('股票代码格式应为 sh600519 / sz000001')
    return value


def _number(value, label, *, positive=False):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise MarketDataError(f'Invalid numeric field: {label}') from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise MarketDataError(f'Invalid numeric field: {label}')
    return result


def _lots(symbol, provider_volume):
    # Tencent old fqkline + qt use shares for STAR/CDR and lots for main-board.
    # Real probes: 688981 = 37,855,220 shares; 600519 = 38,502 lots on 2026-10-16.
    return provider_volume / 100.0 if symbol.startswith(('sh688', 'sh689')) else provider_volume


def _optional_number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _range(start, end):
    a, b = _date(start), _date(end)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


_HOLIDAYS = {
    2025: [('01-01', '01-01'), ('01-28', '02-04'), ('04-04', '04-06'),
           ('05-01', '05-05'), ('05-31', '06-02'), ('10-01', '10-08')],
    2026: [('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
           ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')],
}


def _validate_calendar(value, year):
    if not isinstance(value, dict) or not isinstance(value.get('holidays'), list):
        raise CalendarUnavailable(f'{year}年交易日历格式错误')
    source = str(value.get('source', ''))
    if urllib.parse.urlsplit(source).hostname not in ('www.sse.com.cn', 'sse.com.cn'):
        raise CalendarUnavailable('交易日历必须包含上交所官方来源链接')
    try:
        dates = sorted({_date(x).isoformat() for x in value['holidays']})
    except (TypeError, ValueError) as exc:
        raise CalendarUnavailable(f'{year}年交易日历日期格式错误') from exc
    if len(dates) < 15 or len(dates) > 60 or any(not x.startswith(f'{year}-') for x in dates):
        raise CalendarUnavailable(f'{year}年交易日历未通过完整性校验')
    return {'year': year, 'holidays': dates, 'source': source,
            'verified_through': f'{year}-12-31', 'verified': True,
            'provenance': value.get('provenance', 'user_official_calendar')}


def _parse_calendar_page(page, year):
    text = html.unescape(re.sub(r'<[^>]*>', ' ', page))
    text = re.sub(r'\s+', ' ', text)
    marker = re.search(rf'{year}\s*年休市安排', text)
    if not marker:
        raise CalendarUnavailable(f'上交所尚未提供可确认的{year}年度交易日历')
    body = text[marker.end():]
    body = body.split('相关公告', 1)[0]
    if not all(name in body for name in ('元旦', '春节', '清明节', '劳动节', '端午节', '中秋节', '国庆节')):
        raise CalendarUnavailable(f'{year}年度官方交易日历未通过节日完整性校验')
    holidays = set()
    # Match only actual closure clauses; the reopening date is excluded.
    pattern = r'(\d{1,2})月(\d{1,2})日(?:[（(][^）)]*[）)])?\s*(?:至\s*(?:(\d{1,2})月)?(\d{1,2})日(?:[（(][^）)]*[）)])?)?\s*休市'
    for sm, sd, em, ed in re.findall(pattern, body):
        start = dt.date(year, int(sm), int(sd))
        end = dt.date(year, int(em or sm), int(ed or sd))
        holidays.update(_range(start, end))
    return _validate_calendar({'holidays': sorted(holidays), 'source': CALENDAR_SOURCE,
                               'provenance': 'sse_live_verified'}, year)


def get_calendar(year):
    """Return verified annual holiday calendar, or raise (never assume weekdays).

    Future years are loaded from the SSE annual page. If that fails, an operator
    may provide data/calendar_overrides.json: {"2027": {"holidays": [...],
    "source": "https://www.sse.com.cn/...official-notice..."}}.
    """
    year = int(year)
    if year in _HOLIDAYS:
        holidays = [d for a, b in _HOLIDAYS[year] for d in _range(f'{year}-{a}', f'{year}-{b}')]
        return {'year': year, 'holidays': sorted(holidays), 'source': CALENDAR_SOURCE,
                'verified_through': f'{year}-12-31', 'verified': True, 'provenance': 'verified_sse_annual_notice'}
    base = Path(os.environ.get('ASHARE_DATA_DIR', str(Path(__file__).resolve().parent / 'data')))
    with _CALENDAR_LOCK:
        for name in ('calendar_overrides.json', 'calendar_cache.json'):
            path = base / name
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding='utf-8-sig')).get(str(year))
                    if value:
                        return _validate_calendar(value, year)
                except (ValueError, OSError) as exc:
                    raise CalendarUnavailable(f'{name} 读取或校验失败') from exc
        if year in _CALENDAR_CACHE:
            return dict(_CALENDAR_CACHE[year])
        try:
            value = _parse_calendar_page(_request(CALENDAR_SOURCE, referer=CALENDAR_SOURCE), year)
        except (MarketDataError, ValueError) as exc:
            raise CalendarUnavailable(f'{year}年交易日历不可验证，已暂停新推荐。请核对上交所公告并更新data/calendar_overrides.json') from exc
        _CALENDAR_CACHE[year] = value
        try:
            base.mkdir(parents=True, exist_ok=True)
            path = base / 'calendar_cache.json'
            existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            existing[str(year)] = value
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
        except (ValueError, OSError):
            pass  # Freshly validated in memory; persistent-cache errors are not fabricated calendars.
        return dict(value)


def is_trading_day(value):
    day = _date(value)
    calendar = get_calendar(day.year)
    return day.weekday() < 5 and day.isoformat() not in calendar['holidays']


def last_completed_session(now=None):
    """ISO date of latest session finished by 15:20 Beijing time."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=CHINA)
    now = now.astimezone(CHINA)
    day = now.date()
    if now.time().replace(tzinfo=None) < dt.time(15, 20):
        day -= dt.timedelta(days=1)
    for _ in range(40):
        if is_trading_day(day):
            return day.isoformat()
        day -= dt.timedelta(days=1)
    raise CalendarUnavailable('无法确认最近完整交易日')


def _parse_bars(payload, symbol, adjustment):
    if not isinstance(payload, dict) or payload.get('code', 0) != 0:
        raise MarketDataError(f'{symbol}: 腾讯历史行情返回错误')
    block = payload.get('data', {}).get(symbol)
    if not isinstance(block, dict):
        raise MarketDataError(f'{symbol}: 腾讯未返回历史行情')
    key = 'qfqday' if adjustment == 'qfq' else 'day'
    rows = block.get(key)
    # Indexes have no corporate-action adjustment and Tencent uses day.
    if rows is None and adjustment == 'qfq' and symbol == 'sh000001':
        rows = block.get('day')
    if not isinstance(rows, list) or not rows:
        raise MarketDataError(f'{symbol}: 缺少{key}数据，未替换为其他复权口径')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise MarketDataError(f'{symbol}: 历史行情行格式错误')
        try:
            date = _date(row[0]).isoformat()
        except (TypeError, ValueError) as exc:
            raise MarketDataError(f'{symbol}: 历史行情日期错误') from exc
        if date in seen:
            raise MarketDataError(f'{symbol}: 历史行情日期重复')
        seen.add(date)
        # newfqkline: OHLCV; STAR volume is shares, other stocks use lots.
        # The new route's index 8 is amount in 10,000 RMB (live checked
        # 2026-10-24 against 600519/688981 quotes); do not infer it on old routes.
        bar = {'date': date, 'open': _number(row[1], 'open', positive=True),
               'close': _number(row[2], 'close', positive=True),
               'high': _number(row[3], 'high', positive=True),
               'low': _number(row[4], 'low', positive=True), 'volume': _number(row[5], 'volume')}
        if bar['volume'] < 0 or bar['high'] + 0.005 < max(bar['open'], bar['close'], bar['low']) or bar['low'] - 0.005 > min(bar['open'], bar['close']):
            raise MarketDataError(f'{symbol}: 历史OHLC范围或成交量异常')
        bar['volume'] = _lots(symbol, bar['volume'])
        bar['volume_shares'] = bar['volume'] * 100
        if len(row) > 8:
            amount = _optional_number(row[8])
            if amount is not None:
                bar['amount'] = amount * 10000  # Tencent source unit: ten-thousand RMB.
        result.append(bar)
    return sorted(result, key=lambda x: x['date']), block


def _tencent_history(symbol, count, fetched, expected):
    query = urllib.parse.urlencode({'param': f'{symbol},day,,,{count},qfq'})
    adjusted_payload = _json(HISTORY_SOURCE + '?' + query)
    block = adjusted_payload.get('data', {}).get(symbol, {})
    provider_unadjusted = 'qfqday' not in block and 'day' in block
    # Tencent legitimately returns day for no-adjustment windows (including
    # newly listed names). This explicit case is additionally raw-cross-checked.
    adjusted, block = _parse_bars(adjusted_payload, symbol, 'raw' if provider_unadjusted else 'qfq')
    if symbol == 'sh000001':
        raw = adjusted
    else:
        query = urllib.parse.urlencode({'param': f'{symbol},day,,,{count},'})
        raw, _ = _parse_bars(_json(HISTORY_SOURCE + '?' + query), symbol, 'raw')
    raw_by_date = {row['date']: row for row in raw}
    bars = []
    for row in adjusted:
        if row['date'] > expected:
            continue  # Exclude incomplete today and any provider future rows.
        original = raw_by_date.get(row['date'])
        if original is None:
            raise MarketDataError(f'{symbol}: 前复权与原始价格日期不一致({row["date"]})')
        if provider_unadjusted and any(abs(row[field] - original[field]) > 0.0001 for field in ('open', 'high', 'low', 'close')):
            raise MarketDataError(f'{symbol}: 无复权标记的行情与原始OHLC不一致')
        bar = dict(row)
        bar.update({f'raw_{field}': original[field] for field in ('open', 'high', 'low', 'close')})
        bars.append(bar)
    if not bars:
        raise MarketDataError(f'{symbol}: 无已完成交易日数据')
    qt = block.get('qt', {}).get(symbol, [])
    name = qt[1] if isinstance(qt, list) and len(qt) > 1 else symbol
    return {'symbol': symbol, 'name': name, 'bars': bars, 'source': HISTORY_SOURCE,
            'adjustment': 'none' if symbol == 'sh000001' else 'qfq', 'raw_prices': True,
            'provider_adjustment_key': 'day' if provider_unadjusted else 'qfqday',
            'adjustment_note': '前复权请求返回day，已逐日核对与原始OHLC一致' if provider_unadjusted else '腾讯前复权序列',
            'fetched_at': fetched.isoformat(), 'latest_date': bars[-1]['date'],
            'expected_date': expected, 'stale': bars[-1]['date'] != expected,
            'volume_unit': 'lot_100_shares', 'count': len(bars)}


def _parse_eastmoney_bars(payload, symbol):
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('klines'), list) or not data['klines']:
        raise MarketDataError(f'{symbol}: 东方财富历史行情为空或格式错误')
    if str(data.get('code', '')) != symbol[2:]:
        raise MarketDataError(f'{symbol}: 东方财富返回证券代码不一致')
    bars, seen = [], set()
    for line in data['klines']:
        values = line.split(',') if isinstance(line, str) else []
        if len(values) < 7:
            raise MarketDataError(f'{symbol}: 东方财富OHLC行缺列')
        try:
            date = _date(values[0]).isoformat()
        except (TypeError, ValueError) as exc:
            raise MarketDataError(f'{symbol}: 东方财富日期格式错误') from exc
        if date in seen:
            raise MarketDataError(f'{symbol}: 东方财富日期重复')
        seen.add(date)
        bar = {'date': date, 'open': _number(values[1], 'open', positive=True),
               'close': _number(values[2], 'close', positive=True),
               'high': _number(values[3], 'high', positive=True),
               'low': _number(values[4], 'low', positive=True),
               'volume': _number(values[5], 'volume'), 'amount': _number(values[6], 'amount')}
        if bar['volume'] < 0 or bar['amount'] < 0 or bar['high'] + 0.005 < max(bar['open'], bar['close'], bar['low']) or bar['low'] - 0.005 > min(bar['open'], bar['close']):
            raise MarketDataError(f'{symbol}: 东方财富OHLC或成交量异常')
        bar['volume_shares'] = bar['volume'] * 100  # Eastmoney: lots for all boards.
        bars.append(bar)
    return sorted(bars, key=lambda x: x['date']), data.get('name', symbol)


def _eastmoney_history(symbol, count, fetched, expected):
    if not symbol.startswith(('sh', 'sz')):
        raise MarketDataError('东方财富备用历史源暂支持沪深股票')
    sequences = []
    for adjustment in (1, 0):
        params = {'secid': ('1.' if symbol.startswith('sh') else '0.') + symbol[2:],
                  'klt': 101, 'fqt': adjustment, 'beg': f'{fetched.year - 3}0101',
                  'end': '20500101', 'lmt': count,
                  'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': 'f51,f52,f53,f54,f55,f56,f57'}
        url = EASTMONEY_HISTORY_SOURCE + '?' + urllib.parse.urlencode(params, safe=',')
        sequences.append(_parse_eastmoney_bars(_json(url, referer='https://quote.eastmoney.com/'), symbol))
    adjusted, name = sequences[0]
    raw, _ = sequences[1]
    adjusted = [x for x in adjusted if x['date'] <= expected][-count:]
    raw_by_date = {x['date']: x for x in [x for x in raw if x['date'] <= expected][-count:]}
    if not adjusted or set(x['date'] for x in adjusted) != set(raw_by_date):
        raise MarketDataError(f'{symbol}: 东方财富前复权与原始行情日期不一致或没有完整交易日')
    bars = []
    for row in adjusted:
        original = raw_by_date[row['date']]
        bar = dict(row)
        bar.update({f'raw_{field}': original[field] for field in ('open', 'high', 'low', 'close')})
        bars.append(bar)
    return {'symbol': symbol, 'name': name, 'bars': bars, 'source': EASTMONEY_HISTORY_SOURCE,
            'adjustment': 'none' if symbol == 'sh000001' else 'qfq', 'raw_prices': True,
            'provider_adjustment_key': 'fqt=1/fqt=0', 'adjustment_note': '东方财富前复权与原始价格独立获取并对齐',
            'fetched_at': fetched.isoformat(), 'latest_date': bars[-1]['date'],
            'expected_date': expected, 'stale': bars[-1]['date'] != expected,
            'volume_unit': 'lot_100_shares', 'count': len(bars)}


def fetch_history(symbol, count=640):
    symbol = _symbol(symbol)
    count = int(count)
    if not 1 <= count <= 640:
        raise ValueError('历史窗口必须为1至640个交易日')
    fetched = _now()
    expected = last_completed_session(fetched)
    try:
        return _tencent_history(symbol, count, fetched, expected)
    except MarketDataError as primary:
        try:
            result = _eastmoney_history(symbol, count, fetched, expected)
        except MarketDataError as fallback:
            raise MarketDataError(f'{symbol}: 腾讯历史源失败 [{primary}]；东方财富备用源失败 [{fallback}]') from fallback
        result['fallback_from'] = HISTORY_SOURCE
        result['warnings'] = [str(primary)]
        return result


def _flags(symbol, name):
    upper = name.upper().replace(' ', '')
    st = upper.startswith(('ST', '*ST', 'SST', 'S*ST'))
    delisting = '退' in name
    return {'is_st': st, 'is_delisting': delisting,
            'is_new': upper.startswith(('N', 'C')),
            'board': 'star' if symbol.startswith(('sh688', 'sh689')) else 'chinext' if symbol.startswith('sz30') else 'main',
            'actionable': not st and not delisting}


def _parse_quotes(text, requested=None):
    result = {}
    for symbol, line in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"]*)"', text):
        fields = line.split('~')
        if len(fields) < 35:
            continue
        try:
            moment = dt.datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
            row = {'symbol': symbol, 'name': fields[1], 'price': _number(fields[3], 'price'),
                   'previous_close': _number(fields[4], 'previous_close'), 'open': _number(fields[5], 'open'),
                   'volume': _lots(symbol, _number(fields[6], 'volume')), 'change_pct': _number(fields[32], 'change_pct'),
                   'high': _number(fields[33], 'high'), 'low': _number(fields[34], 'low'),
                   'datetime': moment.isoformat(), 'date': moment.date().isoformat(),
                   'source': QUOTE_SOURCE, 'fetched_at': _now().isoformat(), 'volume_unit': 'lot_100_shares'}
        except (ValueError, MarketDataError) as exc:
            raise MarketDataError(f'{symbol}: 报价日期或数字异常') from exc
        row.update(_flags(symbol, row['name']))
        row['volume_shares'] = row['volume'] * 100
        for key, index in (('pe', 39), ('pb', 46), ('market_cap_100m', 45), ('circulating_cap_100m', 44), ('turnover_ratio', 38)):
            row[key] = _optional_number(fields[index]) if len(fields) > index else None
        amount = _optional_number(fields[37]) if len(fields) > 37 else None
        row['amount'] = amount * 10000 if amount is not None else None
        row['suspended'] = row['price'] <= 0 or row['volume'] <= 0
        row['actionable'] = row['actionable'] and not row['suspended']
        result[symbol] = row
    if requested is not None:
        missing = set(requested) - set(result)
        if missing:
            raise MarketDataError(f'腾讯报价缺少{len(missing)}只证券：{",".join(sorted(missing)[:8])}')
    return result


def fetch_quotes(symbols):
    symbols = list(dict.fromkeys(_symbol(x) for x in symbols))
    result = {}
    for offset in range(0, len(symbols), 80):
        batch = symbols[offset:offset + 80]
        text = _request('https://qt.gtimg.cn/q=' + ','.join(batch), encoding='gb18030')
        result.update(_parse_quotes(text, batch))
    return result


def _parse_sse_universe(payload):
    if not isinstance(payload, dict) or payload.get('actionErrors'):
        raise MarketDataError('上交所证券列表返回错误')
    rows = payload.get('result')
    page = payload.get('pageHelp')
    total = page.get('total') if isinstance(page, dict) else None
    if not isinstance(rows, list) or type(total) is not int or not rows or len(rows) != total:
        raise MarketDataError('上交所证券列表不完整，停止全市场更新')
    result, seen = [], set()
    for item in rows:
        if not isinstance(item, dict):
            raise MarketDataError('上交所证券列表包含无效证券记录')
        code, name = str(item.get('A_STOCK_CODE', '')), str(item.get('SEC_NAME_CN', '')).strip()
        if not re.fullmatch(r'(?:60[0135]|68[89])\d{3}', code) or not name:
            raise MarketDataError('上交所证券列表包含无效代码或空名称')
        symbol = 'sh' + code
        if symbol in seen:
            raise MarketDataError('上交所证券列表包含重复证券')
        seen.add(symbol)
        row = {'symbol': symbol, 'name': name, 'source': SSE_LIST_SOURCE,
               'listing_date': item.get('LIST_DATE'), 'industry': item.get('CSRC_CODE_DESC', ''),
               'universe_total': total, 'fetched_at': _now().isoformat()}
        row.update(_flags(symbol, name))
        result.append(row)
    return sorted(result, key=lambda x: x['symbol'])


def _sse_universe():
    params = {'sqlId': 'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L', 'pageHelp.pageSize': '10000',
              'type': 'inParams', 'STOCK_TYPE': '1,8', 'REG_PROVINCE': '', 'CSRC_CODE': '',
              'STOCK_CODE': '', 'COMPANY_STATUS': '2,4,5,7,8', 'isPagination': 'true',
              'pageHelp.pageNo': '1', 'pageHelp.beginPage': '1', 'pageHelp.endPage': '1'}
    query = urllib.parse.urlencode(params)
    errors, failures = [], []
    for route in SSE_LIST_APIS:
        try:
            rows = _parse_sse_universe(_json(route + '?' + query, referer=SSE_LIST_SOURCE))
            for row in rows:
                row['source_api'] = route
            return rows
        except MarketDataError as exc:
            # Both routes are public endpoints on the same official SSE host.
            # Each response must independently pass the full-list validation.
            errors.append(urllib.parse.urlsplit(route).path + ': ' + str(exc)[:240])
            failures.append(exc)
    error_type = MarketNetworkError if all(isinstance(exc, MarketNetworkError) for exc in failures) else MarketDataError
    raise error_type('上交所官方证券列表接口均失败；' + '；'.join(errors))


def _eastmoney_universe(mode):
    fs = 'm:1+t:2,m:1+t:23' if mode == 'shanghai' else 'm:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80'
    result, expected, seen = [], None, set()
    for page in range(1, 101):
        params = {'pn': page, 'pz': 100, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
                  'fid': 'f12', 'fs': fs, 'fields': 'f12,f14,f13'}
        url = 'https://82.push2.eastmoney.com/api/qt/clist/get?' + urllib.parse.urlencode(params, safe=':,')
        payload = _json(url, referer='https://quote.eastmoney.com/')
        data = payload.get('data') or {}
        total, rows = data.get('total'), data.get('diff')
        if not isinstance(total, int) or not isinstance(rows, list) or not rows:
            raise MarketDataError('东方财富列表缺页，未返回部分市场结果')
        if expected is not None and total != expected:
            raise MarketDataError('获取股票列表期间总数变化，请重试')
        expected = total
        for item in rows:
            code, name = str(item.get('f12', '')), str(item.get('f14', '')).strip()
            exchange = 'sh' if item.get('f13') == 1 else 'sz'
            symbol = exchange + code
            if not SYMBOL_RE.fullmatch(symbol) or not name or symbol in seen:
                raise MarketDataError('东方财富证券列表有重复或无效数据')
            seen.add(symbol)
            row = {'symbol': symbol, 'name': name, 'source': 'https://quote.eastmoney.com/',
                   'universe_total': total, 'fetched_at': _now().isoformat()}
            row.update(_flags(symbol, name))
            result.append(row)
        if len(result) == expected:
            return sorted(result, key=lambda x: x['symbol'])
        if len(result) > expected:
            raise MarketDataError('东方财富证券总数校验失败')
        time.sleep(0.12)
    raise MarketDataError('东方财富证券列表超出页数上限')


WATCHLIST = [('sh600519', '贵州茅台'), ('sh600036', '招商银行'), ('sh601318', '中国平安'),
             ('sh600900', '长江电力'), ('sh601166', '兴业银行'), ('sh600030', '中信证券'),
             ('sh601012', '隆基绿能'), ('sh600276', '恒瑞医药'), ('sh600887', '伊利股份'),
             ('sh601398', '工商银行'), ('sh601288', '农业银行'), ('sh601857', '中国石油'),
             ('sh601088', '中国神华'), ('sh600309', '万华化学'), ('sh601899', '紫金矿业'),
             ('sh600031', '三一重工'), ('sh600050', '中国联通'), ('sh600028', '中国石化'),
             ('sh688981', '中芯国际'), ('sh688012', '中微公司'), ('sh601668', '中国建筑'),
             ('sh600104', '上汽集团'), ('sh600585', '海螺水泥'), ('sh601225', '陕西煤业')]


def get_universe(mode='shanghai'):
    """Return full verified universe; default includes SSE Main Board + STAR.

    'all' means Shanghai/Shenzhen A-shares. 'watchlist' is a labeled curated
    starting set, NOT an index constituent list. SSE50 is intentionally rejected
    until an authoritative, current constituent source has been configured.
    """
    if mode == 'shanghai':
        return _sse_universe()
    if mode == 'all':
        return _eastmoney_universe(mode)
    if mode == 'watchlist':
        return [dict(symbol=s, name=n, source='curated_watchlist_not_index', **_flags(s, n)) for s, n in WATCHLIST]
    if mode == 'sse50':
        raise MarketDataError('上证50动态成分源尚未配置，请选择沪市A股或自选；不会用固定名单冒充当前指数成分')
    raise ValueError('不支持的股票范围')
