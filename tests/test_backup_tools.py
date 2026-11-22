"""Failure handling and secret execution boundaries, using only temporary files."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / 'playbooks/roles/pbm/files'


class BackupToolsTest(unittest.TestCase):
    def test_uri_is_passed_literally_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            shutil.copy(TOOLS / 'drctl', home / 'drctl')
            marker = home / 'should-not-exist'
            uri = f'mongodb://user:$(touch {marker})`touch {marker}`@localhost/'
            (home / 'pbm.env').write_text('PBM_MONGODB_URI=' + json.dumps(uri) + '\n')
            result = subprocess.run([sys.executable, str(home / 'drctl'), sys.executable,
                                     '-c', 'import os; print(os.environ["PBM_MONGODB_URI"])'],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), uri)
            self.assertFalse(marker.exists())

    def run_backup(self, failing_step):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            shutil.copy(TOOLS / 'backup.py', home / 'backup.py')
            (home / 'state').mkdir()
            fake = home / 'pbm'
            fake.write_text('#!/bin/sh\n[ "$1" != "$FAIL_STEP" ]\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=str(home) + os.pathsep + os.environ['PATH'],
                       FAIL_STEP=failing_step)
            operation = subprocess.run([sys.executable, str(home / 'backup.py'),
                                        '--retention-days', '7'], env=env, capture_output=True)
            return operation.returncode, json.loads((home / 'state/backup.json').read_text())

    def test_backup_failure_is_persisted(self):
        rc, result = self.run_backup('backup')
        self.assertEqual(rc, 1)
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'backup')
        self.assertFalse(result['running'])
        self.assertNotIn('cleanup_success', result)

    def test_cleanup_failure_preserves_successful_backup_information(self):
        rc, result = self.run_backup('cleanup')
        self.assertEqual(rc, 1)
        self.assertTrue(result['backup_success'])
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_step'], 'cleanup')
        self.assertGreater(result['last_success_timestamp'], 0)

    def test_success_requires_backup_and_cleanup(self):
        rc, result = self.run_backup('neither')
        self.assertEqual(rc, 0)
        self.assertTrue(result['success'])
        self.assertTrue(result['cleanup_success'])


if __name__ == '__main__':
    unittest.main()
