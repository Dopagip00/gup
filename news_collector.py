"""Source-linked morning headlines, without news-derived return forecasts.

The SSE announcement page's own script uses START_DATE/END_DATE and a grouped
``result`` from queryCompanyBulletinNew.do.  The live formats were inspected on
2026-10-18 against these primary publisher pages:
https://www.sse.com.cn/disclosure/listedinfo/announcement/
https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_listedCompanyInfo_2021.js
https://finance.cctv.com/

``verified`` means the configured sources were fetched, parsed and dated; it
does not mean that every article's claims or company prospects were verified.
Missing sources, incomplete pagination and a late collection fail closed.
No browser, brokerage, account, paid API or third-party dependency is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

CHINA = dt.timezone(dt.timedelta(hours=8))
SSE_PAGE = 'https://www.sse.com.cn/disclosure/listedinfo/announcement/'
SSE_API = 'https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do'
CCTV_PAGE = 'https://finance.cctv.com/'
LOOKBACK_DAYS = 4
PAGE_SIZE = 100
MAX_PAGES = 80
MAX_ARTICLES = 16
MAX_ITEMS = 200
MAX_BYTES = 8 * 1024 * 1024
ALLOWED_HOSTS = {'www.sse.com.cn', 'query.sse.com.cn', 'static.sse.com.cn', 'finance.cctv.com'}
ARTICLE_PATH = re.compile(r'^/(\d{4})/(\d{2})/(\d{2})/ARTI[A-Za-z0-9]+\.shtml$')
SSE_CODE = re.compile(r'^(?:60\d{4}|68\d{4})$')


class NewsError(RuntimeError):
    """A public source/data error without credentials or response bodies."""


def _moment(value=None):
    value = value or dt.datetime.now(CHINA)
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError('now must be a timezone-aware datetime')
    return value.astimezone(CHINA)


def _window(value):
    return dt.time(8) <= value.time().replace(tzinfo=None) < dt.time(9, 30)


def _https(value, base, allowed=ALLOWED_HOSTS):
    url = urllib.parse.urljoin(base, str(value or '').strip())
    try:
        parts = urllib.parse.urlsplit(url)
        # SSE sometimes supplies a literal http:// URL for the same official
        # document host.  Only that known host is upgraded, never arbitrary URLs.
        if parts.scheme == 'http' and parts.hostname in {'www.sse.com.cn', 'static.sse.com.cn'}:
            parts = parts._replace(scheme='https')
        if parts.scheme != 'https' or parts.hostname not in allowed or parts.username or parts.password or parts.port not in (None, 443):
            raise NewsError('article URL is not an approved HTTPS publisher URL')
        return urllib.parse.urlunsplit(parts._replace(fragment=''))
    except (TypeError, ValueError) as exc:
        raise NewsError('invalid publisher URL') from exc


def _get(url, referer):
    url = _https(url, referer)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; HuyanResearch/2.1)',
               'Accept': 'application/json,text/html;q=0.9,*/*;q=0.5', 'Referer': referer}
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=12) as response:
                _https(response.geturl(), referer)
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise NewsError('publisher response exceeded size limit')
            return data.decode('utf-8-sig')
        except (OSError, urllib.error.URLError, UnicodeError) as exc:
            if attempt:
                label = 'HTTP ' + str(exc.code) if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                raise NewsError('source request failed (' + label + ')') from exc
            time.sleep(1)
    raise NewsError('source request failed')


def _dated(value, moment):
    text = str(value or '').strip()
    precision = 'date' if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else 'second'
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise NewsError('publisher timestamp is missing or invalid') from exc
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=CHINA)
    parsed = parsed.astimezone(CHINA)
    cutoff = dt.datetime.combine(moment.date() - dt.timedelta(days=LOOKBACK_DAYS), dt.time(), CHINA)
    if not cutoff <= parsed <= moment:
        return None, precision
    # Preserve date-only precision; do not invent an actual midnight publication.
    return text if precision == 'date' else parsed.isoformat(), precision


def _rows(value):
    if not isinstance(value, list):
        raise NewsError('SSE announcement result is not a list')
    for row in value:
        if isinstance(row, list):
            yield from _rows(row)
        elif isinstance(row, dict):
            yield row
        else:
            raise NewsError('SSE announcement row has an unknown format')


def parse_announcements(payload, moment):
    """Parse the official grouped JSON, preserving date-only source precision."""
    moment = _moment(moment)
    if not isinstance(payload, dict) or 'result' not in payload:
        raise NewsError('SSE announcement response is missing result')
    items, invalid = [], 0
    for row in _rows(payload['result']):
        code = str(row.get('SECURITY_CODE', '')).strip()
        # B shares, funds and bonds are outside the Shanghai A-share remit.
        if not SSE_CODE.fullmatch(code):
            continue
        try:
            title = unescape(str(row.get('TITLE') or '')).strip()
            published, precision = _dated(row.get('SSEDATE'), moment)
            if not title:
                raise NewsError('SSE announcement title is missing')
            url = _https(row.get('URL'), SSE_PAGE)
            if not row.get('URL'):
                raise NewsError('SSE announcement document URL is missing')
            if published:
                items.append({'title': title, 'url': url, 'published_at': published,
                              'published_precision': precision, 'source': '上海证券交易所·上市公司公告',
                              'kind': 'announcement', 'symbol': 'sh' + code,
                              'company': str(row.get('SECURITY_NAME') or '').strip(),
                              'verification': '官方公告目录与披露日期；未自动核验PDF正文'})
        except NewsError:
            invalid += 1
    return items, invalid


def _sse(moment):
    start = (moment.date() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    items, groups, total, pages, invalid = [], 0, None, 0, 0
    page_fingerprints = set()
    for page in range(1, MAX_PAGES + 1):
        params = {'isPagination': 'true', 'pageHelp.pageSize': PAGE_SIZE,
                  'pageHelp.pageNo': page, 'pageHelp.beginPage': page,
                  'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
                  'START_DATE': start, 'END_DATE': moment.date().isoformat(),
                  'SECURITY_CODE': '', 'TITLE': '', 'BULLETIN_TYPE': '', 'stockType': ''}
        try:
            payload = json.loads(_get(SSE_API + '?' + urllib.parse.urlencode(params), SSE_PAGE))
        except (json.JSONDecodeError, TypeError) as exc:
            raise NewsError('SSE returned invalid JSON') from exc
        part, bad = parse_announcements(payload, moment)
        info = payload.get('pageHelp')
        if not isinstance(info, dict):
            raise NewsError('SSE pagination metadata is missing')
        try:
            reported_total = int(info['total'])
            reported_page = int(info['pageNo'])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise NewsError('SSE pagination metadata is invalid') from exc
        if reported_total < 0 or reported_page != page:
            raise NewsError('SSE returned inconsistent pagination')
        # A changing announcement count means a snapshot may skip records while
        # paging.  Do not silently call this complete; the next run can retry.
        if total is not None and total != reported_total:
            raise NewsError('SSE announcement count changed during collection; retry required')
        total = reported_total
        current_groups = len(payload['result'])
        if current_groups != min(PAGE_SIZE, total - groups):
            raise NewsError('SSE announcement page is incomplete')
        fingerprint = json.dumps(payload['result'], ensure_ascii=False, sort_keys=True)
        if current_groups and fingerprint in page_fingerprints:
            raise NewsError('SSE repeated a page; announcement scan incomplete')
        page_fingerprints.add(fingerprint)
        groups += current_groups
        pages += 1
        items.extend(part)
        invalid += bad
        if groups >= total:
            break
        time.sleep(.3)
    else:
        raise NewsError('SSE pagination limit reached; announcement scan incomplete')
    if invalid:
        raise NewsError('SSE contains ' + str(invalid) + ' unparseable A-share announcement records')
    return items, {'source': '上海证券交易所·上市公司公告', 'url': SSE_PAGE, 'status': 'complete',
                   'window_start': start, 'window_end': moment.date().isoformat(),
                   'groups_scanned': groups, 'groups_total': total, 'pages': pages,
                   'accepted_items': len(items), 'scope': '沪市A股公告目录标题；不表示已读取每份公告正文'}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.active = [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs = dict(attrs)
            self.active = [attrs.get('href', ''), []]

    def handle_data(self, value):
        if self.active is not None:
            self.active[1].append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active is not None:
            self.links.append((self.active[0], ''.join(self.active[1]).strip()))
            self.active = None


def parse_cctv_links(html, moment):
    """Only dated articles on the publisher's own HTTPS hostname are followed."""
    parser = _Links()
    parser.feed(html)
    links = {}
    for href, title in parser.links:
        if not title:
            continue
        try:
            url = _https(href, CCTV_PAGE, {'finance.cctv.com'})
            match = ARTICLE_PATH.fullmatch(urllib.parse.urlsplit(url).path)
            if not match:
                continue
            day = dt.date(*map(int, match.groups()))
        except (NewsError, ValueError):
            continue
        if moment.date() - dt.timedelta(days=LOOKBACK_DAYS) <= day <= moment.date():
            links[url] = title
    return sorted(links.items(), key=lambda pair: pair[0], reverse=True)[:MAX_ARTICLES]


def parse_cctv_article(html, url, title, moment):
    url = _https(url, CCTV_PAGE, {'finance.cctv.com'})
    # This is publisher-provided article metadata, not a date guessed from its URL.
    found = re.search(r'\b(?:var\s+)?publishDate\s*=\s*[\"\'](\d{14})[\"\']', html)
    if not found:
        raise NewsError('CCTV article publication metadata is missing')
    try:
        published = dt.datetime.strptime(found[1], '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
    except ValueError as exc:
        raise NewsError('CCTV article publication metadata is invalid') from exc
    stamp, _ = _dated(published.isoformat(), moment)
    if not stamp:
        return None
    return {'title': title, 'url': url, 'published_at': stamp, 'published_precision': 'second',
            'source': '央视网财经', 'kind': 'market_news',
            'verification': '发布者页面与文章发布时间；未独立核验报道中的全部事实'}


def _cctv(moment):
    links = parse_cctv_links(_get(CCTV_PAGE, CCTV_PAGE), moment)
    if not links:
        raise NewsError('CCTV finance page has no recent parseable article links')

    def article(pair):
        url, title = pair
        try:
            return parse_cctv_article(_get(url, CCTV_PAGE), url, title, moment), None
        except NewsError as exc:
            return None, str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(article, links))
    items = [item for item, _ in results if item]
    failures = [error for _, error in results if error]
    if failures:
        raise NewsError('CCTV finance article collection incomplete (' + str(len(failures)) + ' errors; ' + failures[0] + ')')
    if not items:
        raise NewsError('CCTV finance articles are future-dated or outside lookback window')
    return items, {'source': '央视网财经', 'url': CCTV_PAGE, 'status': 'complete',
                   'articles_checked': len(links), 'accepted_items': len(items),
                   'scope': '财经首页最多16条近期文章；不是全部财经新闻或实时快讯'}


def risk_exclusions(items):
    """Conservative vetoes from explicit official titles and structured codes.

    A macro headline or a guessed company-name match never creates a stock tag.
    Titles mentioning removal/resumption are not treated as continuing risk.
    This is a prompt for human review, not a finding from the unread PDF body.
    """
    risks = []
    for item in items:
        if item.get('kind') != 'announcement' or not re.fullmatch(r'sh(?:60\d{4}|68\d{4})', str(item.get('symbol', ''))):
            continue
        title = item.get('title', '')
        checks = [('停牌', '停牌' in title and '复牌' not in title),
                  ('立案调查', bool(re.search(r'(?:公司被中国证监会立案|收到.{0,12}立案告知书|因涉嫌.{0,16}被.{0,8}立案)', title))),
                  ('实施风险警示', bool(re.search(r'(?:实施|被实施)(?:退市|其他)风险警示', title)) and not re.search(r'撤销|解除|申请撤', title)),
                  ('终止上市', '终止上市' in title and not re.search(r'撤销|不予|申请撤', title)),
                  ('进入退市整理期', '进入退市整理期' in title),
                  ('债务逾期', bool(re.search(r'债务逾期|无法按期偿还', title)) and not re.search(r'解除|已偿还|全部偿还', title))]
        matched = [label for label, present in checks if present]
        if matched:
            risks.append({'symbol': item['symbol'], 'reason': '官方公告标题涉及' + '、'.join(matched) + '，暂停新买入并人工复核原文。',
                          'title': title, 'url': item['url'], 'published_at': item['published_at'],
                          'source': item['source'], 'evidence_scope': '公告目录标题及证券代码；未分析PDF正文'})
    return risks


def collect(now=None):
    """Return a daily_plan-compatible morning_news document, without writing KV."""
    moment = _moment(now)
    record = {'date': moment.date().isoformat(), 'as_of': moment.isoformat(),
              'timezone': 'Asia/Shanghai', 'status': 'unavailable', 'items': [],
              'risk_exclusions': [], 'errors': [], 'sources': [],
              'verification_scope': '来源与发布时间解析核验，不代表已独立核实全部报道事实或已阅读全部公告正文。',
              'lookback_days': LOOKBACK_DAYS}
    if not _window(moment):
        record['errors'].append('采集时刻不在北京时间08:00至09:30盘前窗口内。')
        return record
    all_items = []
    for name, url, fetch in [('上海证券交易所·上市公司公告', SSE_PAGE, _sse), ('央视网财经', CCTV_PAGE, _cctv)]:
        try:
            items, source = fetch(moment)
            all_items.extend(items)
            record['sources'].append(source)
        except NewsError as exc:
            record['errors'].append(name + '：' + str(exc))
            record['sources'].append({'source': name, 'url': url, 'status': 'failed', 'error': str(exc)})
    # In production use completion time; passing now is an explicit deterministic
    # replay/testing boundary, never a fabricated successful earlier timestamp.
    finished = _moment() if now is None else moment
    record['as_of'] = finished.isoformat()
    if finished.date() != moment.date() or not _window(finished):
        record['errors'].append('采集完成已超出当日盘前窗口，不能作为今日新买入核验。')
    unique = {item['url']: item for item in all_items}
    all_items = sorted(unique.values(), key=lambda item: item['published_at'], reverse=True)
    record['risk_exclusions'] = risk_exclusions(all_items)
    # Display both source types even on heavy announcement days.  Risk screening
    # covers the complete successfully scanned list before this display cap.
    market = [item for item in all_items if item['kind'] == 'market_news']
    announcements = [item for item in all_items if item['kind'] == 'announcement']
    record['items'] = sorted(market + announcements[:max(0, MAX_ITEMS - len(market))], key=lambda item: item['published_at'], reverse=True)
    record['items_scanned'] = len(all_items)
    record['items_displayed'] = len(record['items'])
    if not record['errors'] and record['items']:
        record['status'] = 'verified'
    return record
