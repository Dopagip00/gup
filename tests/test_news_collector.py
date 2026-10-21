import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('news_collector', Path(__file__).resolve().parents[1] / 'news_collector.py')
news = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news)
NOW = dt.datetime(2026, 10, 19, 8, 5, tzinfo=news.CHINA)
ARTICLE = 'https://finance.cctv.com/2026/10/19/ARTIfixture261019.shtml'


def bulletin(code='600001', title='关于公司股票停牌的公告', day='2026-10-19', url=None):
    return {'SECURITY_CODE': code, 'SECURITY_NAME': '测试公司', 'TITLE': title,
            'SSEDATE': day, 'URL': url or '/disclosure/listedinfo/announcement/c/new/2026-10-19/600001_test.pdf'}


def page(rows, number=1, total=None):
    return {'result': rows, 'pageHelp': {'pageNo': number, 'total': len(rows) if total is None else total}}


def risk_receipt(moment=NOW, events=None):
    start=(moment.date()-dt.timedelta(days=31)).isoformat()
    return {'protocol':news.RISK_PROTOCOL,'status':'complete','source_url':news.SSE_PAGE,
        'window_start':start,'window_end':moment.date().isoformat(),'lookback_days':31,
        'observed_at':moment.isoformat(),'queries':[{'keyword':word,'title_query':word,'status':'complete',
            'url':news.SSE_PAGE,'source_url':news.SSE_PAGE,'window_start':start,'window_end':moment.date().isoformat(),
            'groups_total':0,'groups_scanned':0,'pages':1,'observed_at':moment.isoformat()} for word in news.RISK_QUERIES],
        'items_scanned':len(events or []),'risk_exclusions':events or [],'semantics':news.RISK_SEMANTICS}


class NewsTests(unittest.TestCase):
    def setUp(self):
        broad=patch.object(news,'_brief_sources',return_value=([],[]));broad.start();self.addCleanup(broad.stop)
        observation=patch.object(news,'_risk_window',side_effect=lambda moment,**kwargs:risk_receipt(moment))
        observation.start();self.addCleanup(observation.stop)

    def test_sse_grouped_date_precision_and_future_filter(self):
        data = page([[bulletin(), bulletin(title='法律意见书')], [bulletin(day='2026-10-20')],
                     [bulletin(code='900001')], [bulletin(day='2026-09-30')]])
        items, invalid = news.parse_announcements(data, NOW)
        self.assertEqual(invalid, 0)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['symbol'], 'sh600001')
        self.assertEqual(items[0]['published_at'], '2026-10-19')
        self.assertEqual(items[0]['published_precision'], 'date')
        self.assertTrue(items[0]['url'].startswith('https://www.sse.com.cn/'))

    def test_untrusted_urls_and_malformed_announcement_count_as_invalid(self):
        items, invalid = news.parse_announcements(page([[bulletin(url='https://www.sse.com.cn.evil.test/x'),
                                                        bulletin(day='unknown'), bulletin(url='javascript:alert(1)'),
                                                        bulletin(url='https://user:password@www.sse.com.cn/a')]]), NOW)
        self.assertEqual(items, [])
        self.assertEqual(invalid, 4)

    def test_sse_cannot_claim_another_approved_publisher_document(self):
        items, invalid = news.parse_announcements(page([[bulletin(url=ARTICLE), bulletin(url=news.SSE_API)]]), NOW)
        self.assertEqual(items, [])
        self.assertEqual(invalid, 2)

    def test_cross_publisher_redirect_is_rejected_before_reading_body(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = ARTICLE
        with patch.object(news.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(news.NewsError):
                news._get(news.SSE_API, news.SSE_PAGE)
        response.read.assert_not_called()

    def test_all_sse_pages_scanned_not_just_first_page(self):
        responses = [json.dumps(page([[bulletin(title='第一页')], [bulletin(title='第二条')]], total=3)),
                     json.dumps(page([[bulletin(code='688001', title='第三条')]], number=2, total=3))]
        with patch.object(news, 'PAGE_SIZE', 2), patch.object(news, '_get', side_effect=responses) as get, patch.object(news.time, 'sleep'):
            items, source = news._sse(NOW)
        self.assertEqual(len(items), 3)
        self.assertEqual(source['groups_scanned'], 3)
        self.assertEqual(source['pages'], 2)
        self.assertIn('START_DATE=2026-10-15', get.call_args_list[0].args[0])
        self.assertIn('pageHelp.beginPage=2', get.call_args_list[1].args[0])
        self.assertIn('pageHelp.endPage=2', get.call_args_list[1].args[0])

    def test_changed_count_or_incomplete_page_fails_closed(self):
        responses = [json.dumps(page([[bulletin()]], total=2)), json.dumps(page([], number=2, total=3))]
        with patch.object(news, 'PAGE_SIZE', 1), patch.object(news, '_get', side_effect=responses), patch.object(news.time, 'sleep'):
            with self.assertRaisesRegex(news.NewsError, 'count changed'):
                news._sse(NOW)

    def test_boolean_or_fractional_pagination_cannot_claim_complete_coverage(self):
        for key,value in (('total',True),('total',1.5),('pageNo',True),('pageNo',1.5)):
            data=page([[bulletin()]])
            data['pageHelp'][key]=value
            with self.subTest(key=key,value=value),patch.object(news,'_get',return_value=json.dumps(data)):
                with self.assertRaisesRegex(news.NewsError,'pagination metadata'):
                    news._sse(NOW)
        with patch.object(news, '_get', return_value=json.dumps(page([], total=1))):
            with self.assertRaisesRegex(news.NewsError, 'incomplete'):
                news._sse(NOW)

    def test_repeated_page_cannot_claim_full_news_scan(self):
        repeated = [[bulletin()]]
        responses = [json.dumps(page(repeated, total=2)), json.dumps(page(repeated, number=2, total=2))]
        with patch.object(news, 'PAGE_SIZE', 1), patch.object(news, '_get', side_effect=responses), patch.object(news.time, 'sleep'):
            with self.assertRaisesRegex(news.NewsError, 'repeated a page'):
                news._sse(NOW)

    def test_overlapping_groups_cannot_claim_complete_pagination(self):
        first, shared, last = [bulletin(title='第一条')], [bulletin(title='重复条')], [bulletin(title='最后条')]
        responses = [json.dumps(page([first, shared], total=4)),
                     json.dumps(page([shared, last], number=2, total=4))]
        with patch.object(news, 'PAGE_SIZE', 2), patch.object(news, '_get', side_effect=responses), patch.object(news.time, 'sleep'):
            with self.assertRaisesRegex(news.NewsError, 'repeated an announcement group'):
                news._sse(NOW)

    def test_long_holiday_window_includes_previous_session_and_early_break_news(self):
        reopened = NOW.replace(day=9)
        rows = page([[bulletin(day='2026-10-01')]])
        self.assertEqual(news.parse_announcements(rows, reopened)[0], [])
        items, invalid = news.parse_announcements(rows, reopened, '2026-09-30')
        self.assertEqual((len(items), invalid), (1, 0))
        self.assertEqual(items[0]['published_at'], '2026-10-01')
        article = 'https://finance.cctv.com/2026/10/01/ARTIholiday261001.shtml'
        html = '<a href="' + article + '">长假新闻</a>'
        self.assertEqual(news.parse_cctv_links(html, reopened), [])
        self.assertEqual(news.parse_cctv_links(html, reopened, '2026-09-30'), [(article, '长假新闻')])
        item = news.parse_cctv_article('var publishDate="20261001070000"', article, '长假新闻', reopened, '2026-09-30')
        self.assertEqual(item['published_at'], '2026-10-01T07:00:00+08:00')

    def test_unknown_or_unbounded_calendar_window_fails_closed(self):
        for invalid in ('2026-10-20', '2026-09-01', 'unknown', NOW):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                news.collect(NOW, coverage_start=invalid)

    def test_cctv_link_and_precise_publisher_timestamp(self):
        html = '<a href="' + ARTICLE + '">宏观<b>数据</b></a><a href="https://other.test/2026/10/19/ARTIfixture.shtml">外部</a>'
        html += '<a href="https://finance.cctv.com/2026/10/20/ARTIfuture.shtml">未来</a>'
        self.assertEqual(news.parse_cctv_links(html, NOW), [(ARTICLE, '宏观数据')])
        item = news.parse_cctv_article('var publishDate = "20261019075900";', ARTICLE, '宏观数据', NOW)
        self.assertEqual(item['published_at'], '2026-10-19T07:59:00+08:00')
        self.assertIsNone(news.parse_cctv_article('var publishDate="20261019090000";', ARTICLE, '未来', NOW))
        with self.assertRaises(news.NewsError):
            news.parse_cctv_article('<p>2026-10-19</p>', ARTICLE, '缺元数据', NOW)

    def test_risk_tags_need_structured_official_code_and_explicit_title(self):
        titles = ['关于公司股票停牌的公告', '关于公司股票复牌的公告', '关于撤销退市风险警示的公告',
                  '关于公司被中国证监会立案调查的公告', '关于对外投资的公告', '关于实施退市风险警示的公告']
        items, _ = news.parse_announcements(page([[bulletin(title=title)] for title in titles]), NOW)
        items += [{'kind': 'market_news', 'symbol': 'sh600001', 'title': '某公司被调查', 'url': ARTICLE}]
        risks = news.risk_exclusions(items)
        self.assertEqual(len(risks), 3)
        self.assertTrue(all(risk['symbol'] == 'sh600001' for risk in risks))
        self.assertTrue(all('公告目录标题' in risk['evidence_scope'] for risk in risks))

    def test_delayed_resumption_and_pending_risk_warning_removal_remain_vetoes(self):
        titles = ['关于筹划重大资产重组停牌及延期复牌的公告', '关于暂缓复牌的公告',
                  '关于申请撤销其他风险警示的公告', '关于中国证监会立案调查进展的公告',
                  '关于股票复牌的公告', '关于无需停牌的公告', '关于撤销其他风险警示的公告']
        items, _ = news.parse_announcements(page([[bulletin(title=title)] for title in titles]), NOW)
        risks = news.risk_exclusions(items)
        self.assertEqual([item['title'] for item in risks], titles[:4])

    def test_removing_one_warning_does_not_mask_another_continuing_warning(self):
        titles = ['关于撤销退市风险警示并实施其他风险警示的公告',
                  '关于撤销其他风险警示的公告']
        items, _ = news.parse_announcements(page([[bulletin(title=title)] for title in titles]), NOW)
        risks = news.risk_exclusions(items)
        self.assertEqual([item['title'] for item in risks], titles[:1])

    def test_collect_requires_both_sources_and_preserves_failure(self):
        items, _ = news.parse_announcements(page([[bulletin()]]), NOW)
        source = {'source': 'SSE', 'status': 'complete'}
        with patch.object(news, '_sse', return_value=(items, source)), patch.object(news, '_cctv', side_effect=news.NewsError('request failed')):
            record = news.collect(NOW)
        self.assertEqual(record['status'], 'unavailable')
        self.assertEqual(record['date'], '2026-10-19')
        self.assertEqual(len(record['items']), 1)
        self.assertEqual(len(record['risk_exclusions']), 1)
        self.assertIn('request failed', record['errors'][0])
        self.assertEqual(record['sources'][1]['status'], 'failed')

    def test_collect_verified_is_source_check_and_timestamp_not_fake_analysis(self):
        items, _ = news.parse_announcements(page([[bulletin()]]), NOW)
        market = news.parse_cctv_article('var publishDate="20261019070000"', ARTICLE, '财经新闻', NOW)
        with patch.object(news, '_sse', return_value=(items, {'status': 'complete'})), patch.object(news, '_cctv', return_value=([market], {'status': 'complete'})):
            record = news.collect(NOW)
        self.assertEqual(record['status'], 'verified')
        self.assertEqual(record['as_of'], NOW.isoformat())
        self.assertEqual(record['items_scanned'], 2)
        self.assertIn('不代表', record['verification_scope'])
        self.assertNotIn('sentiment_score', record)
        self.assertEqual(record['window_start'], '2026-10-15')
        self.assertEqual(record['verification_protocol'],news.VERIFICATION_PROTOCOL)
        self.assertEqual(record['risk_monitoring']['lookback_days'],31)
        self.assertEqual(record['collection_started_at'], NOW.isoformat())
        self.assertEqual(record['sources'][0]['url'], news.SSE_PAGE)
        self.assertEqual(record['sources'][1]['url'], news.CCTV_PAGE)
        for item in record['items']:
            self.assertEqual(item['observed_at'], NOW.isoformat())
            self.assertIn(item['source_url'], (news.SSE_PAGE, news.CCTV_PAGE))

    def test_collect_passes_expanded_window_and_keeps_real_source_receipts(self):
        reopened = NOW.replace(day=9)
        items, _ = news.parse_announcements(page([[bulletin(day='2026-10-01')]]), reopened, '2026-09-30')
        market = news.parse_cctv_article('var publishDate="20261009070000"', ARTICLE, '新闻', reopened)
        with patch.object(news, '_sse', return_value=(items, {'status': 'complete'})) as sse, patch.object(news, '_cctv', return_value=([market], {'status': 'complete'})) as cctv:
            record = news.collect(reopened, coverage_start='2026-09-30')
        self.assertEqual(record['status'], 'verified')
        self.assertEqual(record['lookback_days'], 9)
        self.assertEqual(record['window_start'], '2026-09-30')
        sse.assert_called_once_with(reopened, '2026-09-30')
        cctv.assert_called_once_with(reopened, '2026-09-30')
        self.assertTrue(all(source['window_start'] == '2026-09-30' for source in record['sources']))

    def test_incomplete_receipt_or_late_actual_completion_cannot_verify(self):
        items, _ = news.parse_announcements(page([[bulletin()]]), NOW)
        with patch.object(news, '_sse', return_value=(items, {'status': 'partial'})), patch.object(news, '_cctv', return_value=([], {'status': 'complete'})):
            self.assertEqual(news.collect(NOW)['status'], 'unavailable')
        late = NOW.replace(hour=9, minute=31)
        with patch.object(news, '_moment', side_effect=[NOW, NOW, late, late]), patch.object(news, '_sse', return_value=(items, {'status': 'complete'})), patch.object(news, '_cctv', return_value=([], {'status': 'complete'})):
            record = news.collect()
        self.assertEqual(record['status'], 'unavailable')
        self.assertEqual(record['as_of'], late.isoformat())
        self.assertTrue(any('超出' in error for error in record['errors']))

    def test_display_cap_does_not_hide_risk_scan(self):
        items, _ = news.parse_announcements(page([[bulletin(title='普通公告')], [bulletin(title='关于停牌的公告', code='600002')]]), NOW)
        market = news.parse_cctv_article('var publishDate="20261019070000"', ARTICLE, '财经新闻', NOW)
        with patch.object(news, 'MAX_ITEMS', 1), patch.object(news, '_sse', return_value=(items, {'status': 'complete'})), patch.object(news, '_cctv', return_value=([market], {'status': 'complete'})):
            record = news.collect(NOW)
        self.assertEqual(record['items_displayed'], 1)
        self.assertEqual(record['risk_exclusions'][0]['symbol'], 'sh600002')

    def test_outside_window_makes_no_request_and_naive_now_rejected(self):
        with patch.object(news, '_sse') as sse:
            record = news.collect(NOW.replace(hour=7, minute=59))
            self.assertEqual(record['status'], 'unavailable')
            self.assertEqual(record['items'], [])
            sse.assert_not_called()
        with self.assertRaises(ValueError):
            news.collect(NOW.replace(tzinfo=None))

    def test_independent_risk_observation_failure_prevents_verified_status(self):
        items,_=news.parse_announcements(page([[bulletin()]]),NOW)
        market=news.parse_cctv_article('var publishDate="20261019070000"',ARTICLE,'财经新闻',NOW)
        with patch.object(news,'_sse',return_value=(items,{'status':'complete'})),patch.object(news,'_cctv',return_value=([market],{'status':'complete'})),patch.object(news,'_risk_window',side_effect=news.NewsError('pagination limit reached')):
            record=news.collect(NOW)
        self.assertEqual(record['status'],'unavailable')
        self.assertEqual(record['risk_monitoring']['status'],'failed')
        self.assertIn('pagination limit reached',record['errors'][0])

    def test_old_risk_is_screened_outside_recent_headlines_and_display_cap(self):
        old,_=news.parse_announcements(page([[bulletin(code='600241',title='股票交易风险提示公告',day='2026-10-10')]]),NOW,'2026-09-18')
        old[0].update(observed_at=NOW.isoformat(),query_keyword='股票交易')
        risk=news.risk_exclusions(old)
        market=news.parse_cctv_article('var publishDate="20261019070000"',ARTICLE,'财经新闻',NOW)
        with patch.object(news,'MAX_ITEMS',1),patch.object(news,'_sse',return_value=([],{'status':'complete'})),patch.object(news,'_cctv',return_value=([market],{'status':'complete'})),patch.object(news,'_risk_window',return_value=risk_receipt(NOW,risk)):
            record=news.collect(NOW)
        self.assertEqual(record['status'],'verified')
        self.assertEqual(record['window_start'],'2026-10-15')
        self.assertEqual(record['risk_monitoring']['window_start'],'2026-09-18')
        self.assertEqual(record['risk_exclusions'][0]['symbol'],'sh600241')
        self.assertEqual(record['items'],[dict(market,observed_at=NOW.isoformat())])


class RiskObservationTests(unittest.TestCase):
    def test_each_fixed_keyword_scans_every_page_and_keeps_old_official_events(self):
        from urllib.parse import parse_qs,urlsplit
        old=[bulletin(code='600241',title='辽宁时代万恒股份有限公司股票交易风险提示公告',day='2026-10-10')]
        second=[bulletin(code='600241',title='辽宁时代万恒股份有限公司股票交易异常波动公告',day='2026-10-13',url='/risk_second.pdf')]
        calls=[]
        def get(url,referer):
            params=parse_qs(urlsplit(url).query);calls.append(params)
            self.assertEqual(params['START_DATE'],['2026-09-18'])
            self.assertEqual(params['END_DATE'],['2026-10-19'])
            word=params['TITLE'][0];number=int(params['pageHelp.pageNo'][0])
            return json.dumps(page([old if number==1 else second],number,2) if word=='股票交易' else page([],number,0))
        with patch.object(news,'PAGE_SIZE',1),patch.object(news,'_get',side_effect=get),patch.object(news.time,'sleep'):
            record=news._risk_window(NOW,fixed_observed=NOW)
        self.assertEqual(len(calls),10)
        self.assertEqual([query['keyword'] for query in record['queries']],list(news.RISK_QUERIES))
        self.assertEqual(record['queries'][0]['pages'],2)
        self.assertEqual({event['published_at'] for event in record['risk_exclusions']},{'2026-10-10','2026-10-13'})
        self.assertTrue(all(event['symbol']=='sh600241' for event in record['risk_exclusions']))
        self.assertTrue(all(event['resolution_status']=='not_determined' for event in record['risk_exclusions']))
        self.assertEqual(record['semantics'],news.RISK_SEMANTICS)
        self.assertIn('不是全部潜在风险',record['scope'])

    def test_ignored_title_filter_is_not_complete_coverage(self):
        with patch.object(news,'_get',return_value=json.dumps(page([[bulletin(title='普通董事会公告')]]))):
            with self.assertRaisesRegex(news.NewsError,'title filter'):
                news._risk_window(NOW,fixed_observed=NOW)

    def test_official_matching_group_may_include_an_unrelated_companion_document(self):
        grouped=page([[bulletin(title='关于股票复牌的公告'),bulletin(title='受托管理事务报告',url='/unrelated.pdf')]])
        with patch.object(news,'_get',return_value=json.dumps(grouped)):
            items,receipt=news._sse(NOW,'2026-09-18',title_query='复牌')
        self.assertEqual([item['title'] for item in items],['关于股票复牌的公告'])
        self.assertEqual(receipt['groups_scanned'],1)
        self.assertEqual(receipt['accepted_items'],1)

    def test_risk_query_page_limit_is_an_error_not_a_truncated_success(self):
        first=page([[bulletin(title='股票交易风险提示公告')]],total=2)
        with patch.object(news,'PAGE_SIZE',1),patch.object(news,'MAX_PAGES',1),patch.object(news,'_get',return_value=json.dumps(first)),patch.object(news.time,'sleep'):
            with self.assertRaisesRegex(news.NewsError,'pagination limit'):
                news._risk_window(NOW,fixed_observed=NOW)

    def test_resumption_title_does_not_automatically_resolve_an_earlier_event(self):
        old,_=news.parse_announcements(page([[bulletin(title='关于股票停牌的公告',day='2026-10-01')]]),NOW,'2026-09-18')
        resumed,_=news.parse_announcements(page([[bulletin(title='关于股票复牌的公告',url='/resumed.pdf')]]),NOW,'2026-09-18')
        def scan(moment,start,*,title_query):
            chosen=[item for item in old+resumed if title_query in item['title']]
            return chosen,{'status':'complete','groups_total':len(chosen),'groups_scanned':len(chosen),'pages':1}
        with patch.object(news,'_sse',side_effect=scan):record=news._risk_window(NOW,fixed_observed=NOW)
        self.assertEqual(len(record['risk_exclusions']),1)
        self.assertEqual(record['risk_exclusions'][0]['published_at'],'2026-10-01')
        self.assertEqual(record['risk_exclusions'][0]['resolution_status'],'not_determined')
        self.assertIn('窗口外不表示安全',record['expiry_note'])


class BroadBriefTests(unittest.TestCase):
    def test_official_first_page_rows_keep_link_date_identity_and_day_precision(self):
        html='<dl><dd><span>2026-10-19</span><a href="/aboutus/mediacenter/hotandd/c/c_20261019_10835135.shtml" title="首批主动ETF获批">短标题</a></dd></dl>'
        items=news.parse_official_headlines(html,'上交所',news.SSE_NEWS_PAGE,NOW)
        self.assertEqual(items[0]['published_at'],'2026-10-19');self.assertEqual(items[0]['published_precision'],'date')
        self.assertEqual(items[0]['title'],'首批主动ETF获批')
        pbc='<tr><td><a href="/goutongjiaoliu/113456/113469/202610190001/index.html">金融统计数据</a></td><td>2026-10-19</td></tr>'
        self.assertEqual(len(news.parse_official_headlines(pbc,'央行',news.PBC_PAGE,NOW)),1)
        with self.assertRaises(news.NewsError):news.parse_official_headlines('<a href="/x">未知日期</a>','央行',news.PBC_PAGE,NOW)

    def test_future_external_links_and_unrelated_page_dates_never_become_headlines(self):
        html='<span>2026-10-19</span><li><a href="https://evil.test/1.html">外部标题</a></li>'
        with self.assertRaises(news.NewsError):news.parse_official_headlines(html,'央行',news.PBC_PAGE,NOW)
        html='<li>2026-10-20<a href="/123/456.html">未来消息</a></li>'
        self.assertEqual(news.parse_official_headlines(html,'央行',news.PBC_PAGE,NOW),[])

    def test_csrc_reads_article_declared_date_instead_of_guessing_homepage_year(self):
        url='https://www.csrc.gov.cn/csrc/c100028/c7665462/content.shtml'
        with patch.object(news,'_brief_get',side_effect=['<a href="'+url+'">先行赔付指导意见</a><span>10-19</span>',
            '<h1>先行赔付指导意见</h1><p>日期：2026-10-19 来源：证监会</p>']):
            items=news._csrc_headlines(NOW,'2026-10-15')
        self.assertEqual(items[0]['published_at'],'2026-10-19');self.assertEqual(items[0]['published_precision'],'date')
        with patch.object(news,'_brief_get',side_effect=['<a href="'+url+'">标题</a>','<p>只有10-19</p>']):
            with self.assertRaises(news.NewsError):news._csrc_headlines(NOW,'2026-10-15')

    def test_fixed_topics_deduplicate_and_limit_issuer_news_without_stock_forecasts(self):
        items=[]
        for index in range(12):
            items.append({'title':'金融统计与货币政策'+str(index),'url':news.PBC_PAGE.replace('index.html',str(index)+'.html'),
                'published_at':'2026-10-19','published_precision':'date','observed_at':NOW.isoformat(),
                'source':'央行','kind':'market_news','verification':'官方目录，未读正文'})
        items.append(dict(items[0]));value=news.market_brief(items,[],NOW)
        self.assertEqual(len(value['items']),8);self.assertEqual(len({row['title'] for row in value['items']}),8)
        self.assertEqual(value['items'][0]['impact'],'macro');self.assertNotIn('beneficiary_symbols',value)
        self.assertTrue(all(row['published_precision']=='day' for row in value['items']))
        insurance=dict(items[0],title='推动普惠保险由风险补偿向风险减量转型')
        self.assertEqual(news.market_brief([insurance],[],NOW)['items'][0]['impact'],'general')

    def test_failed_source_is_visible_and_does_not_claim_no_news(self):
        with patch.object(news,'_brief_get',side_effect=news.NewsError('SSL error')),patch.object(news,'_csrc_headlines',side_effect=news.NewsError('format failed')):
            items,coverage=news._brief_sources(NOW,'2026-10-15',fixed_observed=NOW)
        self.assertEqual(items,[]);self.assertEqual(len(coverage),3)
        self.assertTrue(all(row['status']=='error' and row['collected_at']==NOW.isoformat() for row in coverage))

    def test_late_brief_is_independent_real_time_and_never_claims_verified_morning(self):
        late=NOW.replace(hour=12)
        raw={'title':'央行金融统计','url':'https://www.pbc.gov.cn/1.html','source':'央行','kind':'market_news',
            'published_at':'2026-10-19','published_precision':'date','observed_at':late.isoformat()}
        with patch.object(news,'_brief_sources',return_value=([raw],[])) as sources,patch.object(news,'_brief_get',side_effect=news.NewsError('unavailable')):
            value=news.collect_brief_only(late)
        self.assertTrue(value['late']);self.assertEqual(value['generated_at'],late.isoformat())
        self.assertEqual(value['date'],'2026-10-19');self.assertNotEqual(value['status'],'verified')
        self.assertTrue(sources.call_args.kwargs['allow_late']);self.assertTrue(any('晚到' in message for message in value['warnings']))


if __name__ == '__main__':
    unittest.main()
