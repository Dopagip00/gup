import hashlib
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portability as p


def project(root, version='2.0.0'):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'version.json').write_text(json.dumps({'version': version, 'data_schema_version': 1, 'project_id': p.PROJECT_ID}), encoding='utf-8')
    (root / 'app.py').write_text('VERSION = ' + repr(version) + '\n', encoding='utf-8')
    (root / 'data').mkdir()
    con = sqlite3.connect(root / 'data' / 'market.sqlite3')
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE lots(symbol TEXT,quantity INTEGER); CREATE TABLE signals(report_date TEXT);')
    con.execute('INSERT INTO lots VALUES (?,?)', ('sh600519', 100))
    con.execute('INSERT INTO kv VALUES (?,?)', ('cash', '99800'))
    con.execute('INSERT INTO kv VALUES (?,?)', ('schedule_status', '{"threadId":"old-machine"}'))
    con.execute('INSERT INTO kv VALUES (?,?)', ('csrf_token', 'local-only-secret'))
    con.execute('INSERT INTO signals VALUES (?)', ('2026-10-16',))
    con.commit()
    return con


def change_zip(source, destination, replacements=None, additions=None):
    replacements, additions = replacements or {}, additions or {}
    with zipfile.ZipFile(source) as reader, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as writer:
        for entry in reader.infolist():
            writer.writestr(entry, replacements.get(entry.filename, reader.read(entry.filename)))
        for name, payload in additions.items():
            writer.writestr(name, payload)


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.connection = project(self.root)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_backup_restores_committed_wal_and_omits_credentials(self):
        (self.root / 'data' / 'reports').mkdir()
        (self.root / 'data' / 'reports' / 'day.json').write_text('{"report":42}', encoding='utf-8')
        (self.root / '.env').write_text('PASSWORD=secret', encoding='utf-8')
        (self.root / 'Cookies').write_bytes(b'secret')
        package = self.base / 'full.zip'
        p.backup(self.root, package)
        with zipfile.ZipFile(package) as archive:
            self.assertNotIn('.env', archive.namelist())
            self.assertNotIn('Cookies', archive.namelist())
            self.assertFalse(any(x.endswith(('-wal', '-shm')) for x in archive.namelist()))
            handoff = archive.read('migration/session-handoff.md').decode('utf-8')
            self.assertIn('2026-10-16', handoff)
            schedules = json.loads(archive.read('migration/schedules.json'))
            self.assertEqual([x['time'] for x in schedules['tasks']], ['08:00', '09:00'])
        restored = self.base / 'new-computer'
        p.restore_backup(package, restored)
        with closing(sqlite3.connect(restored / 'data' / 'market.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT quantity FROM lots').fetchone()[0], 100)
            self.assertEqual(con.execute("SELECT value FROM kv WHERE key='cash'").fetchone()[0], '99800')
            self.assertEqual(con.execute("SELECT COUNT(*) FROM kv WHERE key IN ('csrf_token','schedule_status')").fetchone()[0], 0)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual((restored / 'data/reports/day.json').read_text(), '{"report":42}')
        # Snapshot cleanup does not modify the source application's KV.
        self.assertIsNotNone(self.connection.execute("SELECT value FROM kv WHERE key='csrf_token'").fetchone())

    def test_update_preserves_database_and_local_config_and_backs_up(self):
        incoming = self.base / 'incoming'
        other = project(incoming, '2.1.0')
        other.close()
        (incoming / 'config.json').write_text('{"setting":"developer"}')
        (self.root / 'config.json').write_text('{"setting":"user"}')
        (incoming / 'static').mkdir()
        (incoming / 'static' / 'app.js').write_text('updated', encoding='utf-8')
        package = self.base / 'update.zip'
        p.export_update(incoming, package)
        with zipfile.ZipFile(package) as archive:
            self.assertFalse(any(name.startswith('data/') for name in archive.namelist()))
            self.assertNotIn('config.json', archive.namelist())
        self.connection.execute('UPDATE lots SET quantity=321')
        self.connection.commit()
        result = p.apply_update(self.root, package, ports=())
        self.assertTrue(result['data_preserved'])
        self.assertEqual(self.connection.execute('SELECT quantity FROM lots').fetchone()[0], 321)
        self.assertIn('2.1.0', (self.root / 'app.py').read_text())
        self.assertEqual((self.root / 'config.json').read_text(), '{"setting":"user"}')
        self.assertTrue(Path(result['backup']).is_file())
        with zipfile.ZipFile(result['backup']) as archive:
            self.assertIn('2.0.0', archive.read('app.py').decode())

    def test_rejects_path_traversal_before_writing(self):
        package = self.base / 'update.zip'
        p.export_update(self.root, package)
        bad = self.base / 'bad.zip'
        change_zip(package, bad, additions={'../escaped.py': b'bad'})
        with self.assertRaises(p.PortabilityError):
            p.apply_update(self.root, bad, ports=())
        self.assertFalse((self.base / 'escaped.py').exists())
        self.assertFalse((self.root / 'backups').exists())

    def test_rejects_hash_corruption_without_changes(self):
        package = self.base / 'update.zip'
        p.export_update(self.root, package)
        bad = self.base / 'bad.zip'
        original = (self.root / 'app.py').read_bytes()
        change_zip(package, bad, replacements={'app.py': b'X' * len(original)})
        with self.assertRaisesRegex(p.PortabilityError, 'SHA256'):
            p.apply_update(self.root, bad, ports=())
        self.assertEqual((self.root / 'app.py').read_bytes(), original)

    def test_rejects_symlink_zip_and_data_in_update(self):
        package = self.base / 'update.zip'
        p.export_update(self.root, package)
        link_zip = self.base / 'symlink.zip'
        with zipfile.ZipFile(package) as src, zipfile.ZipFile(link_zip, 'w') as dst:
            for item in src.infolist():
                if item.filename == 'app.py':
                    item.create_system = 3
                    item.external_attr = (stat.S_IFLNK | 0o777) << 16
                dst.writestr(item, src.read(item.filename))
        with self.assertRaisesRegex(p.PortabilityError, '符号链接'):
            p.apply_update(self.root, link_zip, ports=())
        with zipfile.ZipFile(package) as src:
            manifest = json.loads(src.read(p.MANIFEST))
        payload = b'overwrite cash'
        manifest['files']['data/config.json'] = {'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}
        malicious = self.base / 'data-update.zip'
        change_zip(package, malicious, replacements={p.MANIFEST: json.dumps(manifest).encode()}, additions={'data/config.json': payload})
        with self.assertRaisesRegex(p.PortabilityError, '非代码'):
            p.apply_update(self.root, malicious, ports=())

    def test_running_process_blocks_update_without_killing(self):
        package = self.base / 'update.zip'
        p.export_update(self.root, package)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        try:
            with self.assertRaisesRegex(p.PortabilityError, 'stop.ps1'):
                p.apply_update(self.root, package, ports=(listener.getsockname()[1],))
            self.assertGreater(listener.fileno(), 0)
            self.assertFalse((self.root / 'backups').exists())
        finally:
            listener.close()

    def test_restore_cannot_overwrite_existing_directory(self):
        package = self.base / 'full.zip'
        p.backup(self.root, package)
        with self.assertRaisesRegex(p.PortabilityError, '空文件夹'):
            p.restore_backup(package, self.root)

    def test_schema_and_project_mismatch_rejected(self):
        for value in ({'version': '2.1.0', 'project_id': p.PROJECT_ID, 'data_schema_version': 2},
                      {'version': '2.1.0', 'project_id': 'unrelated', 'data_schema_version': 1}):
            with self.assertRaises(p.PortabilityError):
                p._version(value)

    def test_secret_json_rejected(self):
        (self.root / 'config.json').write_text('{"api_key":"private-value"}', encoding='utf-8')
        with self.assertRaisesRegex(p.PortabilityError, '凭据'):
            p.backup(self.root, self.base / 'full.zip')
        self.assertFalse((self.base / 'full.zip').exists())


if __name__ == '__main__':
    unittest.main()
