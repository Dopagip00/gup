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
import hashlib
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
PBC_PAGE = 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html'
CSRC_PAGE = 'https://www.csrc.gov.cn/'
SSE_NEWS_PAGE = 'https://www.sse.com.cn/aboutus/mediacenter/hotandd/'
BRIEF_SOURCES=(('中国人民银行·新闻',PBC_PAGE),('中国证监会·要闻',CSRC_PAGE),('上海证券交易所·热点动态',SSE_NEWS_PAGE))
BRIEF_HOSTS={'www.pbc.gov.cn','www.csrc.gov.cn','www.sse.com.cn'}
BRIEF_MAX_BYTES=2*1024*1024
LOOKBACK_DAYS = 4
MAX_LOOKBACK_DAYS = 31
VERIFICATION_PROTOCOL = 'official-sources-v3-risk31-v1'
RISK_PROTOCOL = 'sse-risk-title-observation-v1'
RISK_LOOKBACK_DAYS = 31
RISK_QUERIES = ('股票交易', '停牌', '复牌', '立案', '风险警示', '终止上市', '退市整理', '债务逾期', '无法按期偿还')
RISK_SEMANTICS = 'rolling_research_observation_not_risk_resolution'
PAGE_SIZE = 100
MAX_PAGES = 80
MAX_ARTICLES = 16
MAX_ITEMS = 200
MAX_BYTES = 8 * 1024 * 1024
SSE_HOSTS = {'www.sse.com.cn', 'query.sse.com.cn', 'static.sse.com.cn'}
SSE_DOCUMENT_HOSTS = {'www.sse.com.cn', 'static.sse.com.cn'}
CCTV_HOSTS = {'finance.cctv.com'}
ALLOWED_HOSTS = SSE_HOSTS | CCTV_HOSTS
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


def _coverage_start(moment, value=None):
    """Cover at least four days, including a supplied last completed session.

    The caller must derive that session from the official trading calendar.  A
    bounded interval prevents an unknown/stale calendar from quietly requesting
    a huge scan and being described as current news coverage.
    """
    start = moment.date() - dt.timedelta(days=LOOKBACK_DAYS)
    if value is not None:
        if isinstance(value, dt.datetime):
            raise ValueError('coverage_start must be a calendar date, not a timestamp')
        try:
            supplied = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError('coverage_start must be an ISO calendar date') from exc
        if supplied > moment.date() or supplied < moment.date() - dt.timedelta(days=MAX_LOOKBACK_DAYS):
            raise ValueError('coverage_start is future-dated or older than the supported news window')
        start = min(start, supplied)
    return start


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
    # A redirect to a different approved publisher is still a source mismatch.
    # Never label a CCTV response as an SSE announcement, or vice versa.
    hosts = SSE_HOSTS if urllib.parse.urlsplit(referer).hostname in SSE_HOSTS else CCTV_HOSTS
    url = _https(url, referer, hosts)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; HuyanResearch/2.1)',
               'Accept': 'application/json,text/html;q=0.9,*/*;q=0.5', 'Referer': referer}
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=12) as response:
                _https(response.geturl(), referer, hosts)
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


def _dated(value, moment, window_start=None):
    text = str(value or '').strip()
    precision = 'date' if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else 'second'
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise NewsError('publisher timestamp is missing or invalid') from exc
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=CHINA)
    parsed = parsed.astimezone(CHINA)
    cutoff = dt.datetime.combine(_coverage_start(moment, window_start), dt.time(), CHINA)
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


def _pagination_integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d+', value):
        return int(value)
    raise ValueError('pagination count is not an integer')


def parse_announcements(payload, moment, window_start=None):
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
            published, precision = _dated(row.get('SSEDATE'), moment, window_start)
            if not title:
                raise NewsError('SSE announcement title is missing')
            url = _https(row.get('URL'), SSE_PAGE, SSE_DOCUMENT_HOSTS)
            if not row.get('URL'):
                raise NewsError('SSE announcement document URL is missing')
            if published:
                items.append({'title': title, 'url': url, 'published_at': published,
                              'published_precision': precision, 'source': '上海证券交易所·上市公司公告',
                              'kind': 'announcement', 'symbol': 'sh' + code,
                              'source_url': SSE_PAGE,
                              'company': str(row.get('SECURITY_NAME') or '').strip(),
                              'verification': '官方公告目录与披露日期；未自动核验PDF正文'})
        except NewsError:
            invalid += 1
    return items, invalid


def _sse(moment, window_start=None, *, title_query=''):
    start = _coverage_start(moment, window_start).isoformat()
    items, groups, total, pages, invalid = [], 0, None, 0, 0
    page_fingerprints, group_fingerprints = set(), set()
    for page in range(1, MAX_PAGES + 1):
        params = {'isPagination': 'true', 'pageHelp.pageSize': PAGE_SIZE,
                  'pageHelp.pageNo': page, 'pageHelp.beginPage': page,
                  'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
                  'START_DATE': start, 'END_DATE': moment.date().isoformat(),
                  'SECURITY_CODE': '', 'TITLE': title_query, 'BULLETIN_TYPE': '', 'stockType': ''}
        try:
            payload = json.loads(_get(SSE_API + '?' + urllib.parse.urlencode(params), SSE_PAGE))
        except (json.JSONDecodeError, TypeError) as exc:
            raise NewsError('SSE returned invalid JSON') from exc
        part, bad = parse_announcements(payload, moment, start)
        if title_query:
            # The official API filters groups, and may attach an unrelated
            # companion document to a matching group. Every group must match;
            # only matching documents belong to this declared title search.
            if any(not any(title_query in str(row.get('TITLE') or '') for row in _rows([group]))
                   for group in payload['result']):
                raise NewsError('SSE did not honor the risk title filter')
            part = [item for item in part if title_query in item['title']]
        info = payload.get('pageHelp')
        if not isinstance(info, dict):
            raise NewsError('SSE pagination metadata is missing')
        try:
            reported_total = _pagination_integer(info['total'])
            reported_page = _pagination_integer(info['pageNo'])
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
        # Total counts alone cannot detect shifting offsets: A/B then B/C can
        # have an unchanged total while omitting D.  Reject an overlapping group.
        for group in payload['result']:
            group_fingerprint = json.dumps(group, ensure_ascii=False, sort_keys=True)
            if group_fingerprint in group_fingerprints:
                raise NewsError('SSE repeated an announcement group; scan incomplete')
            group_fingerprints.add(group_fingerprint)
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
                   'title_query': title_query,
                   'accepted_items': len(items), 'scope': '沪市A股公告目录标题；不表示已读取每份公告正文'}


def _risk_window(moment, *, fixed_observed=None):
    """Scan every page of nine declared official title searches over 31 days.

    This is a rolling research observation window, not an issuer risk lifetime.
    Neither a later resumption/removal title nor leaving the window proves that
    a previous risk has ended. No PDF-body interpretation is performed.
    """
    start = (moment.date() - dt.timedelta(days=RISK_LOOKBACK_DAYS)).isoformat()
    receipts, by_url = [], {}
    for keyword in RISK_QUERIES:
        try:
            items, receipt = _sse(moment, start, title_query=keyword)
            if not isinstance(receipt, dict) or receipt.get('status') != 'complete':
                raise NewsError('risk title query did not produce a complete receipt')
        except NewsError as exc:
            raise NewsError('31-day SSE risk query [' + keyword + '] incomplete: ' + str(exc)) from exc
        observed = fixed_observed or _moment()
        receipt = dict(receipt, keyword=keyword, source_url=SSE_PAGE, title_query=keyword,
                       window_start=start, window_end=moment.date().isoformat(), observed_at=observed.isoformat())
        receipts.append(receipt)
        for item in items:
            item = dict(item, source_url=SSE_PAGE, observed_at=observed.isoformat(), query_keyword=keyword)
            original = by_url.get(item['url'])
            if original and any(original.get(key) != item.get(key) for key in ('symbol', 'title', 'published_at')):
                raise NewsError('SSE risk queries returned conflicting announcement identities')
            by_url[item['url']] = item
    items = sorted(by_url.values(), key=lambda item: item['published_at'], reverse=True)
    risks = risk_exclusions(items)
    for risk in risks:
        risk.update(observation_status='research_window_veto', resolution_status='not_determined',
                    observation_window_start=start, observation_window_end=moment.date().isoformat())
    return {'protocol': RISK_PROTOCOL, 'status': 'complete', 'source_url': SSE_PAGE,
            'window_start': start, 'window_end': moment.date().isoformat(), 'lookback_days': RISK_LOOKBACK_DAYS,
            'observed_at': (fixed_observed or _moment()).isoformat(), 'queries': receipts,
            'items_scanned': len(items), 'risk_exclusions': risks, 'semantics': RISK_SEMANTICS,
            'scope': '固定9项标题关键词的沪市A股官方公告目录完整分页；不是全部潜在风险或PDF正文核验。',
            'expiry_note': '31天仅为滚动研究观察窗口；未自动判定风险解除。窗口外不表示安全，复牌/撤销标题也不自动解除此前观察。'}


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


def parse_cctv_links(html, moment, window_start=None):
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
        if _coverage_start(moment, window_start) <= day <= moment.date():
            links[url] = title
    return sorted(links.items(), key=lambda pair: pair[0], reverse=True)[:MAX_ARTICLES]


def parse_cctv_article(html, url, title, moment, window_start=None):
    url = _https(url, CCTV_PAGE, {'finance.cctv.com'})
    # This is publisher-provided article metadata, not a date guessed from its URL.
    found = re.search(r'\b(?:var\s+)?publishDate\s*=\s*[\"\'](\d{14})[\"\']', html)
    if not found:
        raise NewsError('CCTV article publication metadata is missing')
    try:
        published = dt.datetime.strptime(found[1], '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
    except ValueError as exc:
        raise NewsError('CCTV article publication metadata is invalid') from exc
    stamp, _ = _dated(published.isoformat(), moment, window_start)
    if not stamp:
        return None
    return {'title': title, 'url': url, 'published_at': stamp, 'published_precision': 'second',
            'source': '央视网财经', 'kind': 'market_news',
            'source_url': CCTV_PAGE,
            'verification': '发布者页面与文章发布时间；未独立核验报道中的全部事实'}


def _cctv(moment, window_start=None):
    start = _coverage_start(moment, window_start).isoformat()
    links = parse_cctv_links(_get(CCTV_PAGE, CCTV_PAGE), moment, start)
    if not links:
        raise NewsError('CCTV finance page has no recent parseable article links')

    def article(pair):
        url, title = pair
        try:
            return parse_cctv_article(_get(url, CCTV_PAGE), url, title, moment, start), None
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
                   'window_start': start, 'window_end': moment.date().isoformat(),
                   'articles_checked': len(links), 'accepted_items': len(items),
                   'scope': '财经首页最多16条近期文章；不是全部财经新闻或实时快讯',
                   'selection': '首页文章按URL日期排序取样；同日文章URL顺序不代表发布时间先后'}


def risk_exclusions(items):
    """Conservative vetoes from explicit official titles and structured codes.

    A macro headline or a guessed company-name match never creates a stock tag.
    A bare resumption/removal title does not create a new risk tag or automatically
    release a previous event in the observation window. A title about a
    suspension, delay of resumption or an application to remove a warning is
    still a veto until the original document has been reviewed.
    This is a prompt for human review, not a finding from the unread PDF body.
    """
    risks = []
    for item in items:
        if item.get('kind') != 'announcement' or not re.fullmatch(r'sh(?:60\d{4}|68\d{4})', str(item.get('symbol', ''))):
            continue
        title = item.get('title', '')
        checks = [('股票交易异常波动', bool(re.search(r'股票交易.{0,8}(?:异常波动|严重异常)', title))),
                  ('股票交易风险提示', bool(re.search(r'股票交易.{0,8}风险提示', title))),
                  ('停牌', bool(re.search(r'停牌|(?:延期|暂缓|无法|不予|未能|不能|未获).{0,5}复牌', title)) and not re.search(r'(?:不|无需)停牌', title)),
                  ('立案调查', bool(re.search(r'(?:立案调查|立案告知书|被.{0,24}立案)', title))),
                  ('实施风险警示', bool(re.search(r'(?:实施|被实施)(?:退市|其他)风险警示', title))),
                  ('风险警示待撤销', bool(re.search(r'(?:申请|拟申请)(?:撤销|解除)(?:退市|其他)风险警示', title))),
                  ('终止上市', '终止上市' in title and not re.search(r'撤销|不予|申请撤', title)),
                  ('进入退市整理期', '进入退市整理期' in title),
                  ('债务逾期', bool(re.search(r'债务逾期|无法按期偿还', title)) and not re.search(r'解除|已偿还|全部偿还', title))]
        matched = [label for label, present in checks if present]
        if matched:
            risks.append({'symbol': item['symbol'], 'reason': '官方公告标题涉及' + '、'.join(matched) + '，暂停新买入并人工复核原文。',
                          'title': title, 'url': item['url'], 'published_at': item['published_at'],
                          'published_precision': item.get('published_precision'),
                          'source': item['source'], 'source_url': item.get('source_url', SSE_PAGE),
                          'observed_at': item.get('observed_at'), 'query_keyword': item.get('query_keyword'),
                          'evidence_scope': '公告目录标题及证券代码；未分析PDF正文'})
    return risks


def _brief_get(url,base):
    """One bounded request, exact publisher redirects and normal TLS checks."""
    host=urllib.parse.urlsplit(base).hostname
    url=_https(url,base,{host})
    if urllib.parse.urlsplit(url).query:raise NewsError('brief URL must not contain a query')
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; HuyanResearch/2.6)','Referer':base})
        with urllib.request.urlopen(request,timeout=6) as response:
            _https(response.geturl(),base,{host});data=response.read(BRIEF_MAX_BYTES+1)
        if len(data)>BRIEF_MAX_BYTES:raise NewsError('brief source exceeded response limit')
        return data.decode('utf-8-sig')
    except (OSError,urllib.error.URLError,UnicodeError) as exc:
        code='HTTP '+str(exc.code) if isinstance(exc,urllib.error.HTTPError) else type(getattr(exc,'reason',exc)).__name__
        raise NewsError('official headline source request failed ('+code+')') from exc


class _HeadlineRows(HTMLParser):
    """Keep link/date associations inside official list rows, never the page date."""
    def __init__(self):super().__init__(convert_charrefs=True);self.stack=[];self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('li','tr','dd'):self.stack.append({'tag':tag,'text':[],'links':[],'active':None})
        if tag=='a' and self.stack:
            attrs=dict(attrs);self.stack[-1]['active']=[attrs.get('href',''),[],attrs.get('title','')]
    def handle_data(self,value):
        for row in self.stack:row['text'].append(value)
        if self.stack and self.stack[-1]['active'] is not None:self.stack[-1]['active'][1].append(value)
    def handle_endtag(self,tag):
        if tag=='a' and self.stack and self.stack[-1]['active'] is not None:
            link=self.stack[-1]['active'];self.stack[-1]['links'].append((link[0],link[2] or ''.join(link[1]).strip()));self.stack[-1]['active']=None
        if self.stack and tag==self.stack[-1]['tag']:
            self.rows.append(self.stack.pop())


def parse_official_headlines(html,source,url,moment,window_start=None):
    """Parse PBC/SSE's dated first-page rows; retain only date precision."""
    parser=_HeadlineRows();parser.feed(html);items=[];dated=0
    for row in parser.rows:
        found=re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)',' '.join(row['text']))
        if not found:continue
        dated+=1
        stamp,_=_dated(found[1],moment,window_start)
        for href,title in row['links']:
            try:link=_https(href,url,{urllib.parse.urlsplit(url).hostname})
            except NewsError:continue
            path=urllib.parse.urlsplit(link).path
            if not title or not re.search(r'(?:/c_\d{8}_\d+\.shtml|/\d+/index\.html|/\d+\.html)$',path):continue
            if stamp:items.append({'title':title[:250],'url':link,'published_at':stamp,'published_precision':'date',
                'source':source,'kind':'market_news','source_url':url,'verification':'官方目录标题及披露日期；未核验正文'})
    if not dated:raise NewsError('official first-page headline/date format was not recognized')
    return list({item['url']:item for item in items}.values())[:32]


def _csrc_headlines(moment,start):
    # The current official list is JS-driven; the homepage's c100028 links are
    # public, but its MM-DD labels do not establish a year. Read the article's
    # declared publication date, at most four articles, without interpreting body.
    parser=_Links();parser.feed(_brief_get(CSRC_PAGE,CSRC_PAGE));links={}
    for href,title in parser.links:
        try:url=_https(href,CSRC_PAGE,{'www.csrc.gov.cn'})
        except NewsError:continue
        if title and re.fullmatch(r'/csrc/c100028/c\d+/content\.shtml',urllib.parse.urlsplit(url).path):links[url]=title
    if not links:raise NewsError('CSRC current homepage headline links were not recognized')
    def article(pair):
        url,title=pair;html=_brief_get(url,CSRC_PAGE)
        text=unescape(re.sub(r'<[^>]+>',' ',html))
        found=re.search(r'日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})',text)
        if not found:raise NewsError('CSRC article declared publication date missing')
        stamp,_=_dated(found[1],moment,start)
        return {'title':title[:250],'url':url,'published_at':stamp,'published_precision':'date','source':'中国证监会·要闻',
            'kind':'market_news','source_url':CSRC_PAGE,'verification':'官方文章标题与明示发布日期；未分析正文'} if stamp else None
    with ThreadPoolExecutor(max_workers=4) as pool:items=list(pool.map(article,list(links.items())[:4]))
    return [item for item in items if item]


def _brief_sources(moment,start,*,fixed_observed=None,allow_late=False):
    actual=fixed_observed or _moment()
    if not allow_late and not _window(actual):
        return [],[{'source':name,'url':url,'status':'error','collected_at':actual.isoformat(),'count':0,
            'error':'盘前窗口已结束，未追加来源请求','scope':'未请求，未声称已读'} for name,url in BRIEF_SOURCES]
    def source(spec):
        name,url=spec
        try:
            items=_csrc_headlines(moment,start) if url==CSRC_PAGE else parse_official_headlines(_brief_get(url,url),name,url,moment,start)
            observed=fixed_observed or _moment()
            return [dict(item,observed_at=observed.isoformat()) for item in items],{'source':name,'url':url,'status':'complete',
                'collected_at':observed.isoformat(),'count':len(items),'error':'',
                'scope':start+'至'+moment.date().isoformat()+'公开首屏有界标题取样；央行/上交所最多32条，证监会最多4条文章日期元数据；未声称全站或全文覆盖'}
        except NewsError as exc:
            observed=fixed_observed or _moment()
            return [],{'source':name,'url':url,'status':'error','collected_at':observed.isoformat(),'count':0,'error':str(exc),
                'scope':'本次官方首屏取样失败，不代表没有消息'}
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(source,BRIEF_SOURCES))
    return [item for items,_ in results for item in items],[coverage for _,coverage in results]


def collect_brief_only(now=None,*,coverage_start=None):
    """Late-safe context only: never creates a morning risk-verification receipt."""
    moment=_moment(now);start=_coverage_start(moment,coverage_start).isoformat()
    items,coverage=_brief_sources(moment,start,fixed_observed=moment if now is not None else None,allow_late=True)
    try:
        links=parse_cctv_links(_brief_get(CCTV_PAGE,CCTV_PAGE),moment,start)[:4]
        def article(pair):
            url,title=pair
            return parse_cctv_article(_brief_get(url,CCTV_PAGE),url,title,moment,start)
        with ThreadPoolExecutor(max_workers=4) as pool:accepted=[item for item in pool.map(article,links) if item]
        observed=moment if now is not None else _moment()
        items.extend(dict(item,observed_at=observed.isoformat()) for item in accepted)
        coverage.append({'source':'央视网财经','url':CCTV_PAGE,'status':'complete' if links else 'partial',
            'collected_at':observed.isoformat(),'count':len(accepted),'error':'' if links else '首页没有可解析的近期文章',
            'scope':'财经首页最多4条文章的标题与发布时间元数据，不是全部新闻或正文核验'})
    except NewsError as exc:
        observed=moment if now is not None else _moment()
        coverage.append({'source':'央视网财经','url':CCTV_PAGE,'status':'error','collected_at':observed.isoformat(),
            'count':0,'error':str(exc),'scope':'本次有界采样失败，未声称已读'})
    finished=moment if now is not None else _moment()
    value=market_brief(items,coverage,finished)
    if not _window(finished):value['warnings'].append('本次为实际晚到消息更新，不是08点采集或09点准时发布证明，不授权新买入。')
    return value


def market_brief(items,coverage,moment):
    """Fixed title-topic importance, never sentiment, returns or beneficiary stocks."""
    rules=[('货币政策',r'货币政策|降准|降息|利率|流动性|逆回购|金融统计','macro'),
           ('监管规则',r'监管|指导意见|实施规则|征求意见|行政处罚|资本市场','policy'),
           ('宏观经济',r'经济|GDP|CPI|PPI|消费|出口|财政|就业','macro'),
           ('市场机制',r'ETF|融资|交易|证券|上市|中长期资金','market'),
           ('风险提示',r'风险提示|风险警示|停牌|立案|退市|逾期','risk'),
           ('产业动态',r'科技|半导体|人工智能|能源|制造','general')]
    unique={};cutoff=moment-dt.timedelta(hours=20)
    for item in items:
        title=re.sub(r'\s+',' ',str(item.get('title',''))).strip()
        if not title or not item.get('published_at') or not item.get('observed_at'):continue
        topics=[(name,impact) for name,pattern,impact in rules if re.search(pattern,title)]
        precision='day' if item.get('published_precision') in ('date','day') else item.get('published_precision','second')
        published=item['published_at'];stamp=dt.datetime.combine(dt.date.fromisoformat(published),dt.time(),CHINA) if precision=='day' else dt.datetime.fromisoformat(published)
        recent=stamp>=cutoff or precision=='day' and published>=cutoff.date().isoformat()
        key=re.sub(r'[\s，。！？：:、《》“”]','',title)
        row={'id':hashlib.sha256(item['url'].encode()).hexdigest()[:20],'title':title[:250],'url':item['url'],
            'source':item['source'],'published_at':published,'published_precision':precision,'collected_at':item['observed_at'],
            'importance':'high' if topics else 'normal','topics':[name for name,_ in topics[:4]],
            'importance_reason':('标题涉及'+'、'.join(name for name,_ in topics[:3])+'，值得核对原文。') if topics else '官方近期财经标题，供背景阅读。',
            'impact':topics[0][1] if topics else 'general','scope':item.get('verification','官方目录标题，未核验正文'),
            '_recent':recent,'_issuer':item.get('kind')=='announcement'}
        if key not in unique or (row['_recent'],published)>(unique[key]['_recent'],unique[key]['published_at']):unique[key]=row
    ordered=sorted(unique.values(),key=lambda row:(row['_recent'],row['importance']=='high',row['published_at']),reverse=True)
    selected=[];issuers=0
    for row in ordered:
        if row['_issuer'] and issuers>=2:continue
        issuers+=int(row['_issuer']);selected.append({k:v for k,v in row.items() if not k.startswith('_')})
        if len(selected)==8:break
    failures=[source for source in coverage if source['status']!='complete']
    warnings=['仅按官方标题和固定主题规则选取，重要性不是利好/利空评分，不推断受益股、涨跌或未来收益。',
              '优先隔夜及当日已发布消息；不足时保留窗口内较早背景，日期精度不会伪造为具体时分。']
    if failures:warnings.append('部分官方来源获取或解析失败，请展开来源覆盖；未声称全部消息已读。')
    if len(selected)<5:warnings.append('本次已核验标题少于5条，按真实数量展示，不补造新闻。')
    return {'protocol':'market-brief-v1','date':moment.date().isoformat(),'generated_at':moment.isoformat(),
        'status':'partial' if failures and selected else 'complete' if selected else 'unavailable','items':selected,
        'coverage':coverage[:8],'warnings':warnings,'late':not _window(moment),
        'scope':'有限官方来源标题与发布时间汇总；未阅读全文，不代表全部消息或收益预测。'}


def collect(now=None, *, coverage_start=None):
    """Return a daily_plan-compatible morning_news document, without writing KV."""
    moment = _moment(now)
    start = _coverage_start(moment, coverage_start).isoformat()
    record = {'date': moment.date().isoformat(), 'as_of': moment.isoformat(),
              'collection_started_at': moment.isoformat(),
              'window_start': start, 'window_end': moment.date().isoformat(),
              'verification_protocol': VERIFICATION_PROTOCOL,
              'timezone': 'Asia/Shanghai', 'status': 'unavailable', 'items': [],
              'risk_exclusions': [], 'errors': [], 'sources': [],
              'verification_scope': '来源与发布时间解析核验，不代表已独立核实全部报道事实或已阅读全部公告正文。',
              'lookback_days': (moment.date() - dt.date.fromisoformat(start)).days}
    if not _window(moment):
        record['errors'].append('采集时刻不在北京时间08:00至09:30盘前窗口内。')
        return record
    all_items = []
    for name, url, fetch in [('上海证券交易所·上市公司公告', SSE_PAGE, _sse), ('央视网财经', CCTV_PAGE, _cctv)]:
        try:
            items, source = fetch(moment, start)
            observed = _moment() if now is None else moment
            if not isinstance(source, dict) or source.get('status') != 'complete':
                raise NewsError('source collection did not produce a complete receipt')
            source = dict(source, source=name, url=url, window_start=start,
                          window_end=moment.date().isoformat(), observed_at=observed.isoformat())
            all_items.extend(dict(item, source_url=url, observed_at=observed.isoformat()) for item in items)
            record['sources'].append(source)
        except NewsError as exc:
            record['errors'].append(name + '：' + str(exc))
            record['sources'].append({'source': name, 'url': url, 'status': 'failed', 'error': str(exc)})
    try:
        monitoring = _risk_window(moment, fixed_observed=moment if now is not None else None)
        if monitoring.get('status') != 'complete':
            raise NewsError('31-day risk observation did not complete')
        record['risk_monitoring'] = monitoring
    except NewsError as exc:
        record['risk_monitoring'] = {'protocol': RISK_PROTOCOL, 'status': 'failed', 'source_url': SSE_PAGE,
            'window_start': (moment.date() - dt.timedelta(days=RISK_LOOKBACK_DAYS)).isoformat(),
            'window_end': moment.date().isoformat(), 'lookback_days': RISK_LOOKBACK_DAYS,
            'semantics': RISK_SEMANTICS, 'error': str(exc)}
        record['errors'].append('上交所31天风险研究观察窗口：' + str(exc))
    # Independent broad context does not relax or alter the strict SSE risk
    # coverage above. Each added source has one six-second request per page,
    # at most four CSRC date pages, and only three concurrent publishers.
    broad_items,broad_coverage=_brief_sources(moment,start,fixed_observed=moment if now is not None else None)
    # In production use completion time; passing now is an explicit deterministic
    # replay/testing boundary, never a fabricated successful earlier timestamp.
    finished = _moment() if now is None else moment
    record['as_of'] = finished.isoformat()
    if finished.date() != moment.date() or not _window(finished):
        record['errors'].append('采集完成已超出当日盘前窗口，不能作为今日新买入核验。')
    unique = {item['url']: item for item in all_items}
    all_items = sorted(unique.values(), key=lambda item: item['published_at'], reverse=True)
    recent_risks = risk_exclusions(all_items)
    monitoring_risks = record['risk_monitoring'].get('risk_exclusions', [])
    record['risk_exclusions'] = list({(risk['symbol'], risk['url']): risk
                                    for risk in recent_risks + monitoring_risks}.values())
    # Display both source types even on heavy announcement days.  Risk screening
    # covers the complete successfully scanned list before this display cap.
    market = [item for item in all_items if item['kind'] == 'market_news']
    announcements = [item for item in all_items if item['kind'] == 'announcement']
    record['items'] = sorted(market + announcements[:max(0, MAX_ITEMS - len(market))], key=lambda item: item['published_at'], reverse=True)
    record['items_scanned'] = len(all_items)
    record['items_displayed'] = len(record['items'])
    coverage=[{'source':source['source'],'url':source['url'],'status':'complete' if source['status']=='complete' else 'error',
        'collected_at':source.get('observed_at',finished.isoformat()),'count':source.get('accepted_items',0),
        'error':source.get('error',''),'scope':source.get('scope','固定官方公告来源目录核验，未阅读全部正文')}
        for source in record['sources']]
    record['market_brief']=market_brief(all_items+broad_items,coverage+broad_coverage,finished)
    if not record['errors'] and record['items']:
        record['status'] = 'verified'
    return record
