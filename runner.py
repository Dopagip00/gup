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

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
PUBLIC_TABLES = ('securities', 'histories', 'signals', 'runs')
PUBLIC_KEYS = {'universe_info', 'last_success', 'last_completed', 'valuation_quotes', 'longterm',
               'morning_news', 'quotes', 'github_collect', 'github_publish', 'github_fundamentals', 'github_run_status', 'model_recalculation'}
PUBLIC_SCHEMA = {
    'securities': ('symbol', 'name', 'active'),
    'histories': ('symbol', 'payload', 'updated_at'),
    'signals': ('report_date', 'symbol', 'payload'),
    'runs': ('id', 'started_at', 'ended_at', 'target_date', 'status', 'total', 'ok', 'failed', 'error'),
}
MAX_SEED_BYTES = 700 * 1024 * 1024
SEED_PART_BYTES = 4 * 1024 * 1024
MAX_SEED_COMPRESSED_BYTES = 95 * 1024 * 1024
MAX_PLANS_BYTES = 64 * 1024 * 1024


def now():
    return dt.datetime.now(TZ)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def public_key(key):
    return key in PUBLIC_KEYS or re.fullmatch(r'daily_plan:\d{4}-\d{2}-\d{2}', key) is not None


def clean_value(key, value):
    # Research timestamps survive; local watchlists, positions and settings do not.
    if key in ('last_success', 'last_completed') and isinstance(value, dict):
        return {k: value[k] for k in ('target_date', 'finished_at', 'status', 'ok', 'failed', 'total', 'universe', 'model_version') if k in value}
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


def run_stage(stage, core, service):
    moment = core.now(); date = moment.date().isoformat()
    result = {'date': date, 'stage': stage, 'started_at': moment.isoformat(), 'mode': 'manual_research'}
    if not core.md.is_trading_day(date):
        return dict(result, status='closed', reason='上交所官方日历休市，跳过采集与新推荐')
    target = core.md.last_completed_session(moment.replace(hour=9, minute=0, second=0, microsecond=0))
    result['target_date'] = target
    if stage == 'collect':
        if moment.time().replace(tzinfo=None) < dt.time(8):
            return dict(result, status='skipped', reason='尚未到北京时间08:00')
        if moment.time().replace(tzinfo=None) >= dt.time(15, 20):
            return dict(result, status='late_skipped', reason='采集启动已晚于当日收盘，不补造08点研究')
        news = collect_news(core)  # Sources and timestamps precede the long market download.
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
        core.putkv('github_collect', result)
    elif stage == 'publish':
        if moment.time().replace(tzinfo=None) < dt.time(9):
            return dict(result, status='skipped', reason='尚未到北京时间09:00，禁止提前发布')
        if moment.time().replace(tzinfo=None) < dt.time(9, 30):
            # Same authorized 09:00 job: recheck newly published notices before
            # selection, retaining the actual new completion time and receipts.
            collect_news(core)
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
            core.putkv('github_fundamentals', result)
            return result
        fundamentals = importlib.import_module('research_refresh').refresh_fundamentals()
        coverage = fundamentals.get('coverage', {})
        complete = coverage.get('financial_complete', 0) == coverage.get('valuation_qualified', 0)
        result.update(status='success' if complete else 'partial', finished_at=core.now().isoformat(), coverage=coverage,
                      as_of=fundamentals.get('as_of'), target_date=fundamentals.get('as_of'), message='单独更新长线研究，不改写已发布的买卖建议。')
        core.putkv('github_fundamentals', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('collect', 'publish', 'fundamentals'))
    parser.add_argument('--engine-dir', type=Path, default=ROOT / 'engine')
    parser.add_argument('--state-dir', type=Path, default=ROOT / '.state')
    parser.add_argument('--restore-cache', type=Path)
    parser.add_argument('--cache-output', type=Path, default=ROOT / '.cache' / 'export')
    parser.add_argument('--seed', type=Path, default=ROOT / 'seed' / 'research.json.gz')
    parser.add_argument('--plans-file', type=Path, default=ROOT / 'research' / 'daily-plans.json')
    parser.add_argument('--restore-plans', type=Path)
    parser.add_argument('--plans-output', type=Path, default=ROOT / '.cache' / 'plans-export')
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
    service = core.Service()
    failure = False
    try:
        if args.stage != 'collect':
            recalculation = importlib.import_module('model_refresh').refresh_cached_predictions(core)
            core.putkv('model_recalculation', recalculation)
            if recalculation.get('status') in ('error', 'conflict'):
                raise RuntimeError('模型重算失败或源数据已变化，暂停本次研究发布')
        result = run_stage(args.stage, core, service)
    except Exception as exc:
        result = {'date': core.now().date().isoformat(), 'stage': args.stage, 'status': 'error', 'finished_at': core.now().isoformat(),
                  'error': type(exc).__name__, 'mode': 'manual_research', 'message': '本次研究未完成；禁止将旧内容标作当日成功。'}
        core.putkv('github_'+args.stage, result)
        failure = True
    # The exporter must expose only public market research; no account/order API.
    result['restored_daily_plans'] = restored_plans
    result['archived_daily_plans'] = save_plans(core, args.plans_file.resolve(), args.plans_output.resolve())
    core.putkv('github_run_status', result)
    save_cache(core, state, args.cache_output.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    importlib.import_module('export_static').export_site(args.output_dir.resolve(), service, engine_dir=engine)
    (args.output_dir / 'run-status.json').write_text(encode(result), encoding='utf-8')
    print(encode(result))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('site_ready='+str((args.output_dir/'index.html').is_file()).lower()+'\n')
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
