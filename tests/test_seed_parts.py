import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest

import prepare_source
import runner


class SeedPartsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.seed = self.root / 'research.json.gz'
        self.payload = {'schema_version': 1, 'tables': {}, 'kv': {'universe_info': {'total': 2320}}}
        self.seed.write_bytes(gzip.compress(json.dumps(self.payload).encode()))
        self.manifest_path, self.manifest = prepare_source.write_seed_parts(self.seed)

    def tearDown(self):
        self.temporary.cleanup()

    def save_manifest(self, manifest):
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    def test_multichunk_reassembly_preserves_every_compressed_byte_and_decompresses(self):
        raw = os.urandom(runner.SEED_PART_BYTES + 256)
        compressed = gzip.compress(raw)
        self.seed.write_bytes(compressed)
        _, manifest = prepare_source.write_seed_parts(self.seed)
        self.assertEqual(len(manifest['parts']), 2)
        self.assertEqual(manifest['parts'][0]['size'], runner.SEED_PART_BYTES)
        self.seed.unlink()
        result = io.BytesIO()
        runner.reassemble_seed(self.seed, result)
        self.assertEqual(result.getvalue(), compressed)
        with runner.open_seed_gzip(self.seed) as source:
            self.assertEqual(source.read(), raw)

    def test_split_restore_keeps_public_whitelist_and_rejects_private_keys(self):
        database = self.root / 'state.sqlite3'
        with contextlib.closing(sqlite3.connect(database)) as con, con:
            con.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')

        @contextlib.contextmanager
        def connect():
            with contextlib.closing(sqlite3.connect(database)) as con, con:
                yield con

        core = types.SimpleNamespace(connect=connect)
        self.seed.unlink()
        self.assertTrue(runner.restore_seed(self.seed, core, self.root))
        with connect() as con:
            self.assertEqual(json.loads(con.execute('SELECT value FROM kv').fetchone()[0]), {'total': 2320})
        self.payload['kv']['cash'] = 100000
        self.seed.write_bytes(gzip.compress(json.dumps(self.payload).encode()))
        prepare_source.write_seed_parts(self.seed)
        self.seed.unlink()
        with self.assertRaisesRegex(ValueError, '非研究键'):
            runner.restore_seed(self.seed, core, self.root)
        with connect() as con:
            self.assertIsNone(con.execute("SELECT value FROM kv WHERE key='cash'").fetchone())

    def test_modified_part_is_rejected_before_decompression(self):
        part = self.root / self.manifest['parts'][0]['name']
        content = bytearray(part.read_bytes())
        content[len(content) // 2] ^= 1
        part.write_bytes(content)
        self.seed.unlink()
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            with runner.open_seed_gzip(self.seed):
                self.fail('A modified part must never be decoded')

    def test_missing_part_is_rejected_instead_of_silently_using_empty_seed(self):
        (self.root / self.manifest['parts'][0]['name']).unlink()
        self.seed.unlink()
        with self.assertRaisesRegex(ValueError, '缺失'):
            with runner.open_seed_gzip(self.seed):
                self.fail('A missing part must not yield an empty seed')

    def test_manifest_rejects_traversal_duplicate_count_and_length_mismatch(self):
        self.seed.unlink()
        mutations = (
            lambda value: value['parts'][0].update(name='../research.part000.bin'),
            lambda value: value['parts'][0].update(name='C:\\research.part000.bin'),
            lambda value: value['parts'].append(dict(value['parts'][0])),
            lambda value: value.update(total_bytes=value['total_bytes'] + 1),
            lambda value: value['parts'][0].update(size=True),
            lambda value: value.update(sha256='0' * 64),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                manifest = json.loads(json.dumps(self.manifest))
                mutate(manifest)
                self.save_manifest(manifest)
                with self.assertRaises(ValueError):
                    runner.reassemble_seed(self.seed, io.BytesIO())


if __name__ == '__main__':
    unittest.main()
