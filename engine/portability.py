"""Portable backups and validated code-only updates using Python 3.10+ stdlib.

Archives are integrity checked, not cryptographically signed: accept update
packages only from a source you trust. No browser profiles or account sessions
are collected. Data-schema migrations must be explicitly implemented first.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT_ID = 'huyan-ashare'
FORMAT_VERSION = 1
KNOWN_SCHEMA_VERSION = 1
MANIFEST = 'manifest.json'
MAX_ENTRIES = 20000
MAX_UPDATE_BYTES = 512 * 1024 * 1024
MAX_BACKUP_BYTES = 32 * 1024 * 1024 * 1024
CODE_DIRS = {'static', 'tests', 'docs', 'scripts'}
CODE_SUFFIXES = {'.py', '.pyi', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss', '.md', '.txt', '.json', '.svg', '.png', '.jpg', '.jpeg', '.ico', '.webp', '.cmd', '.ps1', '.sh', '.toml', '.yaml', '.yml'}
ROOT_CODE_SUFFIXES = {'.py', '.ps1', '.cmd', '.md', '.sh'}
ROOT_CODE_FILES = {'version.json', '.gitignore', 'requirements.txt', 'pyproject.toml', 'package.json', 'package-lock.json', 'schedules.template.json'}
EXCLUDED_DIRS = {'.git', '.venv', 'venv', 'env', '__pycache__', 'node_modules', '.pytest_cache', '.mypy_cache', '.codex', '.aws', '.ssh', '.config', 'backups', 'work', 'temp', 'tmp', 'futu', 'opend', 'browser', 'browser-profile', 'browser_profiles', 'profiles'}
SENSITIVE_NAME = re.compile(r'(^|[._-])(passwords?|passwd|cookies?|credentials?|secrets?|tokens?|auth|login|futu|opend)([._-]|$)', re.I)
SECRET_KEYS = re.compile(r'^(password|passwd|cookie|cookies|access_token|refresh_token|api_key|apikey|private_key|client_secret|session_cookie|authorization)$', re.I)
DEVICE_KEYS = re.compile(r'^(thread_?id|client_?thread_?id|host_?id|automation_?id|task_?id|schedule_?id|pid|process_?id|cwd|working_?directory|absolute_?path)$', re.I)
EPHEMERAL_KV_KEYS = {'schedule_status', 'agent_status', 'server_status', 'csrf_token', 'session_token', 'auth_token'}


class PortabilityError(RuntimeError):
    pass


def _utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _stamp():
    return dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _version(value):
    if not isinstance(value, dict) or value.get('project_id') != PROJECT_ID:
        raise PortabilityError('项目标识不匹配；此工具只接受本项目的备份和更新包。')
    version = str(value.get('version', ''))
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?', version):
        raise PortabilityError('version.json 中缺少有效版本号。')
    schema = value.get('data_schema_version')
    if type(schema) is not int or not 1 <= schema <= KNOWN_SCHEMA_VERSION:
        raise PortabilityError(f'不支持数据库结构版本 {schema}；当前工具仅支持 1–{KNOWN_SCHEMA_VERSION}，需要带数据库迁移程序的新版本。')
    return {'project_id': PROJECT_ID, 'version': version, 'data_schema_version': schema}


def read_version(root):
    try:
        return _version(json.loads((Path(root) / 'version.json').read_text(encoding='utf-8-sig')))
    except (OSError, ValueError) as exc:
        raise PortabilityError('无法读取项目根目录的 version.json。') from exc


def _version_tuple(value):
    return tuple(int(x) for x in re.split(r'[-+]', value, 1)[0].split('.'))


def _path(name):
    if not isinstance(name, str) or not name or any(ch in name for ch in '\\:\x00<>"|?*'):
        raise PortabilityError('压缩包包含无效路径。')
    path = PurePosixPath(name)
    parts = name.split('/')
    if path.is_absolute() or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts):
        raise PortabilityError(f'压缩包路径不安全：{name}')
    reserved = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
    if any(reserved.match(p) or any(ord(ch) < 32 for ch in p) for p in parts):
        raise PortabilityError(f'压缩包包含系统保留文件名：{name}')
    return path


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _target(root, name):
    root = Path(root).resolve()
    relative = _path(name)
    current = root
    for piece in relative.parts:
        current = current / piece
        if _linked(current):
            raise PortabilityError(f'拒绝经过符号链接或目录联接写入：{name}')
    if not current.resolve().is_relative_to(root):
        raise PortabilityError(f'路径超出项目目录：{name}')
    return current


def _excluded(relative):
    parts = relative.parts
    lower = [part.lower() for part in parts]
    name = lower[-1]
    return (any(x in EXCLUDED_DIRS for x in lower[:-1]) or name == MANIFEST or
            name in {'.env', '.env.local', '.env.production', 'local state', 'login data', 'web data'} or
            name.startswith('.env.') or bool(SENSITIVE_NAME.search(name)) or
            (name.endswith(('.zip',)) and not (lower[0]=='runtime' and re.fullmatch(r'python\d+\.zip',name))) or
            name.endswith(('.pyc', '.pyo', '.7z', '.tar', '.gz', '.pem', '.key', '.pfx', '.p12', '-wal', '-shm', '-journal', '.pid', '.lock')))


def is_code_path(name):
    path = _path(name)
    if _excluded(path):
        return False
    if len(path.parts) == 1:
        return path.suffix.lower() in ROOT_CODE_SUFFIXES or path.name in ROOT_CODE_FILES
    return path.parts[0] in CODE_DIRS and path.suffix.lower() in CODE_SUFFIXES


def _walk(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED_DIRS and not _linked(parent / d))
        for name in sorted(files):
            path = parent / name
            relative = path.relative_to(root)
            if _linked(path) or _excluded(PurePosixPath(relative.as_posix())):
                continue
            yield relative.as_posix(), path


def _contains_secret(value):
    if isinstance(value, dict):
        return any((SECRET_KEYS.fullmatch(str(k)) and v not in (None, '', False, [], {})) or _contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(x) for x in value)
    return False


def _scrub_metadata(value):
    """Strip machine credentials/IDs from migration metadata, never from prices."""
    if isinstance(value, dict):
        return {k: _scrub_metadata(v) for k, v in value.items() if not SECRET_KEYS.fullmatch(str(k)) and not DEVICE_KEYS.fullmatch(str(k))}
    if isinstance(value, list):
        return [_scrub_metadata(x) for x in value]
    return value


def _snapshot_database(source, destination):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256, sleep=0.05)
            check = dst.execute('PRAGMA quick_check').fetchone()[0]
            if check != 'ok':
                raise PortabilityError(f'SQLite 快照完整性检查失败：{source.name}')
            # Local process and scheduling state is never an active registration
            # on a different computer. Keep market data, reports and positions.
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kv'").fetchone():
                for key, raw in dst.execute('SELECT key,value FROM kv').fetchall():
                    if key in EPHEMERAL_KV_KEYS or SECRET_KEYS.fullmatch(str(key)):
                        dst.execute('DELETE FROM kv WHERE key=?', (key,))
                        continue
                    try:
                        value = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if _contains_secret(value):
                        dst.execute('UPDATE kv SET value=? WHERE key=?', (json.dumps(_scrub_metadata(value), ensure_ascii=False), key))
                dst.commit()
            # Snapshot uses one self-contained DB file, never live WAL sidecars.
            dst.execute('PRAGMA journal_mode=DELETE')


def _summary(database):
    result = {'database': 'data/market.sqlite3', 'tables': {}, 'latest_report_date': None}
    if database is None:
        result['database'] = None
        return result
    with closing(sqlite3.connect(database)) as con:
        tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('histories', 'securities', 'signals', 'orders', 'lots', 'trades'):
            if table in tables:
                result['tables'][table] = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        if 'signals' in tables:
            result['latest_report_date'] = con.execute('SELECT MAX(report_date) FROM signals').fetchone()[0]
    return result


def _handoff(version, summary, created):
    counts = '\n'.join(f'- {key}：{value}' for key, value in summary['tables'].items()) or '- 没有本地数据库。'
    return f'''# 项目迁移交接

生成时间：{created}；项目：{PROJECT_ID}；代码版本：{version['version']}；数据库结构：{version['data_schema_version']}。

最近本地报告日期：{summary.get('latest_report_date') or '无'}。这不是新电脑当前行情的新鲜度证明。

{counts}

## 当前工作方式

用户手动交易。用户已同意公开GitHub研究网站，目标仓库Dopagip00/gup，网址https://dopagip00.github.io/gup/，手机可看；普通Pages无需个人登录。北京时间交易日08:00资讯、09:00买入研究和前日建议的D2卖出参考。Actions可能延迟，不能保证09:00整点；部署、启用与实际日报需独立核验，此备份不是上线证明。任务正常运行后不要求新电脑常开。

## 在另一台电脑继续

1. 把完整包恢复至独立文件夹，验证manifest.json。Windows 64位完整包已含runtime便携Python；其他系统或未带runtime的包需Python 3.10+。
2. 在 Codex 打开该项目文件夹，让新任务先读 PROJECT_STATE.md、AGENT_OPERATIONS.md、MIGRATION.md 和本文件；不存在的交接文件以实际项目内容为准。
3. 本地研究界面可用 start.cmd；只绑定本机 127.0.0.1，不会因此发布到互联网。
4. 更新代码用 portability.py apply-update；不要把开发机的 data 目录覆盖到已有使用记录的新电脑。
5. 整个项目迁移使用根目录完整打包工具，包含ashare-platform、ashare-github和历史ashare-cloud。后续先改本机源码并测试，再运行ashare-github/prepare_source.py更新公开engine/seed并提交push到同一Dopagip00/gup仓库。GitHub状态与Actions研究artifact独立核验；不复用旧电脑绝对路径或旧任务ID，不继续历史Sites发布。

ZIP不包含Codex聊天记录、登录Cookies、密码或浏览器个人资料，也不会自动迁移GitHub授权、Actions artifact和任务。完整备份含本机数据，不得整体上传公开仓库。migration/schedules.json只是相对路径的待核验计划说明，不会创建任务。
'''.encode('utf-8')


def _schedule_template():
    return {'timezone': 'Asia/Shanghai', 'mode': 'manual_research', 'execution': 'github_actions',
            'activation': 'verify_in_hosted_service_not_activated_by_zip',
            'project_root': '.', 'guide': 'MIGRATION.md',
            'tasks': [{'time': '08:00', 'purpose': '资讯研究更新'},
                      {'time': '09:00', 'purpose': '买入研究建议及昨日建议卖出观察价'}],
            'notes': '目标Dopagip00/gup公开研究。Pages部署、Actions启用与日志独立核验，可能延迟，不保证09:00整点。本地仅备用/开发，不启用旧交易计划。'}


def _put_file(archive, name, source):
    digest, count = hashlib.sha256(), 0
    with source.open('rb') as reader, archive.open(name, 'w', force_zip64=True) as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
            writer.write(chunk)
    return {'sha256': digest.hexdigest(), 'size': count}


def _put_bytes(archive, name, payload):
    archive.writestr(name, payload)
    return {'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}


def _output_path(output):
    path = Path(output).expanduser()
    if not path.is_absolute():
        raise PortabilityError('--output 必须是绝对路径。')
    if path.suffix.lower() != '.zip':
        raise PortabilityError('输出文件扩展名必须是 .zip。')
    path = path.resolve()
    if path.exists():
        raise PortabilityError('输出文件已存在，请使用新的文件名，避免覆盖旧备份。')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _archive(root, output, kind):
    root = Path(root).resolve()
    version = read_version(root)
    output = _output_path(output)
    created = _utc()
    manifest = {'format_version': FORMAT_VERSION, 'kind': kind, 'created_at': created,
                'project': version, 'files': {}, 'credential_policy': 'exclude credential files and sanitize credential-bearing KV metadata',
                'portable_state': 'market data, reports, positions and audit history; no login or active task registration'}
    if kind == 'full-backup' and os.environ.get('ASHARE_DATA_DIR'):
        if Path(os.environ['ASHARE_DATA_DIR']).resolve() != (root / 'data').resolve():
            raise PortabilityError('当前 ASHARE_DATA_DIR 指向项目外。请先将业务数据迁回本项目 data 并取消该变量，再生成迁移包；避免遗漏数据。')
    temporary = output.with_name('.' + output.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tempfile.TemporaryDirectory(prefix='ashare-snapshot-') as scratch_name:
            scratch = Path(scratch_name)
            main_snapshot = None
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for name, source in _walk(root):
                    if source == temporary or source == output or name.startswith('migration/'):
                        continue
                    if kind == 'code-update' and not is_code_path(name):
                        continue
                    if source.suffix.lower() == '.json':
                        try:
                            value = json.loads(source.read_text(encoding='utf-8-sig'))
                        except (OSError, ValueError):
                            value = None
                        if _contains_secret(value):
                            raise PortabilityError(f'检测到含凭据的 JSON 文件：{name}。请移除凭据或改用环境配置后重新备份。')
                    if kind == 'full-backup' and source.suffix.lower() in ('.sqlite', '.sqlite3', '.db'):
                        snapshot = scratch / (uuid.uuid4().hex + '.sqlite3')
                        _snapshot_database(source, snapshot)
                        if name == 'data/market.sqlite3':
                            main_snapshot = snapshot
                        source = snapshot
                    manifest['files'][name] = _put_file(archive, name, source)
                if 'version.json' not in manifest['files']:
                    raise PortabilityError('归档缺少 version.json。')
                if kind == 'full-backup':
                    summary = _summary(main_snapshot)
                    manifest['state_summary'] = summary
                    manifest['files']['migration/session-handoff.md'] = _put_bytes(archive, 'migration/session-handoff.md', _handoff(version, summary, created))
                    manifest['files']['migration/schedules.json'] = _put_bytes(archive, 'migration/schedules.json', _json_bytes(_schedule_template()))
                archive.writestr(MANIFEST, _json_bytes(manifest))
            os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {'path': str(output), 'kind': kind, 'version': version['version'], 'files': len(manifest['files']),
            'archive_sha256': _file_hash(output), 'size': output.stat().st_size}


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backup(root, output):
    return _archive(root, output, 'full-backup')


def export_update(root, output):
    return _archive(root, output, 'code-update')


def _validate_archive(package, required_kind=None, stage=None):
    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise PortabilityError('压缩包文件数超过上限。')
            names, folded = set(), set()
            for info in infos:
                _path(info.filename)
                if info.filename.casefold() in folded:
                    raise PortabilityError('压缩包包含重复路径或大小写冲突。')
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode not in (0, stat.S_IFREG):
                    raise PortabilityError('压缩包包含目录项、符号链接或特殊文件；只接受本工具生成的文件归档。')
                if info.flag_bits & 1:
                    raise PortabilityError('不支持加密 ZIP。请先在独立位置解密并验证来源。')
                names.add(info.filename)
                folded.add(info.filename.casefold())
            if MANIFEST not in names or archive.getinfo(MANIFEST).file_size > 8 * 1024 * 1024:
                raise PortabilityError('缺少有效 manifest.json。')
            manifest = json.loads(archive.read(MANIFEST))
            if not isinstance(manifest, dict):
                raise PortabilityError('manifest 必须是 JSON 对象。')
            if manifest.get('format_version') != FORMAT_VERSION:
                raise PortabilityError('归档格式版本不受支持。')
            kind = manifest.get('kind')
            if kind not in ('full-backup', 'code-update') or (required_kind and kind != required_kind):
                raise PortabilityError(f'需要 {required_kind or "合法"} 类型归档，不能用完整备份覆盖已有项目的数据。')
            _version(manifest.get('project'))
            files = manifest.get('files')
            if not isinstance(files, dict) or set(files) != names - {MANIFEST} or 'version.json' not in files:
                raise PortabilityError('manifest 文件清单与 ZIP 内容不一致。')
            limit = MAX_UPDATE_BYTES if kind == 'code-update' else MAX_BACKUP_BYTES
            if sum(i.file_size for i in infos) > limit:
                raise PortabilityError('归档展开大小超过上限。')
            for name, expected in files.items():
                _path(name)
                if kind == 'code-update' and not is_code_path(name):
                    raise PortabilityError(f'代码更新包试图覆盖非代码目录：{name}')
                if not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', str(expected.get('sha256', ''))):
                    raise PortabilityError('manifest 中的哈希或文件长度不合法。')
                info = archive.getinfo(name)
                if expected['size'] != info.file_size:
                    raise PortabilityError(f'文件长度校验失败：{name}')
                target = _target(stage, name) if stage else None
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                digest, length = hashlib.sha256(), 0
                with archive.open(name) as reader:
                    writer = target.open('wb') if target else None
                    try:
                        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                            length += len(chunk)
                            if length > expected['size']:
                                raise PortabilityError(f'文件解压长度异常：{name}')
                            digest.update(chunk)
                            if writer:
                                writer.write(chunk)
                    finally:
                        if writer:
                            writer.close()
                if length != expected['size'] or digest.hexdigest() != expected['sha256']:
                    raise PortabilityError(f'SHA256 校验失败，文件已损坏或被改动：{name}')
            archived_version = _version(json.loads(archive.read('version.json')))
            if archived_version != manifest['project']:
                raise PortabilityError('version.json 与 manifest 版本信息不一致。')
            return manifest
    except (zipfile.BadZipFile, OSError, ValueError, KeyError, RuntimeError) as exc:
        if isinstance(exc, PortabilityError):
            raise
        raise PortabilityError(f'归档无法读取或校验失败：{type(exc).__name__}') from exc


@contextmanager
def stopped_application(ports=(8765, 18767)):
    locks = []
    try:
        for port in ports:
            lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                lock.bind(('127.0.0.1', int(port)))
                lock.listen(1)
            except OSError as exc:
                lock.close()
                raise PortabilityError(f'端口 {port} 正在使用，可能有平台或更新任务运行。请先运行 stop.ps1，并等待数据同步结束；若是其它程序占用，请自行确认关闭。工具不会强杀任何进程。') from exc
            locks.append(lock)
        yield
    finally:
        for lock in locks:
            lock.close()


def apply_update(root, package, ports=(8765, 18767)):
    root = Path(root).resolve()
    local = read_version(root)
    marker = _target(root, 'migration/update-in-progress.json')
    if marker.exists():
        raise PortabilityError('发现未完成的更新。请先按 migration/update-in-progress.json 指向的备份检查恢复，再重试。')
    with tempfile.TemporaryDirectory(prefix='ashare-update-') as stage_name:
        stage = Path(stage_name)
        manifest = _validate_archive(package, 'code-update', stage)
        incoming = manifest['project']
        if incoming['data_schema_version'] != local['data_schema_version']:
            raise PortabilityError('更新包与本地数据库结构版本不同，需要专门的数据迁移程序。')
        if _version_tuple(incoming['version']) < _version_tuple(local['version']):
            raise PortabilityError('拒绝代码降级；如需回滚，请使用更新前完整备份恢复到独立目录。')
        targets = {name: _target(root, name) for name in manifest['files']}
        for target in targets.values():
            if target.exists() and not target.is_file():
                raise PortabilityError('更新目标已有同名目录，无法覆盖。')
        with stopped_application(ports):
            prior = backup(root, _target(root, 'backups/pre-update-' + _stamp() + '.zip'))
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(_json_bytes({'backup': Path(prior['path']).relative_to(root).as_posix(), 'started_at': _utc(), 'target_version': incoming['version'], 'files': list(targets)}))
            applied = []
            try:
                for name in sorted(targets, key=lambda n: (n == 'version.json', n)):
                    destination = _target(root, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # os.replace is atomic for each file on the same filesystem;
                    # copy the validated bytes adjacent to the target first.
                    temporary = destination.with_name('.' + destination.name + '.update-' + uuid.uuid4().hex)
                    try:
                        shutil.copyfile(stage / Path(name), temporary)
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                    applied.append(name)
            except Exception as exc:
                try:
                    with zipfile.ZipFile(prior['path']) as previous:
                        previous_names = set(previous.namelist())
                        for name in reversed(applied):
                            destination = _target(root, name)
                            if name in previous_names:
                                destination.write_bytes(previous.read(name))
                            else:
                                destination.unlink(missing_ok=True)
                    marker.unlink(missing_ok=True)
                except Exception as rollback:
                    raise PortabilityError('更新中断且自动代码回滚未完成。数据目录未覆盖；请按 migration/update-in-progress.json 的备份恢复代码。') from rollback
                raise PortabilityError(f'更新失败，已回滚已替换代码；数据目录未覆盖：{type(exc).__name__}') from exc
            marker.unlink(missing_ok=True)
    return {'status': 'updated', 'version': incoming['version'], 'files': len(targets),
            'backup': prior['path'], 'data_preserved': True, 'restart': 'start.cmd'}


def restore_backup(package, destination):
    destination = Path(destination)
    if not destination.is_absolute():
        raise PortabilityError('--destination 必须是绝对路径。')
    if _linked(destination) or (destination.exists() and any(destination.iterdir())):
        raise PortabilityError('完整备份只能恢复到新的空文件夹，不能覆盖已有数据。')
    # Validate all entries before creating anything in the destination.
    manifest = _validate_archive(package, 'full-backup')
    destination.mkdir(parents=True, exist_ok=True)
    _validate_archive(package, 'full-backup', destination)
    return {'status': 'restored', 'destination': str(destination.resolve()),
            'version': manifest['project']['version'], 'files': len(manifest['files'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description='沪研平台：一致备份、完整恢复、仅代码更新')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('backup', 'export-update'):
        sub = commands.add_parser(name)
        sub.add_argument('--output', required=True, help='新 ZIP 文件的绝对路径')
    for name in ('apply-update', 'verify', 'restore'):
        sub = commands.add_parser(name)
        sub.add_argument('--package', required=True)
        if name == 'restore':
            sub.add_argument('--destination', required=True)
        if name == 'apply-update':
            sub.add_argument('--port', type=int, default=8765, help='本地平台端口（若启动时自定义，必须对应）')
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = backup(ROOT, args.output)
        elif args.command == 'export-update':
            result = export_update(ROOT, args.output)
        elif args.command == 'apply-update':
            result = apply_update(ROOT, Path(args.package).resolve(), (args.port, 18767))
        elif args.command == 'restore':
            result = restore_backup(Path(args.package).resolve(), args.destination)
        else:
            manifest = _validate_archive(Path(args.package).resolve())
            result = {'status': 'verified', 'kind': manifest['kind'], 'version': manifest['project']['version'], 'files': len(manifest['files']), 'sha256': _file_hash(args.package)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PortabilityError, sqlite3.Error, OSError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
