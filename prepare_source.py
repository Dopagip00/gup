"""Refresh the public engine and initial market-only seed from the full local project."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

HERE=Path(__file__).resolve().parent
ENGINE_FILES={'app.py','core.py','daily_plan.py','daily_watchlist.py','main_wave.py','intraday_selection.py','technical_diagnosis.py','factor_screen.py','market_brief.py','forecast.py','strategy.py','market_data.py','longterm.py',
              'research_refresh.py','model_refresh.py','forecast_audit.py','portability.py','agent_runner.py','cloud_sync.py','version.json',
              'runtime.ps1','start.cmd','start.ps1','stop.cmd','stop.ps1','backup.cmd','backup.ps1',
              'update-now.cmd','update-now.ps1','apply-update.cmd','apply-update.ps1','MIGRATION.md',
              'AGENTS.md','PROJECT_STATE.md','AGENT_OPERATIONS.md','README.md','.gitignore'}


def write_seed_parts(seed):
    from runner import MAX_SEED_COMPRESSED_BYTES, SEED_PART_BYTES
    seed = Path(seed)
    total = seed.stat().st_size
    if not 0 < total <= MAX_SEED_COMPRESSED_BYTES:
        raise ValueError('公开研究种子超过分片总长度限制')
    parts, digest = [], hashlib.sha256()
    with seed.open('rb') as source:
        while True:
            chunk = source.read(SEED_PART_BYTES)
            if not chunk:
                break
            name = f'research.part{len(parts):03}.bin'
            (seed.parent / name).write_bytes(chunk)
            digest.update(chunk)
            parts.append({'name': name, 'size': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()})
    manifest = {'schema_version': 1, 'source': seed.name, 'compression': 'gzip',
                'chunk_bytes': SEED_PART_BYTES, 'total_bytes': total,
                'sha256': digest.hexdigest(), 'parts': parts}
    if sum(part['size'] for part in parts) != total:
        raise ValueError('生成分片时原始研究种子长度发生变化')
    manifest_path = seed.with_name(seed.name + '.parts.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest_path, manifest


def prepare(platform, *, source_only=False):
    from runner import clean_value,public_key
    platform=Path(platform).resolve();engine=HERE/'engine';engine.mkdir(exist_ok=True)
    for name in ENGINE_FILES:
        if (platform/name).is_file(): shutil.copyfile(platform/name,engine/name)
    for folder in ('static','tests'):
        shutil.copytree(platform/folder,engine/folder,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if source_only:
        return {'engine':str(engine),'source_only':True,'seed_unchanged':True}
    source=sqlite3.connect((platform/'data/market.sqlite3').as_uri()+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    seed=HERE/'seed/research.json.gz';seed.parent.mkdir(exist_ok=True)
    # Stream public tables into gzip: private orders/lots/trades/config/cash never read.
    with gzip.open(seed,'wt',encoding='utf-8',compresslevel=6) as out:
        out.write('{"schema_version":1,"tables":{')
        for n,table in enumerate(('securities','histories','signals','runs')):
            if n:out.write(',')
            out.write(json.dumps(table)+':[')
            for i,row in enumerate(source.execute('SELECT * FROM '+table)):
                if i:out.write(',')
                row=dict(row)
                if table=='runs':row['error']=None
                out.write(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False))
            out.write(']')
        out.write('},"kv":{')
        i=0
        for key,value in source.execute('SELECT key,value FROM kv ORDER BY key'):
            if not public_key(key):continue
            # A dated market source record, never a user-entered account record.
            if i:out.write(',')
            out.write(json.dumps(key)+':'+json.dumps(clean_value(key,json.loads(value)),ensure_ascii=False,separators=(',',':'),allow_nan=False));i+=1
        out.write('},"calendar":')
        calendar={}
        path=platform/'data/calendar_cache.json'
        if path.exists():calendar=json.loads(path.read_text(encoding='utf-8-sig'))
        out.write(json.dumps(calendar,ensure_ascii=False,separators=(',',':'))+'}')
    source.close()
    size=seed.stat().st_size
    manifest_path, manifest = write_seed_parts(seed)
    return {'engine':str(engine),'seed':str(seed),'seed_bytes':size,
            'parts_manifest':str(manifest_path),'part_count':len(manifest['parts']),
            'sha256':manifest['sha256']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--platform',default=str(HERE.parent/'ashare-platform'))
    parser.add_argument('--source-only',action='store_true',help='Only sync source; preserve the existing public seed and parts')
    args=parser.parse_args()
    print(json.dumps(prepare(args.platform,source_only=args.source_only),ensure_ascii=False))
