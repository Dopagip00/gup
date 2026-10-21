import copy
import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import market_brief as brief

NOW=dt.datetime(2026,10,21,8,20,tzinfo=brief.TZ)


def document():
    return {'protocol':brief.PROTOCOL,'date':'2026-10-21','generated_at':NOW.isoformat(),'status':'partial',
        'items':[{'id':'one','title':'央行发布金融统计数据','url':'https://www.pbc.gov.cn/a.html','source':'中国人民银行',
            'published_at':'2026-10-20','published_precision':'day','collected_at':NOW.isoformat(),
            'importance':'high','importance_reason':'标题涉及货币政策，核对原文。','topics':['货币政策'],'impact':'macro','scope':'仅标题及日期'}],
        'coverage':[{'source':'中国人民银行','url':'https://www.pbc.gov.cn/','status':'complete','count':1,
            'collected_at':NOW.isoformat(),'error':'','scope':'首屏有界取样'}],
        'warnings':['不代表全部消息已读'],'private':'PRIVATE'}


class BriefTests(unittest.TestCase):
    def core(self,values=None):
        return SimpleNamespace(now=lambda:NOW,md=SimpleNamespace(is_trading_day=lambda day:True),
            getkv=lambda key,default=None:(values or {}).get(key,default))

    def test_public_contract_preserves_date_precision_real_collection_and_whitelist(self):
        raw=document();result=brief.public(raw,date='2026-10-21',now=NOW)
        self.assertEqual(result['items'][0]['published_at'],'2026-10-20')
        self.assertEqual(result['items'][0]['published_precision'],'day')
        self.assertEqual(result['items'][0]['collected_at'],NOW.isoformat());self.assertNotIn('private',result)
        self.assertEqual(result['status'],'partial')

    def test_foreign_credentials_query_future_and_relabelled_dates_are_rejected(self):
        for field,value in [('url','https://www.pbc.gov.cn.evil.test/a'),('url','https://user:secret@www.pbc.gov.cn/a'),
            ('url','https://www.pbc.gov.cn/a?token=secret'),('published_at','2026-10-22'),
            ('collected_at','2026-10-21T09:00:00+08:00')]:
            raw=document();raw['items'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):brief.public(raw,date='2026-10-21',now=NOW)
        with self.assertRaises(ValueError):brief.public(document(),date='2026-10-20',now=NOW)

    def test_historical_absence_never_uses_todays_news(self):
        core=self.core({'morning_news':{'date':'2026-10-21','market_brief':document()}})
        result=brief.read('2026-10-20',core=core)
        self.assertEqual(result['status'],'historical_unavailable');self.assertEqual(result['items'],[])
        self.assertEqual(brief.read(core=core)['items'][0]['title'],'央行发布金融统计数据')

    def test_closed_unknown_calendar_and_invalid_saved_record_are_explicit(self):
        core=self.core();core.md.is_trading_day=lambda day:False
        self.assertEqual(brief.read(core=core)['status'],'closed')
        core.md.is_trading_day=Mock(side_effect=RuntimeError('calendar unavailable'))
        self.assertEqual(brief.read(core=core)['status'],'unavailable')
        raw=document();raw['generated_at']='2026-10-21T09:00:00+08:00'
        self.assertEqual(brief.read(core=self.core({'market_brief:2026-10-21':raw}))['items'],[])
        raw=document();raw['status']='complete';raw['items']=[]
        with self.assertRaises(ValueError):brief.public(raw,date='2026-10-21',now=NOW)

    def test_local_api_routes_selected_day_without_fetching_external_sources(self):
        import app
        handler=app.make_handler(SimpleNamespace(),8765).__new__(app.make_handler(SimpleNamespace(),8765))
        handler.path='/api/market-brief?date=2026-10-20';handler.host_valid=lambda:True;handler.send=Mock()
        with patch.object(brief,'read',return_value={'date':'2026-10-20','status':'historical_unavailable'}) as read:
            handler.do_GET()
        read.assert_called_once_with('2026-10-20');self.assertEqual(handler.send.call_args.args[0]['status'],'historical_unavailable')


if __name__=='__main__':unittest.main()
