import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import core
import app


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = patch.object(core, 'DATA', Path(self.temp.name))
        self.override.start()
        self.service = core.Service()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def candidate(self, *, prediction_eligible=True):
        return {'symbol': 'sh600298', 'name': '安琪酵母', 'date': '2026-10-15',
                'eligible': True, 'excluded': False, 'stale': False, 'score': 80,
                'entry_low': 9.99, 'entry_high': 10.02, 'max_position_pct': 10,
                'raw_close': 10, 'prediction': {'available': True,
                    'eligible': prediction_eligible, 'as_of_date': '2026-10-15',
                    'expected_return_pct': 3, 'rank_value': 1.2,
                    'training_samples': 120, 'validation_samples': 60}}

    def quote(self, timestamp):
        return {'symbol': 'sh600298', 'name': '安琪酵母', 'datetime': timestamp.isoformat(),
                'date': timestamp.date().isoformat(), 'price': 10, 'previous_close': 10,
                'volume': 1000, 'actionable': True, 'suspended': False,
                'is_st': False, 'is_delisting': False, 'change_pct': 0}

    def seed_candidate(self, signal=None, index_date='2026-10-15'):
        signal = signal or self.candidate()
        with core.connect() as con:
            con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)',
                        ('2026-10-15', 'sh600298', core.encode(signal)))
            con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)',
                        ('2026-10-15', 'sh000001', core.encode({'symbol': 'sh000001', 'date': index_date})))

    def seed_order(self, timestamp):
        self.seed_candidate()
        with core.connect() as con:
            return con.execute('INSERT INTO orders(created_at,symbol,side,quantity,limit_price,status) VALUES(?,?,?,?,?,?)',
                               (timestamp.isoformat(), 'sh600298', 'buy', 100, 10.02, 'pending')).lastrowid

    def test_stale_signal_never_exposes_entry(self):
        signal = {'symbol': 'sh600036', 'date': '2026-10-15', 'eligible': True, 'score': 90, 'entry_low': 20, 'entry_high': 21, 'stop_loss': 19, 'take_profit': 23, 'max_position_pct': 10}
        result = core.gate_signal(signal, '2026-10-16')
        self.assertFalse(result['eligible'])
        self.assertIsNone(result['entry_low'])
        self.assertEqual(result['max_position_pct'], 0)
        self.assertEqual(signal['entry_low'], 20)

    def test_missing_index_disables_buy(self):
        result = core.gate_signal({'date': '2026-10-16', 'eligible': True}, '2026-10-16', False)
        self.assertFalse(result['eligible'])

    def test_fresh_quote_risk_flags_and_price_disagreement_override_old_security_name(self):
        stock=self.candidate()
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        for changes in ({'is_st':True},{'is_delisting':True},{'name':'*ST测试'},{'name':'测试退'},{'price':10.5}):
            with self.subTest(changes=changes):
                result=core.quote_gate(stock,dict(quote,**changes),stock['date'])
                self.assertFalse(result['eligible']);self.assertFalse(result['quote_valid'])
                self.assertEqual(result['levels_status'],'research_only')
        self.assertTrue(core.quote_gate(stock,dict(quote,price=10.01),stock['date'])['eligible'])

    def test_missing_unadjusted_close_cannot_confirm_quote_consistency(self):
        stock=self.candidate();stock.pop('raw_close',None)
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        result=core.quote_gate(stock,quote,stock['date'])
        self.assertFalse(result['eligible']);self.assertFalse(result['quote_valid'])

    def test_old_prediction_cache_cannot_authorize_new_recommendation(self):
        stock=self.candidate(); original=dict(stock['prediction'])
        result=core.prediction_gate(stock,stock['date'])
        self.assertFalse(result['eligible']);self.assertFalse(result['prediction']['eligible'])
        self.assertIsNone(result['prediction']['rank_value']);self.assertEqual(stock['prediction'],original)
        self.assertEqual(result['levels_status'],'research_only')

    def test_calendar_failure_is_closed(self):
        with patch.object(core.md, 'is_trading_day', side_effect=RuntimeError('missing calendar')):
            self.assertFalse(core.is_open(dt.datetime(2027, 1, 4, 10, 0, tzinfo=core.TZ)))

    def test_config_validation(self):
        for payload in ({'universe': 'random'}, {'watchlist': '../../private'}, {'refresh_minutes': 0}):
            with self.assertRaises(ValueError):
                core.set_config(payload)
        self.assertEqual(core.set_config({'watchlist': 'sh600036 sh600519'})['watchlist'], 'sh600036,sh600519')

    def test_missing_or_wrong_day_market_cap_cannot_pass_preference_filter(self):
        stock=self.candidate()
        quote={'symbol':stock['symbol'],'date':stock['date'],'price':10,'market_cap_100m':300,'suspended':False}
        self.assertTrue(core.quote_gate(stock,quote,stock['date'])['eligible'])
        for bad in (None,dict(quote,date='2026-10-14'),dict(quote,market_cap_100m=None),dict(quote,suspended=True)):
            self.assertFalse(core.quote_gate(stock,bad,stock['date'])['eligible'])
        mega=core.quote_gate(stock,dict(quote,market_cap_100m=5000),stock['date'])
        self.assertTrue(mega['excluded']);self.assertIsNone(mega['entry_low'])

    def test_orders_reserve_cash_and_enforce_t1(self):
        report = {'stock': self.candidate()}
        # Existing holdings keep equity at 100,000 while only 10,000 is cash.
        # Both proposed orders fit the exposure cap; only fee-inclusive cash
        # reservations must reject the second one.
        core.putkv('cash', 10000)
        with core.connect() as con:
            con.execute('INSERT INTO lots(symbol,quantity,price,buy_date) VALUES(?,?,?,?)',
                        ('sh603010', 9000, 10, '2026-10-14'))
        with patch.object(self.service, 'stock', return_value=report):
            first = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 900, 'limit_price': 10})
            self.assertEqual(first['status'], 'pending')
            with self.assertRaisesRegex(ValueError, '可用资金不足'):
                self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
            with self.assertRaises(ValueError):
                self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 10})
            self.service.cancel_order(first['id'])
            released = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
            self.assertEqual(released['status'], 'pending')
        self.assertEqual(self.service.portfolio()['cash'], 10000)

    def test_prediction_filter_blocks_order_without_weakening_the_rule(self):
        for prediction in (None, {'eligible': False}):
            signal = self.candidate()
            if prediction is None:
                signal.pop('prediction')
            else:
                signal['prediction'] = prediction
            with patch.object(self.service, 'stock', return_value={'stock': signal}):
                with self.assertRaisesRegex(ValueError, '未通过'):
                    self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10})
        self.assertEqual(self.service.portfolio()['orders'], [])

    def test_old_quote_cannot_fill_new_order(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp)
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=10))])
        self.assertEqual(len(self.service.portfolio()['trades']), 0)
        # Same order, signal and price: advancing only the quote makes it fill.
        with patch.object(core, 'now', return_value=timestamp+dt.timedelta(seconds=3)), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp+dt.timedelta(seconds=2))])
        self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_valid_quote_fill_and_t1(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_candidate()
        with patch.object(core, 'now', return_value=timestamp-dt.timedelta(seconds=30)), patch.object(self.service, 'stock', return_value={'stock': self.candidate()}):
            order = self.service.place_order({'symbol': 'sh600298', 'side': 'buy', 'quantity': 100, 'limit_price': 10.02})
        quote = self.quote(timestamp-dt.timedelta(seconds=2))
        core.putkv('quotes', {'quotes': [quote]})
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([quote])
            portfolio = self.service.portfolio()
            self.assertEqual(len(portfolio['trades']), 1)
            self.assertEqual(portfolio['trades'][0]['order_id'], order['id'])
            self.assertEqual(portfolio['orders'][0]['status'], 'filled')
            trade = portfolio['trades'][0]
            self.assertEqual(trade['quantity'], 100)
            self.assertGreaterEqual(trade['fee'], 5)
            self.assertEqual(portfolio['positions'][0]['available'], 0)
            self.assertAlmostEqual(portfolio['cash'], 100000 - trade['price'] * 100 - trade['fee'], places=2)
            with patch.object(self.service, 'stock', return_value={'stock': self.candidate()}), self.assertRaisesRegex(ValueError, r'T\+1'):
                self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 9})
        # A real next-session sell closes the lot and charges sell-side fees.
        next_session = dt.datetime(2026, 10, 19, 10, 0, tzinfo=core.TZ)
        with patch.object(core, 'now', return_value=next_session-dt.timedelta(seconds=30)), patch.object(self.service, 'stock', return_value={'stock': self.candidate()}):
            sale = self.service.place_order({'symbol': 'sh600298', 'side': 'sell', 'quantity': 100, 'limit_price': 9.98})
        with patch.object(core, 'now', return_value=next_session), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(next_session-dt.timedelta(seconds=2))])
        closed = self.service.portfolio()
        self.assertEqual(len(closed['trades']), 2)
        self.assertEqual(closed['positions'], [])
        sell_trade = next(t for t in closed['trades'] if t['order_id'] == sale['id'])
        self.assertGreater(sell_trade['fee'], trade['fee'])
        self.assertAlmostEqual(closed['cash'], portfolio['cash'] + sell_trade['price'] * 100 - sell_trade['fee'], places=2)

    def test_pending_buy_rechecks_prediction_before_filling(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp-dt.timedelta(seconds=30))
        self.seed_candidate(self.candidate(prediction_eligible=False))
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=2))])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.seed_candidate()
            self.service.match_orders([self.quote(timestamp-dt.timedelta(seconds=1))])
            self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_pending_buy_rechecks_index_date_and_corporate_action_reference(self):
        timestamp = dt.datetime(2026, 10, 16, 10, 30, tzinfo=core.TZ)
        self.seed_order(timestamp-dt.timedelta(seconds=30))
        self.seed_candidate(index_date='2026-10-14')
        quote = self.quote(timestamp-dt.timedelta(seconds=2))
        with patch.object(core, 'now', return_value=timestamp), patch.object(core, 'is_open', return_value=True):
            self.service.match_orders([quote])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.seed_candidate()
            self.service.match_orders([dict(quote, previous_close=9.5)])
            self.assertEqual(self.service.portfolio()['trades'], [])
            self.assertIn('除权', self.service.portfolio()['orders'][0]['note'])
            self.service.match_orders([quote])
            self.assertEqual(len(self.service.portfolio()['trades']), 1)

    def test_http_csrf_and_host_guard(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.make_handler(self.service, 0))
        port = server.server_address[1]
        server.RequestHandlerClass = app.make_handler(self.service, port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{port}'
        try:
            with urlopen(url+'/api/health', timeout=5) as response:
                self.assertTrue(json.load(response)['ok'])
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url+'/api/config', data=b'{}', method='POST'), timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            request = Request(url+'/api/config', data=b'{"refresh_minutes":6}', headers={'X-CSRF-Token': self.service.csrf, 'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response)['refresh_minutes'], 6)
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url+'/api/health', headers={'Host': 'evil.example'}), timeout=5)
            self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
