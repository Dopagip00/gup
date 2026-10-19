"""Official SSE published suspension records for a caller-confirmed session.

API and fields were verified from the SSE page's declared query script:
https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_tradeTip_2021.js
Live schema receipt: 2026-10-18, GW_PL_JYTS_TFPXX, page size 25.
This adapter neither infers a next trading day nor establishes tradeability.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import urllib.parse
import urllib.request

TZ = dt.timezone(dt.timedelta(hours=8))
SOURCE_URL = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/stock/'
ENDPOINT = 'https://query.sse.com.cn/commonSoaQuery.do'
QUERY_SCRIPT_URL = 'https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_tradeTip_2021.js'
SQL_ID = 'GW_PL_JYTS_TFPXX'
PAGE_SIZE = 25
MAX_PAGES = 20
MAX_BYTES = 2_000_000
_STOCK = re.compile(r'^(?:60[0135]\d{3}|68[89]\d{3})$')
_TIME_NAMES = {'WH': '全天', '930': '9:30-10:30', 'AM': '上午', 'PM': '下午', '': '官方未列具体时段'}
_REQUIRED = ('productCode', 'productName', 'controlType', 'startStopDate', 'endStopDate',
             'stopTime', 'stopReason', 'endStopReason', 'type')


class SourceUnavailable(ValueError):
    """Official response could not establish a complete, correctly scoped list."""


def _clock():
    return dt.datetime.now(TZ)


def _day(value, *, optional=False):
    if optional and value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{8}', value):
        raise SourceUnavailable('官方停牌日期必须为YYYYMMDD字符串')
    return dt.datetime.strptime(value, '%Y%m%d').date()


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise SourceUnavailable(f'官方分页{label}缺失或不是非负整数')
    return value


def _request(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; HuyanResearch/2.2)',
        'Referer': SOURCE_URL, 'Accept': 'application/json',
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != 'https' or final.hostname != 'query.sse.com.cn':
            raise SourceUnavailable('官方查询被重定向到非预期地址')
        body = response.read(MAX_BYTES + 1)
        receipt = {'url': url, 'final_url': response.url, 'http_status': response.status,
                   'content_type': response.headers.get('Content-Type', '')}
    if receipt['http_status'] != 200 or len(body) > MAX_BYTES:
        raise SourceUnavailable('官方查询状态或响应长度不符合要求')
    if 'application/json' not in receipt['content_type'].lower():
        raise SourceUnavailable('官方查询未返回JSON；HTML空壳不作为成功')
    receipt.update(response_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
    return json.loads(body.decode('utf-8')), receipt


def _page(payload, requested_page):
    if not isinstance(payload, dict) or payload.get('sqlId') != SQL_ID:
        raise SourceUnavailable('官方响应SQL标识不匹配')
    if payload.get('actionErrors') != [] or payload.get('fieldErrors') != {}:
        raise SourceUnavailable('官方响应含错误或错误字段缺失')
    if payload.get('isPagination') not in ('true', True):
        raise SourceUnavailable('官方分页模式未确认')
    result, help_ = payload.get('result'), payload.get('pageHelp')
    if not isinstance(result, list) or not isinstance(help_, dict) or help_.get('data') != result:
        raise SourceUnavailable('官方result与pageHelp.data缺失或不一致')
    page_no = _integer(help_.get('pageNo'), 'pageNo')
    page_size = _integer(help_.get('pageSize'), 'pageSize')
    total = _integer(help_.get('total'), 'total')
    page_count = _integer(help_.get('pageCount'), 'pageCount')
    if page_no != requested_page or page_size != PAGE_SIZE:
        raise SourceUnavailable('官方返回页码或页长与请求不符')
    expected_pages = math.ceil(total / page_size)
    if page_count != expected_pages and not (total == 0 and page_count == 1):
        raise SourceUnavailable('官方总数、页长和总页数不一致')
    expected_rows = max(0, min(page_size, total - (page_no - 1) * page_size))
    if len(result) != expected_rows:
        raise SourceUnavailable('官方当前页行数与总数不一致')
    if page_count > MAX_PAGES:
        raise SourceUnavailable('官方停牌列表超过有界分页上限，不能宣称完整')
    return result, total, page_count


def _record(row, session):
    if not isinstance(row, dict) or any(key not in row or not isinstance(row[key], str) for key in _REQUIRED):
        raise SourceUnavailable('官方停牌记录缺失已核验字段或字段类型错误')
    code = row['productCode']
    if not re.fullmatch(r'\d{6}', code) or not row['productName'].strip():
        raise SourceUnavailable('官方证券代码或简称无效')
    start, end = _day(row['startStopDate']), _day(row['endStopDate'], optional=True)
    if start > session or (end is not None and (end < start or end < session)):
        raise SourceUnavailable('官方记录不在所请求交易日范围内；不能确认查询过滤生效')
    if row['type'] not in ('LXTP', 'LSTP') or row['stopTime'] not in _TIME_NAMES:
        raise SourceUnavailable('官方停牌类型或时段出现尚未核验值')
    if row['controlType'] not in ('TR', 'CP', 'GP', 'CB', 'GB', 'CR', 'GR'):
        raise SourceUnavailable('官方业务控制类型出现尚未核验值')
    if _STOCK.fullmatch(code) and row['controlType'] != 'TR':
        raise SourceUnavailable('股票代码与官方业务控制类型不一致')
    # The source also publishes bond conversion/put events. Six digit codes
    # alone must never map 110/113 bond events into an equity exclusion.
    is_stock = bool(_STOCK.fullmatch(code)) and row['controlType'] == 'TR'
    instrument = 'shanghai_a_share' if is_stock else 'non_equity_or_unverified_control'
    identity = json.dumps({key: row[key] for key in _REQUIRED}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {**{key: row[key] for key in _REQUIRED},
            'record_id': hashlib.sha256(identity.encode('utf-8')).hexdigest(),
            'symbol': 'sh' + code if is_stock else None, 'instrument_scope': instrument,
            'session_date': session.isoformat(), 'start_date': start.isoformat(),
            'end_date_as_published': end.isoformat() if end else None,
            'time_as_published': _TIME_NAMES[row['stopTime']],
            'resumption_confirmed': False, 'source_url': SOURCE_URL,
            'published_at': None}


def collect(session_date, now=None):
    """Return complete / empty_unconfirmed / unavailable with source receipts.

    Caller must establish the requested D1 from the official exchange calendar.
    A complete result means only the requested published suspension list was
    retrieved and validated. Missing publication times prevent historical PIT use.
    Even a structurally complete empty response never clears a new recommendation.
    """
    result = {'status': 'unavailable', 'session_date': session_date, 'as_of': None,
              'collection_started_at': None,
              'source_url': SOURCE_URL, 'records': [], 'risk_exclusions': [], 'errors': [],
              'scope': {'exchange': 'SSE', 'source': 'official_published_suspension_records',
                        'caller_must_confirm_official_session': True,
                        'publication_timestamps_available': False, 'historical_information_set_safe': False,
                        'establishes_tradeability': False, 'source_scope_complete': False,
                        'absence_meaning': '仅在完整正确的非空当日官方列表中未发现该股票停牌记录；不代表没有其他风险或保证可交易',
                        'end_date_meaning': '只保留官方停牌终止字段，不推断复牌；摘牌和终止上市也可能出现在结束原因中'},
              'receipt': {'query_script_url': QUERY_SCRIPT_URL, 'sql_id': SQL_ID, 'page_size': PAGE_SIZE, 'pages': []}}
    try:
        stamp = now if now is not None else _clock()
        if not isinstance(stamp, dt.datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise SourceUnavailable('抓取时间必须包含明确时区，不能使用naive时间')
        stamp = stamp.astimezone(TZ)
        result['as_of'] = stamp.isoformat()
        result['collection_started_at'] = stamp.isoformat()
        if not isinstance(session_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', session_date):
            raise SourceUnavailable('目标交易日必须为YYYY-MM-DD')
        session = dt.date.fromisoformat(session_date)
        if session < stamp.date():
            raise SourceUnavailable('不使用事后抓取的停牌列表为过去预测补造当时信息')
        compact = session.strftime('%Y%m%d')
        result['scope'].update(query_start_stop_date=compact, query_end_stop_date=compact)
        expected_total = expected_pages = None
        normalized, ids = [], set()
        page = 1
        while True:
            params = {'isPagination': 'true', 'sqlId': SQL_ID, 'pageHelp.pageSize': str(PAGE_SIZE),
                      'pageHelp.pageNo': str(page), 'productCode': '', 'keyWords': '',
                      'startStopDate': compact, 'endStopDate': compact}
            url = ENDPOINT + '?' + urllib.parse.urlencode(params)
            payload, receipt = _request(url)
            received_at = stamp if now is not None else _clock().astimezone(TZ)
            rows, total, pages = _page(payload, page)
            receipt.update(page_no=page, page_count=pages, total=total, fetched_at=received_at.isoformat())
            result['receipt']['pages'].append(receipt)
            if expected_total is None:
                expected_total, expected_pages = total, pages
            elif (total, pages) != (expected_total, expected_pages):
                raise SourceUnavailable('官方列表在分页期间变动，不能确认完整性')
            for row in rows:
                record = _record(row, session)
                if record['record_id'] in ids:
                    raise SourceUnavailable('官方列表出现重复记录，不能确认分页完整性')
                ids.add(record['record_id'])
                normalized.append(record)
            if page >= pages:
                break
            page += 1
        if len(normalized) != expected_total:
            raise SourceUnavailable('官方全列表行数与总数不一致')
        result['records'] = normalized
        result['receipt'].update(total=expected_total, page_count=expected_pages,
                                 records_sha256=hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest())
        if not normalized:
            result.update(status='empty_unconfirmed', errors=['官方返回空列表；未据此确认股票无停牌，需复核发布状态'])
            return result
        by_symbol = {}
        for record in normalized:
            if record['symbol'] is None:
                continue
            entry = by_symbol.setdefault(record['symbol'], {
                'symbol': record['symbol'], 'session_date': session_date, 'source_url': SOURCE_URL,
                'reason': '上交所已公布该交易日范围内的停牌记录；暂停新买入研究，不推断可成交或复牌', 'record_ids': []})
            entry['record_ids'].append(record['record_id'])
        result.update(status='complete', risk_exclusions=list(by_symbol.values()))
        result['scope']['source_scope_complete'] = True
    except Exception as exc:
        result.update(status='unavailable', records=[], risk_exclusions=[])
        result['errors'].append(type(exc).__name__ + ': ' + str(exc))
    finally:
        if now is None:
            result['as_of'] = _clock().astimezone(TZ).isoformat()
    return result
