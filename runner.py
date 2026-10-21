"""GitHub-hosted public-market research. Never authenticates to a broker."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from ai_review import load_review as load_ai_review

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
PUBLIC_TABLES = ('securities', 'histories', 'signals', 'runs')
PUBLIC_KEYS = {'ai_review', 'universe_info', 'last_success', 'last_completed', 'valuation_quotes', 'longterm',
               'morning_news', 'quotes', 'github_collect', 'github_close_collect', 'github_publish', 'github_fundamentals', 'github_run_status', 'model_recalculation'}
PUBLIC_KEYS.add('github_recover')
PUBLIC_SCHEMA = {
    'securities': ('symbol', 'name', 'active'),
    'histories': ('symbol', 'payload', 'updated_at'),
    'signals': ('report_date', 'symbol', 'payload'),
    'runs': ('id', 'started_at', 'ended_at', 'target_date', 'status', 'total', 'ok', 'failed', 'error'),
}
MAX_SEED_BYTES = 700 * 1024 * 1024
SEED_PART_BYTES = 4 * 1024 * 1024
# V3 retains bounded per-cycle decision evidence. Raw JSON still has the
# independent 700 MiB ceiling; each GitHub source part remains only 4 MiB.
MAX_SEED_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_PLANS_BYTES = 64 * 1024 * 1024


def now():
    return dt.datetime.now(TZ)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def public_key(key):
    return key in PUBLIC_KEYS or re.fullmatch(r'daily_(?:plan|watchlist):\d{4}-\d{2}-\d{2}', key) is not None


def clean_value(key, value):
    # Research timestamps survive; local watchlists, positions and settings do not.
    if key in ('last_success', 'last_completed') and isinstance(value, dict):
        return {k: value[k] for k in ('target_date', 'finished_at', 'status', 'ok', 'failed', 'total', 'universe', 'model_version', 'chase_policy_version') if k in value}
    return value


def public_snapshot(source: Path, destination: Path):
    """Make a fresh whitelist DB, avoiding deleted/private bytes in SQLite freelists."""
    if not source.is_file():
        raise FileNotFoundError('缺少研究数据库')
    if source.resolve() == destination.resolve():
        raise ValueError('研究快照不能覆盖源数据库')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp.sqlite3')
    temporary.unlink(missing_ok=True)
    with contextlib.closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, contextlib.closing(sqlite3.connect(':memory:')) as consistent:
        original.backup(consistent)
        with contextlib.closing(sqlite3.connect(temporary)) as output, output:
            output.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            for table in PUBLIC_TABLES:
                schema = consistent.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone()
                if not schema:
                    continue
                actual = tuple(row[1] for row in consistent.execute(f'PRAGMA table_info("{table}")'))
                if actual != PUBLIC_SCHEMA[table]:
                    raise ValueError('研究表结构不兼容：' + table)
                output.execute(schema[0])
                placeholders = ','.join('?' for _ in actual)
                output.executemany(f'INSERT INTO "{table}" VALUES({placeholders})', consistent.execute(f'SELECT * FROM "{table}"'))
            if consistent.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kv'").fetchone():
                for key, raw in consistent.execute('SELECT key,value FROM kv'):
                    if public_key(key):
                        output.execute('INSERT INTO kv VALUES(?,?)', (key, encode(clean_value(key, json.loads(raw)))))
            if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('研究快照完整性检查失败')
    temporary.replace(destination)


def reassemble_seed(path: Path, output):
    """Verify a bounded, fixed-name public seed manifest before gzip decoding."""
    manifest_path = path.with_name(path.name + '.parts.json')
    if manifest_path.stat().st_size > 64 * 1024:
        raise ValueError('初始化研究分片清单超过大小限制')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    required = {'schema_version', 'source', 'compression', 'chunk_bytes', 'total_bytes', 'sha256', 'parts'}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get('schema_version') != 1 or manifest.get('source') != path.name or manifest.get('compression') != 'gzip':
        raise ValueError('初始化研究分片清单格式不兼容')
    total = manifest.get('total_bytes')
    parts = manifest.get('parts')
    valid_hash = lambda value: isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None
    if type(total) is not int or not 0 < total <= MAX_SEED_COMPRESSED_BYTES or type(manifest.get('chunk_bytes')) is not int or manifest['chunk_bytes'] != SEED_PART_BYTES or not valid_hash(manifest.get('sha256')):
        raise ValueError('初始化研究分片总长度或校验值无效')
    count = (total + SEED_PART_BYTES - 1) // SEED_PART_BYTES
    if not isinstance(parts, list) or len(parts) != count:
        raise ValueError('初始化研究分片数量不匹配')
    parent = path.parent.resolve()
    files = []
    # Validate every record and safe filename before reading or writing bytes.
    for index, part in enumerate(parts):
        expected_size = min(SEED_PART_BYTES, total - index * SEED_PART_BYTES)
        expected_name = f'research.part{index:03}.bin'
        if not isinstance(part, dict) or set(part) != {'name', 'size', 'sha256'} or part.get('name') != expected_name or type(part.get('size')) is not int or part['size'] != expected_size or not valid_hash(part.get('sha256')):
            raise ValueError('初始化研究分片文件名、顺序或长度无效')
        target = parent / expected_name
        if target.is_symlink() or target.resolve().parent != parent or not target.is_file():
            raise ValueError('初始化研究分片缺失或路径不安全：' + expected_name)
        if target.stat().st_size != expected_size:
            raise ValueError('初始化研究分片长度不匹配：' + expected_name)
        files.append((target, part))
    combined = hashlib.sha256()
    written = 0
    for target, part in files:
        digest = hashlib.sha256()
        received = 0
        with target.open('rb') as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > part['size']:
                    raise ValueError('初始化研究分片读取长度超限')
                digest.update(chunk)
                combined.update(chunk)
                output.write(chunk)
        if received != part['size'] or digest.hexdigest() != part['sha256']:
            raise ValueError('初始化研究分片SHA-256校验失败：' + part['name'])
        written += received
    if written != total or combined.hexdigest() != manifest['sha256']:
        raise ValueError('初始化研究完整文件SHA-256校验失败')
    return manifest


@contextlib.contextmanager
def open_seed_gzip(path: Path):
    if path.is_file():
        with gzip.open(path, 'rb') as source:
            yield source
    elif path.with_name(path.name + '.parts.json').is_file():
        # A temporary compressed file bounds RAM use; no restored DB bytes exist
        # until every part and the original compressed stream pass SHA-256.
        with tempfile.TemporaryFile(mode='w+b') as temporary:
            reassemble_seed(path, temporary)
            temporary.seek(0)
            with gzip.GzipFile(fileobj=temporary, mode='rb') as source:
                yield source
    else:
        yield None


def restore_seed(path: Path, core, state: Path):
    with open_seed_gzip(path) as source:
        if source is None:
            return False
        raw = source.read(MAX_SEED_BYTES + 1)
    if len(raw) > MAX_SEED_BYTES:
        raise ValueError('初始化研究文件超过大小限制')
    seed = json.loads(raw)
    if seed.get('schema_version') != 1 or not isinstance(seed.get('tables'), dict) or not isinstance(seed.get('kv', {}), dict):
        raise ValueError('初始化研究文件格式不兼容')
    if set(seed['tables']) - set(PUBLIC_TABLES):
        raise ValueError('初始化文件含非研究数据表，拒绝加载')
    with core.connect() as con:
        for table, rows in seed['tables'].items():
            columns = PUBLIC_SCHEMA[table]
            for row in rows:
                if not isinstance(row, dict) or set(row) - set(columns):
                    raise ValueError('初始化研究数据列无效')
                values = [encode(row[column]) if column == 'payload' and isinstance(row.get(column), (dict, list)) else row.get(column) for column in columns]
                con.execute(f'INSERT OR REPLACE INTO "{table}" VALUES({",".join("?" for _ in columns)})', values)
        for key, value in seed.get('kv', {}).items():
            if not public_key(key):
                raise ValueError('初始化文件含非研究键，拒绝加载')
            con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, encode(clean_value(key, value))))
    if isinstance(seed.get('calendar'), dict):
        (state / 'calendar_cache.json').write_text(encode(seed['calendar']), encoding='utf-8')
    return True


def restore_cache(cache: Path | None, state: Path):
    if not cache or not (cache / 'research.sqlite3').is_file():
        return False
    metadata = json.loads((cache / 'cache-meta.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256((cache / 'research.sqlite3').read_bytes()).hexdigest()
    if metadata.get('schema_version') != 1 or metadata.get('sha256') != digest:
        raise ValueError('研究缓存哈希或结构版本不匹配')
    public_snapshot(cache / 'research.sqlite3', state / 'market.sqlite3')
    if isinstance(metadata.get('calendars'), dict):
        (state / 'calendar_cache.json').write_text(encode(metadata['calendars']), encoding='utf-8')
    return True


def save_cache(core, state: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    db = output / 'research.sqlite3'
    public_snapshot(state / 'market.sqlite3', db)
    calendars = {}
    for filename in ('calendar_cache.json', 'calendar_overrides.json'):
        path = state / filename
        if path.is_file():
            values = json.loads(path.read_text(encoding='utf-8-sig'))
            for year, value in values.items():
                calendars[year] = core.md._validate_calendar(value, int(year))
    metadata = {'schema_version': 1, 'created_at': now().isoformat(), 'sha256': hashlib.sha256(db.read_bytes()).hexdigest(),
                'calendars': calendars, 'mode': 'manual_research', 'contains': '公开证券行情、研究信号和建议日报；无账户或交易记录'}
    (output / 'cache-meta.json').write_text(encode(metadata), encoding='utf-8')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def checked_plans(value):
    if not isinstance(value, dict):
        raise ValueError('日报档案格式错误')
    for date, plan in value.items():
        if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date or not isinstance(plan, dict) or plan.get('date') != date or plan.get('status') != 'published':
            raise ValueError('日报档案日期或发布状态错误')
        buy = plan.get('buy')
        if buy is not None:
            if not isinstance(buy, dict) or buy.get('recommended_date') != date or not isinstance(buy.get('sell_date'), str) or dt.date.fromisoformat(buy['sell_date']).isoformat() != buy['sell_date'] or buy['sell_date'] <= date:
                raise ValueError('日报原建议日期或D2日期错误，不自动修补')
    return value


def restore_plans(path: Path | None, core):
    """Merge immutable public advice. A conflicting published date aborts atomically."""
    if not path:
        return 0
    path = path / 'daily-plans.json' if path.is_dir() else path
    if not path.is_file():
        return 0
    if path.stat().st_size > MAX_PLANS_BYTES:
        raise ValueError('日报档案超过大小限制')
    archive = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(archive, dict) or archive.get('schema_version') != 1:
        raise ValueError('日报档案结构版本不兼容')
    plans = checked_plans(archive.get('plans'))
    digest = hashlib.sha256(canonical(plans).encode('utf-8')).hexdigest()
    if archive.get('sha256') != digest:
        raise ValueError('日报档案完整性校验失败')
    inserted = 0
    with core.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for date, plan in sorted(plans.items()):
            key = 'daily_plan:' + date
            row = con.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            existing = json.loads(row[0]) if row else None
            if isinstance(existing, dict) and existing.get('status') == 'published':
                if canonical(existing) != canonical(plan):
                    raise ValueError('原始已发布日报发生冲突，拒绝覆盖：' + date)
                continue
            con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, canonical(plan)))
            inserted += 1
    return inserted


def save_plans(core, repository_file: Path, artifact_dir: Path):
    plans = {}
    with core.connect() as con:
        for key, raw in con.execute("SELECT key,value FROM kv WHERE key LIKE 'daily_plan:%' ORDER BY key"):
            value = json.loads(raw)
            if public_key(key) and isinstance(value, dict) and value.get('status') == 'published':
                plans[key[len('daily_plan:'):]] = value
    checked_plans(plans)
    archive = {'schema_version': 1, 'plans': plans, 'sha256': hashlib.sha256(canonical(plans).encode('utf-8')).hexdigest()}
    payload = canonical(archive)
    if len(payload.encode('utf-8')) > MAX_PLANS_BYTES:
        raise ValueError('日报档案超过大小限制，请拆分永久档案后重试')
    repository_file.parent.mkdir(parents=True, exist_ok=True)
    repository_file.write_text(payload, encoding='utf-8')
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / 'daily-plans.json').write_text(payload, encoding='utf-8')
    return len(plans)


def checked_watchlists(value):
    if not isinstance(value, dict):
        raise ValueError('观察榜档案结构错误')
    for date, cohort in value.items():
        if (not isinstance(date,str) or dt.date.fromisoformat(date).isoformat()!=date
                or not isinstance(cohort,dict) or cohort.get('date')!=date
                or cohort.get('protocol')!='daily-observation-v1' or cohort.get('status')!='watch_only'
                or cohort.get('model_validated') is not False or cohort.get('actionable') is not False
                or not isinstance(cohort.get('items'),list) or not 1<=len(cohort['items'])<=5):
            raise ValueError('观察榜档案日期、协议或观察资格无效')
        data_date=cohort.get('data_date')
        if not isinstance(data_date,str) or dt.date.fromisoformat(data_date).isoformat()!=data_date or data_date>=date:
            raise ValueError('观察榜信号日错误')
        generated=dt.datetime.fromisoformat(cohort.get('generated_at',''))
        release=dt.datetime.fromisoformat(cohort.get('release_at',''))
        if (generated.utcoffset()!=dt.timedelta(hours=8) or release.utcoffset()!=dt.timedelta(hours=8)
                or release.date().isoformat()!=date or release.time().replace(tzinfo=None)!=dt.time(9)):
            raise ValueError('观察榜真实准备或揭榜时刻无效')
        published=cohort.get('published_at')
        if published:
            instant=dt.datetime.fromisoformat(published)
            if instant.utcoffset()!=dt.timedelta(hours=8) or instant<release or instant<generated:
                raise ValueError('观察榜实际发布时刻无效')
        seen=set()
        for item in cohort['items']:
            symbol=item.get('symbol') if isinstance(item,dict) else None
            if (not isinstance(symbol,str) or not re.fullmatch(r'sh\d{6}',symbol) or symbol in seen
                    or item.get('asof')!=data_date
                    or any(item.get(key) is not False for key in ('model_validated','eligible','actionable'))
                    or any(item.get(key) is not None for key in ('entry_low','entry_high','stop_loss','take_profit','max_position_pct'))):
                raise ValueError('观察档案含无效代码、重复排名或操作性指令')
            seen.add(symbol)
        if canonical(cohort.get('top_pick'))!=canonical(cohort['items'][0]):
            raise ValueError('观察档案首位与原始排名不一致')
    return value


def restore_watchlists(path, core):
    if not path or not path.is_file():return 0
    if path.stat().st_size>MAX_PLANS_BYTES:raise ValueError('观察榜档案超过大小限制')
    archive=json.loads(path.read_text(encoding='utf-8'))
    cohorts=checked_watchlists(archive.get('watchlists'))
    if archive.get('schema_version')!=1 or archive.get('sha256')!=hashlib.sha256(canonical(cohorts).encode()).hexdigest():
        raise ValueError('观察榜档案完整性校验失败')
    for value in cohorts.values():
        if value.get('published_at'):merge_watchlist_risks(value,value,core.now())
    count=0
    with core.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for date,value in sorted(cohorts.items()):
            key='daily_watchlist:'+date
            row=con.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            old=json.loads(row[0]) if row else None
            if isinstance(old,dict) and old.get('published_at'):
                if not value.get('published_at'):continue
                merged=merge_watchlist_risks(old,value,core.now())
                if canonical(merged)==canonical(old):continue
                con.execute('UPDATE kv SET value=? WHERE key=?',(canonical(merged),key));count+=1
                continue
            if isinstance(old,dict) and not value.get('published_at') and old.get('generated_at','')>=value.get('generated_at',''):
                continue
            con.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(key,canonical(value)));count+=1
    return count


WATCHLIST_RISK_FIELDS={'alerts','withdrawn_symbols','warnings','current_risk_verification','review_at','rankings_immutable'}


def merge_watchlist_risks(old, incoming, moment):
    """Union negative evidence while retaining the exact released cohort."""
    original=lambda value:{key:item for key,item in value.items() if key not in WATCHLIST_RISK_FIELDS}
    if canonical(original(old))!=canonical(original(incoming)):
        raise ValueError('已发布观察榜冲突，拒绝覆盖原始排名、分数或时刻')
    published=dt.datetime.fromisoformat(old['published_at'])
    symbols=[item['symbol'] for item in old['items']]
    allowed_alert={'symbol','reason','source','withdrawn','observed_at'}
    allowed_verification={'announcements_status','session_status','ai_status','known_risk_veto_count','evidence_cutoff'}
    def checked(record):
        alerts=record.get('alerts',[]);withdrawn=record.get('withdrawn_symbols',[]);warnings=record.get('warnings',[])
        verification=record.get('current_risk_verification',{})
        stamp=record.get('review_at');review=dt.datetime.fromisoformat(stamp) if stamp else None
        if (published>moment or review and (review.tzinfo is None or not published<=review<=moment)
                or not isinstance(alerts,list) or len(alerts)>1000 or not isinstance(withdrawn,list)
                or not isinstance(warnings,list) or len(warnings)>100
                or any(not isinstance(text,str) or len(text)>1000 for text in warnings)
                or not isinstance(verification,dict) or set(verification)-allowed_verification
                or record.get('rankings_immutable',True) is not True):
            raise ValueError('观察榜后续风险记录或真实复核时刻无效')
        if (alerts or withdrawn or verification) and review is None:
            raise ValueError('观察榜新增风险缺少真实复核时刻')
        cutoff=verification.get('evidence_cutoff')
        if cutoff:
            instant=dt.datetime.fromisoformat(cutoff)
            if instant.tzinfo is None or not published<=instant<=review:
                raise ValueError('观察榜风险证据来自未来或不属于本次复核')
        for alert in alerts:
            if (not isinstance(alert,dict) or set(alert)-allowed_alert or alert.get('symbol') not in symbols
                    or alert.get('withdrawn') is not True
                    or any(not isinstance(alert.get(key),str) or not alert[key].strip() or len(alert[key])>500 for key in ('source','reason'))):
                raise ValueError('观察榜风险撤销证据无效')
            if alert.get('observed_at'):
                observed=dt.datetime.fromisoformat(alert['observed_at'])
                if observed.tzinfo is None or observed>review:
                    raise ValueError('观察榜撤销证据来自未来')
        if any(symbol not in symbols for symbol in withdrawn):
            raise ValueError('撤销代码不在原始观察榜')
        return review,alerts,withdrawn,warnings
    left=checked(old);right=checked(incoming)
    result=json.loads(canonical(old));alerts=[];seen=set();withdrawn=set()
    for _,rows,saved,_ in (left,right):
        withdrawn.update(saved)
        for alert in rows:
            key=(alert['symbol'],alert['source'],alert['reason'])
            if key not in seen:seen.add(key);alerts.append(dict(alert))
            withdrawn.add(alert['symbol'])
    result['alerts']=alerts
    result['withdrawn_symbols']=[symbol for symbol in symbols if symbol in withdrawn]
    result['warnings']=list(dict.fromkeys(left[3]+right[3]))
    if left[0] or right[0]:
        newer=incoming if right[0] and (not left[0] or right[0]>left[0]) else old
        result['review_at']=newer['review_at']
        if 'current_risk_verification' in newer:result['current_risk_verification']=newer['current_risk_verification']
        result['rankings_immutable']=True
    return result


def save_watchlists(core, path):
    if path.is_file():restore_watchlists(path,core)
    with core.connect() as con:
        rows=con.execute("SELECT key,value FROM kv WHERE key LIKE 'daily_watchlist:%' ORDER BY key").fetchall()
    cohorts=checked_watchlists({key[len('daily_watchlist:'):]:json.loads(raw) for key,raw in rows})
    for value in cohorts.values():
        if value.get('published_at'):merge_watchlist_risks(value,value,core.now())
    archive={'schema_version':1,'watchlists':cohorts,'sha256':hashlib.sha256(canonical(cohorts).encode()).hexdigest()}
    payload=canonical(archive)
    if len(payload.encode())>MAX_PLANS_BYTES:raise ValueError('观察榜档案超过大小限制')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(payload,encoding='utf-8')
    return len(cohorts)


def collect_news(core):
    moment = core.now()
    try:
        target = core.md.last_completed_session(moment.replace(hour=9, minute=0, second=0, microsecond=0))
        result = importlib.import_module('news_collector').collect(coverage_start=target)
        if not isinstance(result, dict):
            raise ValueError('新闻采集器返回格式错误')
    except Exception as exc:
        result = {'date': moment.date().isoformat(), 'status': 'unavailable', 'as_of': moment.isoformat(), 'items': [],
                  'risk_exclusions': [], 'errors': ['新闻采集失败：' + type(exc).__name__]}
    try:
        if not core.md.is_trading_day(moment.date().isoformat()):
            raise ValueError('目标不是已核准交易日')
        session_risks = importlib.import_module('session_risks').collect(moment.date().isoformat())
        if not isinstance(session_risks, dict):
            raise ValueError('停复牌采集器返回格式错误')
    except Exception as exc:
        session_risks = {'status':'unavailable','session_date':moment.date().isoformat(),
            'risk_exclusions':[],'errors':[type(exc).__name__]}
    result = dict(result)
    result['session_risks'] = session_risks
    result['risk_exclusions'] = list(result.get('risk_exclusions') or []) + list(session_risks.get('risk_exclusions') or [])
    if session_risks.get('status') != 'complete':
        result['status'] = 'unavailable'
        result['errors'] = list(result.get('errors') or []) + ['当日上交所停复牌列表未取得完整记录，暂停新推荐。']
    finished = core.now()
    result['as_of'] = finished.isoformat()
    if finished.date() != moment.date() or not dt.time(8) <= finished.time().replace(tzinfo=None) < dt.time(9,30):
        result['status'] = 'unavailable'
        result['errors'] = list(result.get('errors') or []) + ['全部资讯核验完成已超出盘前窗口，暂停新推荐。']
    core.putkv('morning_news', result)
    return result


def fast_valuations(core, target):
    """Get the entire universe's dated quotes; avoid slow bulk financial HTTP at 08."""
    with core.connect() as con:
        universe = [dict(row) for row in con.execute('SELECT symbol,name FROM securities WHERE active=1 ORDER BY symbol')]
    quotes, errors = {}, []
    for offset in range(0, len(universe), 40):
        batch = [row['symbol'] for row in universe[offset:offset+40]]
        try:
            quotes.update(core.md.fetch_quotes(batch))
        except Exception as exc:
            errors.append({'symbols': batch, 'error': type(exc).__name__})
    core.putkv('valuation_quotes', {'as_of': target, 'quotes': quotes, 'errors': errors, 'fetched_at': core.now().isoformat()})
    with core.connect() as con:
        rows = con.execute('SELECT symbol,payload FROM signals WHERE report_date=? AND symbol<>\'sh000001\'', (target,)).fetchall()
        for row in rows:
            signal = json.loads(row['payload']); quote = quotes.get(row['symbol'], {})
            signal = core.quote_gate(signal, quote, target)
            con.execute('UPDATE signals SET payload=? WHERE report_date=? AND symbol=?', (core.encode(signal), target, row['symbol']))
    longterm = core.getkv('longterm', {})
    if longterm and longterm.get('as_of') != target:
        longterm['stocks'] = [dict(stock, eligible=False, stale=True) for stock in longterm.get('stocks', [])]
        longterm['warnings'] = list(longterm.get('warnings', [])) + ['08:00优先短线行情与同日报价；长线财务保留原快照，等待单独完整重估，不作为今日新推荐。']
        core.putkv('longterm', longterm)
    return {'received': len(quotes), 'total': len(universe), 'failed_batches': len(errors), 'financial_mode': 'retain_dated_snapshot'}


@contextlib.contextmanager
def short_term_refresh(core, target):
    # The existing engine calls this hook after its daily-bar/model work.
    module = importlib.import_module('research_refresh')
    original = module.refresh_fundamentals
    module.refresh_fundamentals = lambda: fast_valuations(core, target)
    try:
        yield
    finally:
        module.refresh_fundamentals = original


def _current_research(core, moment, target, *, after_close=False):
    """Reuse a proved report within its actual collection period.

    The completion receipt alone is insufficient after a source upgrade: every
    fresh active signal must also carry the current model and chase policy.
    Failed/stale securities remain visible and are allowed only within the same
    one-percent coverage tolerance used by daily publication.
    """
    receipt = core.getkv('last_completed', {})
    forecast = getattr(core, 'forecast', None)
    strategy = getattr(core, 'strategy', None)
    version = getattr(forecast, 'MODEL_VERSION', None)
    protocol = getattr(forecast, 'VALIDATION_PROTOCOL', None)
    policy = getattr(strategy, 'CHASE_POLICY_VERSION', None)
    if not (isinstance(receipt, dict) and version and protocol and policy
            and receipt.get('target_date') == target
            and receipt.get('status') in ('success', 'partial')
            and receipt.get('model_version') == version
            and receipt.get('chase_policy_version') == policy):
        return None
    try:
        finished = dt.datetime.fromisoformat(receipt.get('finished_at', ''))
        if finished.tzinfo is None:
            return None
        finished = finished.astimezone(TZ)
        clock = finished.time().replace(tzinfo=None)
        in_window = clock >= dt.time(16) if after_close else dt.time(8) <= clock < dt.time(9, 30)
        if not (finished.date() == moment.date() and in_window and finished <= moment):
            return None
        total, ok, failed = (receipt.get(key) for key in ('total', 'ok', 'failed'))
        if not (type(total) is int and type(ok) is int and type(failed) is int
                and total > 0 and 0 <= ok <= total and 0 <= failed <= total
                and ok + failed == total and ok / total >= .99):
            return None
        with core.connect() as con:
            if after_close:
                index = con.execute("SELECT payload FROM signals WHERE symbol='sh000001' AND report_date=?", (target,)).fetchone()
                benchmark = json.loads(index[0]) if index else None
                if not (isinstance(benchmark, dict) and benchmark.get('date') == target
                        and benchmark.get('stale') is not True):
                    return None
            rows = con.execute('''SELECT s.symbol,g.payload FROM securities s
                LEFT JOIN signals g ON g.symbol=s.symbol AND g.report_date=?
                WHERE s.active=1 AND s.symbol<>'sh000001' ORDER BY s.symbol''', (target,)).fetchall()
        if len(rows) != total:
            return None
        fresh = 0
        for symbol, raw in rows:
            signal = json.loads(raw) if raw is not None else None
            if not isinstance(signal, dict) or signal.get('stale') is True or signal.get('date') != target:
                continue
            prediction, chase = signal.get('prediction'), signal.get('chase_risk')
            if not (signal.get('symbol') == symbol and isinstance(prediction, dict)
                    and prediction.get('model_version') == version
                    and prediction.get('validation_protocol') == protocol
                    and prediction.get('as_of_date') == target
                    and isinstance(chase, dict) and chase.get('version') == policy
                    and chase.get('as_of_date') == target
                    and signal.get('recompute_status') != 'error'):
                return None
            fresh += 1
        return dict(receipt) if fresh >= ok and fresh / total >= .99 else None
    except (TypeError, ValueError, sqlite3.Error):
        # An unreadable/obsolete cache is recomputed within the collection
        # window; it is never upgraded merely by assigning current labels.
        return None


def current_morning_research(core, moment, target):
    """A closing refresh can never become a morning completion receipt."""
    return _current_research(core, moment, target)


def current_close_research(core, moment, target):
    return _current_research(core, moment, target, after_close=True)


def public_universe_receipt(core):
    """Expose bounded official-list provenance, not arbitrary engine settings."""
    value = core.getkv('universe_info', {})
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get('mode') == 'shanghai':
        result['mode'] = 'shanghai'
    if type(value.get('count')) is int and 0 < value['count'] <= 10000:
        result['count'] = value['count']
    for key in ('fetched_at', 'last_live_attempt_at', 'original_fetched_at', 'checked_at', 'collection_started_at', 'collection_finished_at'):
        if isinstance(value.get(key), str) and len(value[key]) <= 64:
            result[key] = value[key]
    for key in ('source', 'source_api'):
        source = value.get(key)
        if isinstance(source, str) and len(source) <= 300 and re.fullmatch(
                r'https://(?:www\.|query\.)?sse\.com\.cn/[A-Za-z0-9_./-]+', source):
            result[key] = source
    if type(value.get('cache_reused')) is bool:
        result['cache_reused'] = value['cache_reused']
    if isinstance(value.get('source_error'), str):
        result['source_error'] = value['source_error'][:300]
    if value.get('source_provenance') in ('sse_live_verified', 'legacy_sse_engine_path', 'sse_same_day_other_runner'):
        result['source_provenance'] = value['source_provenance']
    if value.get('collector_runner') == 'Windows':result['collector_runner']='Windows'
    if value.get('collector_context') == 'github_actions':result['collector_context']='github_actions'
    if isinstance(value.get('workflow_run_id'),str) and re.fullmatch(r'[0-9]{1,24}',value['workflow_run_id']):
        result['workflow_run_id']=value['workflow_run_id']
    if isinstance(value.get('receipt_sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',value['receipt_sha256']):
        result['receipt_sha256']=value['receipt_sha256']
    return result


def collect_after_close(core, service, moment):
    """Refresh real completed daily bars without rewriting the morning record."""
    date = moment.date().isoformat()
    target = core.md.last_completed_session(moment)
    result = {'date': date, 'stage': 'collect', 'collection_period': 'after_close',
              'started_at': moment.isoformat(), 'target_date': target,
              'mode': 'manual_research', 'morning_collection': False}
    cached = current_close_research(core, moment, target)
    if cached:
        result.update(status='cached', research=cached, research_cached=True,
                      research_finished_at=cached['finished_at'], finished_at=core.now().isoformat(),
                      universe_receipt=public_universe_receipt(core),
                      message='复用已核验的当日收盘行情；保留真实完成时间，不作为盘前采集或改写原日报。')
        core.putkv('github_close_collect', result)
        return result
    previous = core.getkv('last_success', {})
    try:
        if target != date or core.expected_date() != target:
            raise ValueError('官方收盘交易日与当前日期不一致')
        if previous.get('target_date') == target:
            core.putkv('last_success', {})
        with short_term_refresh(core, target):
            service.refresh(force=False, allow_same_day_universe_cache=True)
        finished = core.now()
        receipt = current_close_research(core, finished, target)
        if (service.status.get('status') not in ('success', 'partial') or receipt is None
                or dt.datetime.fromisoformat(receipt['finished_at']).astimezone(TZ) < moment):
            raise RuntimeError('收盘刷新未取得同日有效指数、模型与至少99%行情覆盖')
        result.update(status=service.status['status'], research=dict(service.status),
                      research_finished_at=receipt['finished_at'], finished_at=finished.isoformat(),
                      message='已采集真实收盘行情；不补造晨间资讯、AI复核或买入建议。')
    except Exception as exc:
        result.update(status='error', error=type(exc).__name__, finished_at=core.now().isoformat(),
                      reason='收盘行情刷新未通过完整性核验；不能将旧行情标为当日成功。')
        # This engine exception explicitly contains public provider/data-quality
        # failures safe to display. Other exceptions can contain local paths or
        # private configuration, so their messages remain excluded.
        market_error = getattr(core.md, 'MarketDataError', None)
        if isinstance(market_error, type) and isinstance(exc, market_error):
            result['error_detail'] = str(exc)[:300]
    finally:
        if not core.getkv('last_success') and previous:
            core.putkv('last_success', previous)
    result['universe_receipt'] = public_universe_receipt(core)
    core.putkv('github_close_collect', result)
    return result


def needs_cached_recalculation(stage, core, moment):
    if stage == 'collect':
        return False
    if (stage == 'publish' and moment.time().replace(tzinfo=None) >= dt.time(16)
            and core.md.is_trading_day(moment.date().isoformat())):
        target = core.md.last_completed_session(moment)
        if current_close_research(core, moment, target) is None:
            # The real collection computes the current model on new bars. Do
            # not first spend several minutes recomputing the old report.
            return False
    return True


def update_watchlist(core, service, moment):
    """Prepare independent observation research, preserving its real clock."""
    date = moment.date()
    if moment.time().replace(tzinfo=None) >= dt.time(16):
        for offset in range(1, 41):
            next_date = date + dt.timedelta(days=offset)
            if core.md.is_trading_day(next_date.isoformat()):
                date = next_date
                break
        else:
            raise RuntimeError('无法核验下一观察交易日')
    value = importlib.import_module('daily_watchlist').publish(
        date=date.isoformat(), service=service, now=moment)
    if not isinstance(value, dict):
        raise ValueError('观察榜返回格式无效')
    result = {key: value.get(key) for key in ('date', 'data_date', 'status', 'generated_at',
              'prepared_at', 'release_at', 'published_at', 'publication_timing', 'model_validated')}
    result['count'] = len(value.get('items') or [])
    top = value.get('top_pick')
    result['top_symbol'] = top.get('symbol') if isinstance(top, dict) else None
    return result


def attach_watchlist(result, core, service):
    moment = dt.datetime.fromisoformat(result.get('finished_at') or result['started_at'])
    result['watchlist'] = update_watchlist(core, service, moment)
    if result.get('status') == 'prepared' and (result['watchlist'].get('status') != 'watch_only' or not result['watchlist'].get('count')):
        result.update(status='pending',reason='观察榜尚未具备有效行情或必要证据，未完成预备；不把空榜记作准备成功。')
    return result


@contextlib.contextmanager
def official_source_context(core, directory):
    if directory is None:
        yield
        return
    from universe_receipt import use_receipt
    with use_receipt(core, directory):
        yield


def run_stage(stage, core, service):
    moment = core.now(); date = moment.date().isoformat()
    result = {'date': date, 'stage': stage, 'started_at': moment.isoformat(), 'mode': 'manual_research'}
    if not core.md.is_trading_day(date):
        return dict(result, status='closed', reason='上交所官方日历休市，跳过采集与新推荐')
    target = core.md.last_completed_session(moment.replace(hour=9, minute=0, second=0, microsecond=0))
    result['target_date'] = target
    after_close = moment.time().replace(tzinfo=None) >= dt.time(16)
    if stage == 'recover':
        # This explicit manual stage repairs completed data at its real time.
        # It cannot create an 08:00 receipt, rewrite advice, or fake a 09:00 pick.
        target=core.md.last_completed_session(moment)
        result.update(target_date=target,collection_period='manual_completed_session_recovery',morning_collection=False)
        core.putkv('ai_review',load_ai_review(ROOT,now=moment,signal_date=target))
        with short_term_refresh(core,target):
            service.refresh(force=True)
        result.update(status=service.status.get('status','error'),research=service.status,
                      finished_at=core.now().isoformat(),
                      message='按真实时刻恢复最近已完成交易日；不补造盘前完成或已发布买入记录。')
        if result['status'] != 'error':
            attach_watchlist(result,core,service)
        core.putkv('github_recover',result)
        return result
    if stage == 'collect' and after_close:
        closed = collect_after_close(core, service, moment)
        return attach_watchlist(closed, core, service) if closed['status'] != 'error' else closed
    if stage in ('collect','publish'):
        core.putkv('ai_review', load_ai_review(ROOT, now=moment, signal_date=target))
    if stage == 'collect':
        if moment.time().replace(tzinfo=None) < dt.time(8):
            return attach_watchlist(dict(result, status='prepared', reason='08点采集前仅准备独立观察榜，不发布买入建议'), core, service)
        if moment.time().replace(tzinfo=None) >= dt.time(9, 30):
            return attach_watchlist(dict(result, status='late_skipped', reason='已到北京时间09:30，不补造晨间采集或新买入建议'), core, service)
        if moment.time().replace(tzinfo=None) >= dt.time(9):
            return attach_watchlist(dict(result, status='skipped', reason='已进入09点发布窗口，跳过耗时行情重算并保留原始采集记录'), core, service)
        cached = current_morning_research(core, moment, target)
        if cached:
            news_snapshot = core.getkv('morning_news', {})
            result.update(status='cached', research=cached, research_cached=True,
                          research_finished_at=cached['finished_at'], finished_at=core.now().isoformat(),
                          news_status=news_snapshot.get('status') if isinstance(news_snapshot, dict) else None,
                          message='复用同日已核验的行情、模型与追高规则；原研究完成时间保留，09点发布时另核最新资讯。')
            core.putkv('github_collect', result)
            return attach_watchlist(result, core, service)
        news = collect_news(core)  # Sources and timestamps precede the long market download.
        market_start = core.now()
        if market_start.date() != moment.date() or market_start.time().replace(tzinfo=None) >= dt.time(9):
            return attach_watchlist(dict(result, status='late_skipped' if market_start.date() != moment.date() or
                        market_start.time().replace(tzinfo=None) >= dt.time(9, 30) else 'skipped',
                        finished_at=market_start.isoformat(), news_status=news.get('status'),
                        reason='资讯核验后已进入发布窗口，不再启动耗时行情重算；保留实际资讯时间与原研究记录'), core, service)
        previous = core.getkv('last_success', {})
        if previous.get('target_date') == target:
            core.putkv('last_success', {})  # Re-run models on valid cached bars, not a forced re-download.
        try:
            with short_term_refresh(core, target):
                service.refresh(force=False)
        finally:
            if not core.getkv('last_success') and previous:
                core.putkv('last_success', previous)
        result.update(status=service.status.get('status', 'error'), research=service.status, news_status=news.get('status'),
                      finished_at=core.now().isoformat())
        attach_watchlist(result, core, service)
        core.putkv('github_collect', result)
    elif stage == 'publish':
        if moment.time().replace(tzinfo=None) < dt.time(9):
            return attach_watchlist(dict(result, status='prepared',
                        reason='独立观察榜已预备；09:00仅按实际客户端时钟揭榜，不冒充服务器准点发布或买入建议'), core, service)
        if after_close:
            result['close_research'] = collect_after_close(core, service, moment)
            if result['close_research']['status'] == 'error':
                result.update(status='error', finished_at=core.now().isoformat(),
                              reason='当日收盘行情采集失败，保留原日报并使本次云端任务明确失败。')
                core.putkv('github_publish', result)
                return result
        if moment.time().replace(tzinfo=None) < dt.time(9, 30):
            # Same authorized 09:00 job: recheck newly published notices before
            # selection, retaining the actual new completion time and receipts.
            collect_news(core)
        result['watchlist'] = update_watchlist(core, service, core.now())
        plan = importlib.import_module('daily_plan').publish()
        result.update(status=plan.get('status'), finished_at=core.now().isoformat(), published_at=plan.get('published_at'),
                      morning_ready=plan.get('morning_ready', False), late=plan.get('late', False),
                      buy_symbol=(plan.get('buy') or {}).get('symbol'), sell_count=len(plan.get('sell', [])))
        core.putkv('github_publish', result)
    else:
        latest_target = core.expected_date()
        previous = core.getkv('longterm', {})
        previous_coverage = previous.get('coverage', {})
        same_complete = previous.get('as_of') == latest_target and previous_coverage.get('valuation_qualified', 0) > 0 and previous_coverage.get('financial_complete') == previous_coverage.get('valuation_qualified') and previous_coverage.get('not_researched', 0) == 0
        if same_complete:
            quote_check = fast_valuations(core, latest_target)
            result.update(status='cached', finished_at=core.now().isoformat(), as_of=latest_target, target_date=latest_target,
                          quote_coverage=quote_check, message='已存在同日完整财务快照，仅核验同日报价；不重复抓取全部财报或改写买卖日报。')
            attach_watchlist(result, core, service)
            core.putkv('github_fundamentals', result)
            return result
        fundamentals = importlib.import_module('research_refresh').refresh_fundamentals()
        coverage = fundamentals.get('coverage', {})
        qualified=coverage.get('valuation_qualified')
        complete = (type(qualified) is int and qualified>0 and coverage.get('financial_complete')==qualified
                    and coverage.get('not_researched',0)==0 and fundamentals.get('as_of')==latest_target)
        result.update(status='success' if complete else 'partial', finished_at=core.now().isoformat(), coverage=coverage,
                      as_of=fundamentals.get('as_of'), target_date=fundamentals.get('as_of'), message='单独更新长线研究，不改写已发布的买卖建议。')
        attach_watchlist(result, core, service)
        core.putkv('github_fundamentals', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('collect', 'publish', 'fundamentals', 'recover'))
    parser.add_argument('--engine-dir', type=Path, default=ROOT / 'engine')
    parser.add_argument('--state-dir', type=Path, default=ROOT / '.state')
    parser.add_argument('--restore-cache', type=Path)
    parser.add_argument('--cache-output', type=Path, default=ROOT / '.cache' / 'export')
    parser.add_argument('--universe-dir',type=Path,help='Current-run independently collected official source receipt')
    parser.add_argument('--seed', type=Path, default=ROOT / 'seed' / 'research.json.gz')
    parser.add_argument('--plans-file', type=Path, default=ROOT / 'research' / 'daily-plans.json')
    parser.add_argument('--restore-plans', type=Path)
    parser.add_argument('--plans-output', type=Path, default=ROOT / '.cache' / 'plans-export')
    parser.add_argument('--watchlists-file', type=Path, default=ROOT / 'research' / 'daily-watchlists.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'public')
    args = parser.parse_args(argv)
    engine, state = args.engine_dir.resolve(), args.state_dir.resolve()
    if not (engine / 'core.py').is_file():
        parser.error('engine目录缺少core.py；请放入现有平台的研究源码')
    state.mkdir(parents=True, exist_ok=True)
    os.environ['ASHARE_DATA_DIR'] = str(state)
    sys.path.insert(0, str(engine))
    restored = restore_cache(args.restore_cache, state)
    core = importlib.import_module('core'); core.init_db()
    if not restored:
        with core.connect() as con:
            cold = not con.execute('SELECT 1 FROM signals LIMIT 1').fetchone()
        if cold:
            restore_seed(args.seed, core, state)
    restored_plans = restore_plans(args.plans_file, core)
    restored_plans += restore_plans(args.restore_plans, core)
    restored_watchlists = restore_watchlists(args.watchlists_file, core)
    service = core.Service()
    failure = False
    try:
        if args.stage != 'recover' and needs_cached_recalculation(args.stage, core, core.now()):
            recalculation = importlib.import_module('model_refresh').refresh_cached_predictions(core)
            core.putkv('model_recalculation', recalculation)
            if recalculation.get('status') in ('error', 'conflict'):
                raise RuntimeError('模型重算失败或源数据已变化，暂停本次研究发布')
        with official_source_context(core,args.universe_dir):
            result = run_stage(args.stage, core, service)
        if core.getkv('universe_info'):
            result['universe_receipt']=public_universe_receipt(core)
        failure = result.get('status') in ('error', 'conflict')
    except Exception as exc:
        result = {'date': core.now().date().isoformat(), 'stage': args.stage, 'status': 'error', 'finished_at': core.now().isoformat(),
                  'error': type(exc).__name__, 'mode': 'manual_research', 'message': '本次研究未完成；禁止将旧内容标作当日成功。'}
        core.putkv('github_'+args.stage, result)
        failure = True
    # The exporter must expose only public market research; no account/order API.
    result['restored_daily_plans'] = restored_plans
    result['archived_daily_plans'] = save_plans(core, args.plans_file.resolve(), args.plans_output.resolve())
    result['restored_watchlists'] = restored_watchlists
    core.putkv('github_run_status', result)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    importlib.import_module('export_static').export_site(args.output_dir.resolve(), service, engine_dir=engine)
    # Export reads can create the next dated preparation or persist new risk
    # withdrawals. Archive that final cohort, so deployment and recovery agree.
    result['archived_watchlists'] = save_watchlists(core,args.watchlists_file.resolve())
    core.putkv('github_run_status', result)
    save_cache(core, state, args.cache_output.resolve())
    manifest_path=args.output_dir/'data/manifest.json'
    if manifest_path.is_file():
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['run']=result
        manifest_path.write_text(encode(manifest),encoding='utf-8')
        (args.output_dir/'data/run-status.json').write_text(encode(result),encoding='utf-8')
    (args.output_dir / 'run-status.json').write_text(encode(result), encoding='utf-8')
    print(encode(result))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('site_ready='+str((args.output_dir/'index.html').is_file()).lower()+'\n')
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
