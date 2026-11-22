"""Restore completion, verification and retry signals without a running database."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1] / 'playbooks/roles/pbm/files/restore_report.py'
SPEC = importlib.util.spec_from_file_location('restore_report', TOOL)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class RestoreReportTest(unittest.TestCase):
    def test_failure_remains_visible_during_retry_and_clears_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'restore.json'
            data = {'target': 'dr-01', 'verification_status': 'not_requested',
                    'success': False, 'restore_success': None}
            REPORT.write_report(path, 'start', data)
            self.assertNotIn('mongo_dr_restore_success{', path.with_suffix('.prom').read_text())
            REPORT.write_report(path, 'finish', data)
            REPORT.write_report(path, 'start', data)
            self.assertIn('mongo_dr_restore_success{target="dr-01"} 0', path.with_suffix('.prom').read_text())
            result = REPORT.write_report(path, 'finish', dict(data, success=True, restore_success=True))
            self.assertGreater(result['last_success_timestamp'], 0)
            self.assertIn('mongo_dr_restore_success{target="dr-01"} 1', path.with_suffix('.prom').read_text())

    def test_unverified_restore_is_not_reported_as_verified_or_as_a_drill(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'restore.json'
            data = {'target': 'dr-01', 'verification_status': 'not_requested',
                    'success': True, 'restore_success': True}
            REPORT.write_report(path, 'start', data)
            result = REPORT.write_report(path, 'finish', data)
            metrics = path.with_suffix('.prom').read_text()
            self.assertIsNone(result.get('verification'))
            self.assertIn('mongo_dr_restore_verification_performed{target="dr-01"} 0', metrics)
            self.assertNotIn('mongo_dr_restore_verification_success{', metrics)
            self.assertNotIn('mongo_dr_drill_', metrics)
