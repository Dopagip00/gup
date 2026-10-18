"""Local A-share research service. No brokerage credentials or live order routing."""
from __future__ import annotations

import concurrent.futures
import contextlib
import csv
import datetime as dt
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time

import market_data as md
import strategy
import forecast

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ASHARE_DATA_DIR', str(BASE / 'data'))).resolve()
TZ = dt.timezone(dt.timedelta(hours=8))
DEFAULTS = {'universe': 'shanghai', 'watchlist': 'sh600519,sh600036,sh601318',
            'refresh_minutes': 5, 'history_count': 640,
            'workers': 4, 'initial_cash': 100000, 'max_position_pct': 10}
SYMBOL = re.compile(r'^(sh(?:60[0135]\d{3}|68[89]\d{3})|sz(?:00\d{4}|30\d{4}))$')
HEAVYWEIGHTS = {'sh600519', 'sh600900', 'sh601318', 'sh601628', 'sh601601', 'sh601319',
               'sh601336', 'sh600030', 'sh600837', 'sh601688', 'sh601211', 'sh601066',
               'sh600028', 'sh601857', 'sh600938', 'sh600941', 'sh601728', 'sh600050',
               'sh601088', 'sh601899', 'sh600309', 'sh601668', 'sh601390', 'sh601186'}


def exclusion(symbol, name, quote=None):
    if any(word in name for word in ('银行', '证券', '保险', '石油', '石化')) or symbol in HEAVYWEIGHTS:
        return '按偏好排除金融、石油石化及大型指数权重观察池'
    cap = (quote or {}).get('market_cap_100m')
    if isinstance(cap, (float, int)) and cap >= 5000:
        return '按偏好排除总市值5000亿元以上的超大盘股'
    return None


def quote_gate(result, quote, target):
    result = dict(result)
    cap, price = (quote or {}).get('market_cap_100m'), (quote or {}).get('price')
    valid = bool(quote and quote.get('symbol') == result.get('symbol') and quote.get('date') == target and
        type(cap) in (int, float) and math.isfinite(cap) and cap > 0 and type(price) in (int, float) and
        math.isfinite(price) and price > 0 and quote.get('suspended') is not True)
    result['quote_valid'] = valid
    result['quote_snapshot'] = {k: quote.get(k) for k in ('symbol','date','price','market_cap_100m','suspended')} if quote else None
    why = exclusion(result.get('symbol',''), result.get('name',''), quote)
    if not valid:
        result['eligible'] = False
        result['warnings'] = ['报价或总市值未核验，无法确认超大盘排除，暂停推荐。'] + list(result.get('warnings',[]))
    if why:
        result.update(eligible=False, excluded=True, exclusion_reason=why)
        for key in ('entry_low','entry_high','stop_loss','take_profit','research_levels'):
            result[key] = None
    return result


def enrich_signal(result, bars, benchmark=None, quote=None):
    result = dict(result)
    result['prediction'] = forecast.predict(bars, benchmark_bars=benchmark)
    why = exclusion(result['symbol'], result['name'], quote)
    result['excluded'], result['exclusion_reason'] = bool(why), why
    if why:
        result['eligible'] = False
        result['signal'] = '权重股 · 仅供参考'
    if result.get('entry_low') and result.get('entry_high'):
        # Narrow the old support area without moving it above the previous research ceiling.
        center = round((result['entry_low']+result['entry_high'])/2, 2)
        low = max(result['entry_low'], round(center*.9975, 2))
        high = min(result['entry_high'], round(center*1.0025, 2))
        result.update(entry_low=round(low, 2), entry_high=round(max(low, high), 2), entry_width_pct=round((high/low-1)*100, 3))
        result.setdefault('warnings', []).append('观察区间收窄至约0.5%；更窄不代表更准，未触及则不成交。')
    return result


def now():
    return dt.datetime.now(TZ)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


@contextlib.contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'market.sqlite3', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS histories(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS securities(symbol TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS signals(report_date TEXT NOT NULL,symbol TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(report_date,symbol));
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT,started_at TEXT NOT NULL,ended_at TEXT,target_date TEXT,status TEXT,total INTEGER DEFAULT 0,ok INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,error TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,limit_price REAL NOT NULL,status TEXT NOT NULL,note TEXT,filled_at TEXT,fill_price REAL);
        CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,quantity INTEGER NOT NULL,price REAL NOT NULL,buy_date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL,datetime TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,price REAL NOT NULL,fee REAL NOT NULL);
        ''')
        con.execute('INSERT OR IGNORE INTO kv VALUES(?,?)', ('config', encode(DEFAULTS)))
        con.execute('INSERT OR IGNORE INTO kv VALUES(?,?)', ('cash', encode(DEFAULTS['initial_cash'])))


def getkv(key, default=None):
    with connect() as con:
        row = con.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def putkv(key, value):
    with connect() as con:
        con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, encode(value)))


def config():
    result = DEFAULTS | getkv('config', {})
    result.pop('daily_time', None)
    return result


def set_config(payload):
    cfg = config()
    if 'universe' in payload:
        if payload['universe'] not in ('watchlist', 'shanghai', 'all', 'sse50'):
            raise ValueError('不支持的股票范围')
        cfg['universe'] = payload['universe']
    if 'watchlist' in payload:
        items = [x.strip().lower() for x in re.split(r'[,，\s]+', str(payload['watchlist'])) if x.strip()]
        if not items or len(items) > 500 or any(not SYMBOL.fullmatch(x) for x in items):
            raise ValueError('自选代码需为 sh600519 等沪深A股代码，最多500只')
        cfg['watchlist'] = ','.join(dict.fromkeys(items))
    if 'refresh_minutes' in payload:
        minutes = int(payload['refresh_minutes'])
        if not 3 <= minutes <= 60:
            raise ValueError('报价刷新间隔应为3–60分钟')
        cfg['refresh_minutes'] = minutes
    if 'daily_time' in payload:
        raise ValueError('旧本机时钟已取消；08:00/09:00由云端计划管理')
    putkv('config', cfg)
    return cfg


def expected_date():
    value = md.last_completed_session(now())
    return value.isoformat() if isinstance(value, dt.date) else str(value)


def is_open(moment=None):
    moment = moment or now()
    try:
        trading = md.is_trading_day(moment.date())
    except Exception:
        return False
    hm = moment.strftime('%H:%M')
    return trading and ('09:30' <= hm < '11:30' or '13:00' <= hm < '15:00')


def load_history(symbol):
    with connect() as con:
        row = con.execute('SELECT payload FROM histories WHERE symbol=?', (symbol,)).fetchone()
    return json.loads(row[0]) if row else {'bars': []}


def save_history(symbol, history):
    with connect() as con:
        con.execute('INSERT OR REPLACE INTO histories VALUES(?,?,?)', (symbol, encode(history), now().isoformat()))


def gate_signal(result, target, benchmark_ok=True):
    result = dict(result)
    result['warnings'] = list(result.get('warnings', []))
    result['stale'] = result.get('date') != target
    if result['stale']:
        result['warnings'].append('行情未达到最近完整交易日，暂停买入建议')
    if not benchmark_ok:
        result['warnings'].append('上证指数基准过期或缺失，暂停买入建议')
    if result['stale'] or not benchmark_ok:
        result['eligible'] = False
        result['signal'] = '等待有效数据'
        for key in ('entry_low', 'entry_high', 'stop_loss', 'take_profit'):
            result[key] = None
        result['max_position_pct'] = 0
        result['research_levels'] = None
    if result.get('excluded'):
        for key in ('entry_low', 'entry_high', 'stop_loss', 'take_profit'):
            result[key] = None
        result['research_levels'] = None
    result['levels_status'] = 'validated' if result.get('eligible') and result.get('prediction', {}).get('eligible') else 'research_only'
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result


class Service:
    def __init__(self):
        init_db()
        self.lock = threading.Lock()
        self.status = {'status': 'idle'}
        self.csrf = secrets.token_urlsafe(32)
        self.stop = threading.Event()
        self.last_quote_poll = 0
        self.last_attempt = 0

    def start_refresh(self, force=True):
        if not self.lock.acquire(blocking=False):
            return {'status': 'running', **self.status}
        self.status = {'status': 'running', 'phase': '读取交易日历和股票列表', 'completed': 0, 'total': 0}
        threading.Thread(target=self._background_refresh, args=(force,), daemon=True).start()
        return {'status': 'started'}

    def _background_refresh(self, force):
        try:
            self.refresh(force)
        except Exception as exc:
            logging.exception('refresh failed')
            self.status = {'status': 'error', 'error': str(exc)}
        finally:
            self.lock.release()

    def refresh(self, force=False):
        # A listening loopback socket provides a crash-safe lock shared by CLI and server.
        sync_lock = socket.socket()
        try:
            sync_lock.bind(('127.0.0.1', 18767))
        except OSError:
            sync_lock.close()
            self.status = {'status': 'running', 'phase': '另一个更新进程正在同步'}
            return self.status
        run_id = None
        try:
            self.last_attempt = time.time()
            target = expected_date()
            cfg = config()
            previous = getkv('last_success', {})
            if not force and previous.get('target_date') == target and previous.get('universe') == cfg['universe'] and previous.get('watchlist') == cfg['watchlist']:
                self.status = {'status': 'idle', 'phase': '最近完整交易日已经更新'}
                return self.status
            with connect() as con:
                # A previous crashed process leaves an honest, visible incomplete run.
                con.execute("UPDATE runs SET status='interrupted',ended_at=?,error='上次更新未完成，将重新尝试' WHERE status='running'", (now().isoformat(),))
                run_id = con.execute('INSERT INTO runs(started_at,target_date,status) VALUES(?,?,?)', (now().isoformat(), target, 'running')).lastrowid
            self.status = {'status': 'running', 'phase': '下载沪市证券列表', 'completed': 0, 'total': 0}
            if cfg['universe'] == 'watchlist':
                stocks = [{'symbol': s, 'name': s} for s in cfg['watchlist'].split(',')]
            else:
                stocks = md.get_universe(cfg['universe'])
            if not stocks:
                raise RuntimeError('股票列表为空；拒绝生成全市场报告')
            with connect() as con:
                con.execute('UPDATE securities SET active=0')
                con.executemany('INSERT INTO securities VALUES(?,?,1) ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,active=1', [(s['symbol'], s['name']) for s in stocks])
                con.execute('UPDATE runs SET total=? WHERE id=?', (len(stocks), run_id))
            putkv('universe_info', {'mode': cfg['universe'], 'count': len(stocks), 'fetched_at': now().isoformat()})
            index_error = None
            try:
                index_hist = md.fetch_history('sh000001', count=cfg['history_count'])
                save_history('sh000001', index_hist)
            except Exception as exc:
                index_hist = load_history('sh000001')
                index_error = str(exc)
            benchmark = index_hist.get('bars', [])
            benchmark_ok = bool(benchmark and benchmark[-1]['date'] == target and not index_error)
            if benchmark:
                ix = strategy.analyze(benchmark, 'sh000001', '上证指数')
                ix = gate_signal(ix, target, benchmark_ok)
                ix['source'] = index_hist.get('source')
                with connect() as con:
                    con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)', (target, 'sh000001', encode(ix)))
            ok = failed = 0
            self.status.update(phase='同步日线并计算每日评分', total=len(stocks))

            def process(stock):
                symbol, name = stock['symbol'], stock['name']
                # Always replace the entire adjustment window. Never append mismatched qfq bases.
                cached = load_history(symbol) if not force else {'bars': []}
                if cached.get('bars') and cached['bars'][-1]['date'] == target:
                    history = cached
                else:
                    history = md.fetch_history(symbol, count=cfg['history_count'])
                bars = history.get('bars', [])
                if not bars:
                    raise RuntimeError('日线为空')
                save_history(symbol, history)
                if name == symbol:
                    name = history.get('name') or name
                result = strategy.analyze(bars, symbol, name, benchmark_bars=benchmark)
                result = enrich_signal(result, bars, benchmark)
                if name == symbol or not re.search('[\u3400-\u9fffA-Za-z]', name.replace(symbol, '')):
                    result['eligible'] = False
                    result['signal'] = '证券名称待核验'
                    result.setdefault('warnings', []).append('名称缺失，无法核验ST或退市标记')
                    for key in ('entry_low', 'entry_high', 'stop_loss', 'take_profit'):
                        result[key] = None
                    result['max_position_pct'] = 0
                result = gate_signal(result, target, benchmark_ok)
                result.update(source=history.get('source'), adjustment=history.get('adjustment'), fetched_at=history.get('fetched_at'))
                return result

            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
                futures = {pool.submit(process, stock): stock for stock in stocks}
                for future in concurrent.futures.as_completed(futures):
                    stock = futures[future]
                    try:
                        result = future.result()
                        if result['stale']:
                            failed += 1
                        else:
                            ok += 1
                    except Exception as exc:
                        failed += 1
                        result = {'symbol': stock['symbol'], 'name': stock['name'], 'score': None, 'date': None, 'eligible': False, 'stale': True, 'signal': '数据不可用', 'warnings': [str(exc)[:300]], 'reasons': [], 'factors': [], 'max_position_pct': 0}
                    with connect() as con:
                        con.execute('INSERT OR REPLACE INTO signals VALUES(?,?,?)', (target, stock['symbol'], encode(result)))
                        con.execute('UPDATE runs SET ok=?,failed=? WHERE id=?', (ok, failed, run_id))
                    self.status.update(completed=ok+failed, ok=ok, failed=failed, latest=stock['symbol'])
                    if (ok + failed) % 100 == 0:
                        logging.info('Progress %s/%s fresh=%s stale_or_failed=%s', ok+failed, len(stocks), ok, failed)
            status = 'success' if not failed and benchmark_ok else 'partial'
            end = now().isoformat()
            error = ('指数：' + index_error) if index_error else (None if benchmark_ok else '指数数据未达到目标交易日')
            with connect() as con:
                con.execute('UPDATE runs SET ended_at=?,status=?,error=? WHERE id=?', (end, status, error, run_id))
            # A partial refresh with suspended stocks is still retained as a completed report.
            putkv('last_completed', {'target_date': target, 'finished_at': end, 'ok': ok, 'failed': failed, 'total': len(stocks), 'status': status})
            if status == 'success':
                putkv('last_success', {'target_date': target, 'finished_at': end, 'universe': cfg['universe'], 'watchlist': cfg['watchlist']})
            self.status = {'status': status, 'completed': ok+failed, 'total': len(stocks), 'ok': ok, 'failed': failed}
            try:
                from research_refresh import refresh_fundamentals
                refresh_fundamentals()
            except Exception as exc:
                logging.exception('fundamental refresh failed')
                prior = getkv('longterm', {'stocks': [], 'as_of': None, 'warnings': []})
                prior['warnings'] = list(prior.get('warnings', [])) + ['本轮财务更新失败：' + str(exc)[:200]]
                putkv('longterm', prior)
            self.write_report(target)
            return self.status
        except Exception as exc:
            if run_id:
                with connect() as con:
                    con.execute('UPDATE runs SET ended_at=?,status=?,error=? WHERE id=?', (now().isoformat(), 'error', str(exc)[:500], run_id))
            self.status = {'status': 'error', 'error': str(exc)}
            raise
        finally:
            sync_lock.close()

    def dashboard(self, date=None):
        warnings = []
        cloud_status=getkv('cloud_status',{})
        if not cloud_status.get('schedule_verified'):
            warnings.append('云端08:00/09:00计划尚未完成注册验证；本机旧定时任务已停用，请勿将页面中的计划时刻视为运行证明。')
        try:
            target = expected_date()
        except Exception as exc:
            target = None
            warnings.append('交易日历不可用：' + str(exc))
        with connect() as con:
            runs = [dict(r) for r in con.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
            latest_date = con.execute('SELECT MAX(report_date) FROM signals').fetchone()[0]
            report_date = date or latest_date
            rows = con.execute('SELECT s.payload FROM signals s WHERE s.report_date=? AND (s.symbol=\'sh000001\' OR s.symbol IN (SELECT symbol FROM securities WHERE active=1))', (report_date,)).fetchall() if report_date else []
            total = con.execute('SELECT COUNT(*) FROM securities WHERE active=1').fetchone()[0]
        stocks, index = [], None
        quotes = getkv('valuation_quotes',{}).get('quotes',{})
        fresh = 0
        for row in rows:
            item = json.loads(row[0])
            # Historical report is read-only; never expose old entry levels as today's executable advice.
            item = gate_signal(item, target, bool(target))
            if item['symbol'] == 'sh000001':
                index = item
                index['bars'] = [b for b in load_history('sh000001').get('bars', []) if b['date'] <= (report_date or '')][-180:]
            else:
                item = quote_gate(item, quotes.get(item['symbol']), target)
                stocks.append(item)
                fresh += not item.get('stale', True)
        if not index or index.get('stale'):
            stocks = [gate_signal(x, target, False) for x in stocks]
        stocks.sort(key=lambda x: (x.get('prediction', {}).get('rank_value') is not None, x.get('prediction', {}).get('rank_value') or -1e9, x.get('score') or 0), reverse=True)
        stale = not target or not index or index.get('stale', True) or fresh < total or total == 0
        if stale:
            warnings.append('覆盖尚未完整或含过期数据；仅日期有效的个股可进入候选，其他条目暂停建议。')
        if report_date and date and report_date != target:
            warnings.append('正在查看历史报告；交易功能使用最新行情另行校验。')
        if getkv('universe_info', {}).get('mode') != config()['universe']:
            warnings.append('股票范围已变更，等待重新同步证券列表。')
        if runs and runs[0].get('error'):
            warnings.append(runs[0]['error'])
        index_date = index.get('date') if index else None
        progress = self.status
        if runs and runs[0]['status'] == 'running':
            r = runs[0]
            progress = {'status': 'running', 'phase': '下载日线并计算评分', 'completed': r['ok']+r['failed'], 'total': r['total'], 'ok': r['ok'], 'failed': r['failed']}
        meta = {'app_name': '沪研 · A股量化观察台', 'now': now().isoformat(), 'csrf_token': self.csrf,
                'status': progress, 'last_run': runs[0] if runs else None, 'last_success': getkv('last_success'),
                'data_date': index_date, 'report_date': report_date, 'expected_date': target, 'stale': stale,
                'universe': config()['universe'], 'coverage': {'total': total, 'ok': fresh, 'failed': max(0, total-fresh),
                    'excluded': sum(bool(s.get('excluded')) for s in stocks),
                    'candidate_total': sum(not s.get('excluded') for s in stocks),
                    'candidate_ok': sum(not s.get('excluded') and not s.get('stale') for s in stocks),
                    'candidate_failed': sum(not s.get('excluded') and s.get('stale', True) for s in stocks)},
                'mode': 'manual_research', 'hosted': False,
                'schedule': '北京时间交易日08:00云端收集信息；09:00发布今日买入与前一交易日推荐的卖出参考',
                'next_run': self.next_run() if cloud_status.get('schedule_verified') else None, 'warnings': warnings, 'is_trading': is_open(),
                'schedule_status': getkv('schedule_status'), 'progress': progress}
        meta['agent_status'] = {'verified': False, 'execution_verified': False, 'mode': 'manual_research', 'status': 'manual', 'message': '仅生成研究与买卖参考，由本人操作；已停用券商接入。'}
        meta['exclusion_policy'] = '金融、石油石化、指定大型指数权重股及已知总市值5000亿元以上股票仅列参考'
        official = getkv('daily_plan:'+now().date().isoformat(), {})
        meta.update(daily_buy=official.get('buy'),daily_plan_status=official.get('status','pending'),
            daily_plan_message=official.get('message','当日09:00日报尚未发布；休市日不生成交易计划。当前表格为收盘研究。'))
        return {'meta': meta, 'index': index, 'stocks': stocks, 'runs': runs, 'paper': self.portfolio()}

    def next_run(self):
        moment = now()
        for offset in range(370):
            date = moment.date() + dt.timedelta(days=offset)
            try:
                if md.is_trading_day(date):
                    at = dt.datetime.fromisoformat(date.isoformat() + 'T08:00').replace(tzinfo=TZ)
                    if at > moment:
                        return at.isoformat()
            except Exception:
                return '等待新年度交易日历'
        return None

    def stock(self, symbol):
        if symbol != 'sh000001' and not SYMBOL.fullmatch(symbol):
            raise ValueError('无效股票代码')
        history = load_history(symbol)
        if not history['bars']:
            raise ValueError('该股票尚无可用历史行情，请等待同步')
        with connect() as con:
            row = con.execute('SELECT payload FROM signals WHERE symbol=? ORDER BY report_date DESC LIMIT 1', (symbol,)).fetchone()
        result = json.loads(row[0]) if row else strategy.analyze(history['bars'], symbol, symbol)
        try:
            target = expected_date()
        except Exception:
            target = None
        ix = load_history('sh000001').get('bars', [])
        result = gate_signal(result, target, bool(ix and ix[-1]['date'] == target))
        valuation = getkv('valuation_quotes', {}).get('quotes', {}).get(symbol)
        why = exclusion(symbol, result.get('name', ''), valuation)
        result.update(excluded=bool(why), exclusion_reason=why)
        if why:
            result['eligible'] = False
        result = quote_gate(result, valuation, target)
        try:
            backtest = strategy.backtest_cycle(history['bars'], symbol, name=result.get('name', ''), excluded=bool(why))
            backtest_error = None
        except ValueError as exc:
            backtest, backtest_error = None, str(exc)
        return {'stock': result, 'bars': history['bars'], 'backtest': backtest, 'backtest_error': backtest_error, 'source': history.get('source')}

    def poll_quotes(self):
        if not is_open():
            return getkv('quotes', {'quotes': [], 'status': '休市，展示最近报价（如有）'})
        if time.time() - self.last_quote_poll < config()['refresh_minutes'] * 60:
            return getkv('quotes', {'quotes': []})
        self.last_quote_poll = time.time()
        with connect() as con:
            symbols = [r[0] for r in con.execute('SELECT symbol FROM securities WHERE active=1')]
        quotes = md.fetch_quotes(['sh000001'] + symbols)
        if isinstance(quotes, dict):
            quotes = quotes.get('quotes', list(quotes.values()))
        payload = {'quotes': quotes, 'fetched_at': now().isoformat(), 'status': '交易时段延时报价；评分仍基于完整日线'}
        putkv('quotes', payload)
        self.match_orders(quotes)
        return payload

    def portfolio(self):
        with connect() as con:
            cash = json.loads(con.execute('SELECT value FROM kv WHERE key=\'cash\'').fetchone()[0])
            lots = [dict(r) for r in con.execute('SELECT * FROM lots WHERE quantity>0')]
            orders = [dict(r) for r in con.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 100')]
            trades = [dict(r) for r in con.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 100')]
        groups = {}
        today = now().date().isoformat()
        quotes = {q['symbol']: q for q in getkv('quotes', {}).get('quotes', []) if 'symbol' in q}
        for lot in lots:
            symbol = lot['symbol']
            item = groups.setdefault(symbol, {'symbol': symbol, 'quantity': 0, 'available': 0, 'cost': 0})
            item['quantity'] += lot['quantity']
            item['cost'] += lot['quantity'] * lot['price']
            item['available'] += lot['quantity'] if lot['buy_date'] < today else 0
        equity = cash
        for symbol, item in groups.items():
            bars = load_history(symbol).get('bars', [])
            quote = quotes.get(symbol, {})
            quote_price = quote.get('price')
            valid_quote = isinstance(quote_price, (int, float)) and math.isfinite(quote_price) and quote_price > 0 and quote.get('datetime', '')[:10] >= (bars[-1]['date'] if bars else '')
            price = quote_price if valid_quote else (bars[-1].get('raw_close', bars[-1]['close']) if bars else item['cost']/item['quantity'])
            item.update(price=price, market_value=round(price*item['quantity'], 2), avg_cost=round(item['cost']/item['quantity'], 4))
            equity += item['market_value']
        return {'cash': round(cash, 2), 'equity': round(equity, 2), 'positions': list(groups.values()), 'orders': orders, 'trades': trades, 'mode': '模拟交易', 'initial_cash': DEFAULTS['initial_cash']}

    def place_order(self, data):
        symbol, side = str(data.get('symbol', '')), data.get('side')
        if not SYMBOL.fullmatch(symbol) or side not in ('buy', 'sell'):
            raise ValueError('股票代码或交易方向无效')
        if isinstance(data.get('quantity'), bool) or float(data.get('quantity', 0)) != int(float(data.get('quantity', 0))):
            raise ValueError('股数必须为整数')
        quantity, price = int(float(data.get('quantity', 0))), float(data.get('limit_price', 0))
        if quantity <= 0 or not math.isfinite(price) or price <= 0 or price > 1000000 or quantity > 10000000:
            raise ValueError('数量或价格无效')
        if side == 'buy' and ((symbol.startswith('sh68') and quantity < 200) or (not symbol.startswith('sh68') and quantity % 100)):
            raise ValueError('主板买入按100股整数倍；科创板买入不少于200股')
        # Do not accept a buy based on stale recommendations, ST, insufficient history, or weak score.
        report = self.stock(symbol)['stock']
        if side == 'buy' and (not report.get('eligible') or report.get('excluded') or not report.get('prediction', {}).get('eligible')):
            raise ValueError('该股当前未通过策略或数据检查，模拟买单已阻止')
        if side == 'buy' and not (report.get('entry_low', 0) <= price <= report.get('entry_high', 0)):
            raise ValueError('模拟买单限价应在该股当前观察买入区间内')
        with connect() as con:
            con.execute('BEGIN IMMEDIATE')
            cash = json.loads(con.execute('SELECT value FROM kv WHERE key=\'cash\'').fetchone()[0])
            reserved = sum(r['quantity']*r['limit_price'] + max(5, r['quantity']*r['limit_price']*.0003) + r['quantity']*r['limit_price']*.00001 for r in con.execute("SELECT * FROM orders WHERE status='pending' AND side='buy'"))
            if side == 'buy' and price*quantity + max(5, price*quantity*.0003) + price*quantity*.00001 > cash-reserved:
                raise ValueError('模拟可用资金不足（含未成交委托预占）')
            if side == 'buy':
                held = con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=?', (symbol,)).fetchone()[0]
                pending = con.execute("SELECT COALESCE(SUM(quantity),0) FROM orders WHERE symbol=? AND status='pending' AND side='buy'", (symbol,)).fetchone()[0]
                cap = min(config()['max_position_pct'], report.get('max_position_pct') or 0)
                equity = self.portfolio()['equity']
                if (held + pending + quantity)*price > equity*cap/100:
                    raise ValueError('超过策略单股仓位上限')
            else:
                available = con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=? AND buy_date<?', (symbol, now().date().isoformat())).fetchone()[0]
                reserved_sell = con.execute("SELECT COALESCE(SUM(quantity),0) FROM orders WHERE symbol=? AND status='pending' AND side='sell'", (symbol,)).fetchone()[0]
                if quantity > available-reserved_sell:
                    raise ValueError('可卖数量不足，买入当日受T+1限制')
                if symbol.startswith('sh68') and quantity < 200 and quantity != available-reserved_sell:
                    raise ValueError('科创板不足200股的余额应一次卖出')
                if not symbol.startswith('sh68') and quantity % 100 and quantity != available-reserved_sell:
                    raise ValueError('主板零股余额应一次卖出')
            order_id = con.execute('INSERT INTO orders(created_at,symbol,side,quantity,limit_price,status,note) VALUES(?,?,?,?,?,?,?)', (now().isoformat(), symbol, side, quantity, round(price, 2), 'pending', '等待下一次交易时段有效报价；未连接券商')).lastrowid
        return {'id': order_id, 'status': 'pending', 'message': '模拟委托已登记，等待下一次有效报价；当日收盘失效'}

    def cancel_order(self, order_id):
        with connect() as con:
            changed = con.execute("UPDATE orders SET status='cancelled',note='用户撤销' WHERE id=? AND status='pending'", (int(order_id),)).rowcount
        if not changed:
            raise ValueError('委托已成交、失效或不存在')
        return {'status': 'cancelled'}

    def match_orders(self, quotes):
        moment = now()
        if not is_open(moment):
            return
        quotes = {q['symbol']: q for q in quotes if 'symbol' in q}
        with connect() as con:
            con.execute('BEGIN IMMEDIATE')
            cash = json.loads(con.execute('SELECT value FROM kv WHERE key=\'cash\'').fetchone()[0])
            for order in con.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall():
                if order['created_at'][:10] != moment.date().isoformat():
                    con.execute("UPDATE orders SET status='expired',note='当日委托已过期' WHERE id=?", (order['id'],))
                    continue
                q = quotes.get(order['symbol'])
                if not q:
                    continue
                try:
                    quote_at = dt.datetime.fromisoformat(q.get('datetime', q.get('timestamp', ''))).replace(tzinfo=TZ)
                    age = (moment-quote_at).total_seconds()
                    created = dt.datetime.fromisoformat(order['created_at'])
                    if not 0 <= age <= 120 or quote_at <= created:
                        continue
                    price = float(q['price'])
                    change = float(q['change_pct'])
                    if price <= 0 or not math.isfinite(price) or not math.isfinite(change):
                        continue
                except (ValueError, TypeError, KeyError):
                    continue
                # Conservative: no matching near daily price limits, with no level-2 queue data.
                limit = 19.5 if order['symbol'].startswith(('sh68', 'sz30')) else 4.5 if q.get('is_st') else 9.5
                if abs(change) >= limit:
                    continue
                buy = order['side'] == 'buy'
                volume, previous_close = q.get('volume', 0), q.get('previous_close', 0)
                if not isinstance(volume, (int, float)) or not math.isfinite(volume) or not isinstance(previous_close, (int, float)) or not math.isfinite(previous_close):
                    continue
                if q.get('suspended') or volume <= 0:
                    continue
                if buy:
                    if not q.get('actionable', False) or q.get('is_st') or q.get('is_delisting') or exclusion(order['symbol'], q.get('name', ''), q):
                        continue
                    target = expected_date()
                    signal_row = con.execute('SELECT payload FROM signals WHERE symbol=? ORDER BY report_date DESC LIMIT 1', (order['symbol'],)).fetchone()
                    index_row = con.execute('SELECT payload FROM signals WHERE symbol=\'sh000001\' ORDER BY report_date DESC LIMIT 1').fetchone()
                    signal = json.loads(signal_row[0]) if signal_row else {}
                    index_signal = json.loads(index_row[0]) if index_row else {}
                    if not signal.get('eligible') or signal.get('excluded') or not signal.get('prediction', {}).get('eligible') or signal.get('date') != target or index_signal.get('date') != target:
                        continue
                    reference_close = signal.get('raw_close', signal.get('close'))
                    if not reference_close or abs(q.get('previous_close', 0)-reference_close) > max(.02, reference_close*.0002):
                        con.execute("UPDATE orders SET note='昨收参考价变化，疑似除权；等待重新计算点位' WHERE id=?", (order['id'],))
                        continue
                    if not signal.get('entry_low', float('inf')) <= price <= signal.get('entry_high', 0):
                        continue
                    held = con.execute('SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=?', (order['symbol'],)).fetchone()[0]
                    cap = min(config()['max_position_pct'], signal.get('max_position_pct', 0))
                    if (held+order['quantity'])*price > self.portfolio()['equity']*cap/100:
                        continue
                fill = round(price*(1.0005 if buy else .9995), 2)
                if buy and not signal.get('entry_low', float('inf')) <= fill <= signal.get('entry_high', 0):
                    continue
                if (buy and fill > order['limit_price']) or (not buy and fill < order['limit_price']):
                    continue
                amount = fill*order['quantity']
                fee = round(max(5, amount*.0003) + amount*.00001 + (0 if buy else amount*.0005), 2)
                if buy:
                    if amount+fee > cash:
                        continue
                    cash -= amount+fee
                    con.execute('INSERT INTO lots(symbol,quantity,price,buy_date) VALUES(?,?,?,?)', (order['symbol'], order['quantity'], (amount+fee)/order['quantity'], moment.date().isoformat()))
                else:
                    lots = con.execute('SELECT * FROM lots WHERE symbol=? AND quantity>0 AND buy_date<? ORDER BY id', (order['symbol'], moment.date().isoformat())).fetchall()
                    if sum(x['quantity'] for x in lots) < order['quantity']:
                        continue
                    remaining = order['quantity']
                    for lot in lots:
                        take = min(remaining, lot['quantity'])
                        con.execute('UPDATE lots SET quantity=quantity-? WHERE id=?', (take, lot['id']))
                        remaining -= take
                    cash += amount-fee
                con.execute('UPDATE kv SET value=? WHERE key=\'cash\'', (encode(cash),))
                con.execute("UPDATE orders SET status='filled',filled_at=?,fill_price=?,note='模拟报价撮合，非真实成交' WHERE id=?", (moment.isoformat(), fill, order['id']))
                con.execute('INSERT INTO trades(order_id,datetime,symbol,side,quantity,price,fee) VALUES(?,?,?,?,?,?,?)', (order['id'], moment.isoformat(), order['symbol'], order['side'], order['quantity'], fill, fee))

    def reports(self):
        with connect() as con:
            return [dict(r) for r in con.execute('SELECT report_date AS date,COUNT(*)-1 AS count FROM signals GROUP BY report_date ORDER BY report_date DESC LIMIT 365')]

    def longterm(self):
        data = getkv('longterm', {'as_of': None, 'stocks': [], 'warnings': ['等待真实估值与已公告财报数据；不生成未经核验的长线推荐。']})
        if data.get('as_of') != expected_date():
            data = dict(data)
            data['warnings'] = list(data.get('warnings', [])) + ['长线快照未更新到最近完整交易日']
            data['stocks'] = [dict(s, eligible=False, stale=True) for s in data.get('stocks', [])]
        return data

    def agent_state(self):
        return {'status': {'mode':'manual_research','status':'manual','message':'云端生成日报，本人手动交易；不连接券商'}, 'events': [],
                'schedule': [{'time':'08:00','action':'收集当日新闻、行情和已公告财报'}, {'time':'09:00','action':'发布今日买入参考及前一交易日推荐的卖出参考'}], 'timezone':'Asia/Shanghai'}

    def export_csv(self, date=None):
        dash = self.dashboard(date)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['代码', '名称', '行情日期', '推荐分(非概率)', '状态', '收盘价', '买入下沿', '买入上沿', '失效价', '观察目标', '数据过期', '两交易日周期预期涨幅%', '经验下界%', '经验上界%', '验证MAE%', '综合预测排序值', '说明'])
        def safe(value):
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                return "'" + value
            return value
        for s in dash['stocks']:
            prediction = s.get('prediction', {})
            writer.writerow([safe(s.get(x)) for x in ('symbol','name','date','score','signal','close','entry_low','entry_high','stop_loss','take_profit','stale')] + [prediction.get(x) for x in ('expected_return_pct','interval_low_pct','interval_high_pct','validation_mae_pct','rank_value')] + [safe('；'.join(s.get('reasons', []) + s.get('warnings', [])))])
        return '\ufeff' + buf.getvalue()

    def write_report(self, date):
        folder = DATA / 'reports'
        folder.mkdir(exist_ok=True)
        (folder / (date+'.csv')).write_text(self.export_csv(date), encoding='utf-8')
        dash = self.dashboard(date)
        dash['meta'].pop('csrf_token', None)
        (folder / (date+'.json')).write_text(encode(dash), encoding='utf-8')

    def scheduler(self):
        """Legacy local clock disabled; unattended updates belong to the cloud."""
        return
