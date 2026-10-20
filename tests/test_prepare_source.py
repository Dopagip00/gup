import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import prepare_source


class SourceOnlyTests(unittest.TestCase):
    def test_source_only_syncs_observation_module_without_reading_db_or_rewriting_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);platform=root/'platform';platform.mkdir()
            (platform/'static').mkdir();(platform/'tests').mkdir()
            (platform/'daily_watchlist.py').write_text('PROTOCOL="daily-observation-v1"',encoding='utf-8')
            repo=root/'repo';repo.mkdir();seed=repo/'seed';seed.mkdir()
            sentinel=seed/'research.json.gz';sentinel.write_bytes(b'UNCHANGED_EXISTING_SEED')
            with patch.object(prepare_source,'HERE',repo),patch.object(prepare_source.sqlite3,'connect',side_effect=AssertionError('DB must not be read')):
                result=prepare_source.prepare(platform,source_only=True)
            self.assertTrue(result['source_only']);self.assertTrue(result['seed_unchanged'])
            self.assertEqual(sentinel.read_bytes(),b'UNCHANGED_EXISTING_SEED')
            self.assertEqual((repo/'engine/daily_watchlist.py').read_text(encoding='utf-8'),'PROTOCOL="daily-observation-v1"')


if __name__=='__main__':unittest.main()
