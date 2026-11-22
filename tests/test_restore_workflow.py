"""Exercise Ansible's target selection and restore guards with a local fake PBM.

These tests never connect to the VMs or restore a real database.
"""
import grp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible is required')
class RestoreWorkflowTest(unittest.TestCase):
    def run_restore(self, *, confirm='target', database='myapp', actual_pitr=False,
                    playbook='restore', backup_target=None, fail_step='', verify=False,
                    list_only=False, check_mode=False, standalone=False,
                    cleanup=False, unreachable_target=False):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            storage = {'endpointUrl': 'https://s3.example.invalid', 'region': 'us-east-1',
                       'bucket': 'old-storage', 'prefix': 'pbm', 'forcePathStyle': True,
                       'credentials': {'access-key-id': 'test', 'secret-access-key': 'test'}}
            config = {'storage': {'type': 's3', 's3': storage},
                      'pitr': {'enabled': actual_pitr, 'oplogSpanMin': 1, 'compression': 's2'}}
            (home / 'pbm.yml').write_text(yaml.safe_dump(config))
            (home / 'fail-step').write_text(fail_step)
            program = home / 'drctl'
            program.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''\
                import json, sys, yaml
                from pathlib import Path
                home = Path(__file__).parent
                with (home / 'commands.jsonl').open('a') as log:
                    log.write(json.dumps(sys.argv[1:]) + '\\n')
                args = sys.argv[1:]
                failing = (home / 'fail-step').read_text()
                if ((args[:2] == ['pbm', 'restore'] and failing == 'restore') or
                    ('--force-resync' in args and failing == 'resync') or
                    ('verify' in args and failing == 'verification')):
                    print('secret-diagnostic-not-for-report', file=sys.stderr)
                    sys.exit(1)
                if args[:3] == ['pbm', 'config', '--list']:
                    print((home / 'pbm.yml').read_text())
                elif args[:2] == ['pbm', 'restore']:
                    config = yaml.safe_load((home / 'pbm.yml').read_text())
                    print('Fake restore completed; bucket=' + config['storage']['s3']['bucket'])
                elif 'verify' in args:
                    print(json.dumps({'valid': True, 'after_target_absent': True}))
                '''))
            program.chmod(0o755)
            variables = {
                'ansible_become': False, 'ansible_remote_tmp': str(home / 'remote-tmp'),
                'ansible_async_dir': str(home / 'async'),
                'backup_archives': {'test': {'endpoint': storage['endpointUrl'], 'region': 'us-east-1',
                                             'bucket': 'test', 'prefix': 'pbm', 'access_key': 'test',
                                             'secret_key': 'test'}},
                'pbm_home': str(home), 'pbm_user': grp.getgrgid(os.getgid()).gr_name,
                'pbm_pitr_enabled': False, 'pbm_schedule_enabled': False,
                'pbm_s3_endpoint': storage['endpointUrl'], 'pbm_s3_bucket': 'test',
                'pbm_s3_access': 'test', 'pbm_s3_secret': 'test',
            }
            inventory = {'all': {'vars': variables, 'children': {'pbm_agents': {'hosts': {
                'target': {'ansible_connection': 'local', 'ansible_python_interpreter': sys.executable},
                'unreachable-source': {'ansible_host': '192.0.2.200'},
            }}}}}
            if unreachable_target:
                ssh = home / 'fake-ssh'
                ssh.write_text('#!/bin/sh\nexit 255\n')
                ssh.chmod(0o755)
                inventory['all']['children']['pbm_agents']['hosts']['target'].update({
                    'ansible_connection': 'ssh', 'ansible_ssh_executable': str(ssh),
                })
            inv = home / 'hosts.yml'
            inv.write_text(yaml.safe_dump(inventory))
            extra = {'restore_target': 'target', 'restore_confirm': confirm, 'restore_archive': 'test',
                     'restore_database': database, 'restore_time': '2026-11-20T12:00:00',
                     'restore_report_path': str(home / 'report.json')}
            if backup_target is not None:
                extra['backup_target'] = backup_target
            if verify:
                manifest = home / 'experiment.json'
                manifest.write_text(json.dumps({'database': database, 'cycles': [
                    {'target_utc': extra['restore_time'], 'expected': {}}]}))
                extra['restore_manifest'] = str(manifest)
            if list_only:
                extra['restore_list_only'] = True
                (home / 'report.json').write_text('{"previous_result": true}')
                (home / 'report.prom').write_text('previous_metric 1\n')
            if fail_step == 'preparation':
                prepare = home / 'prepare.yml'
                prepare.write_text(yaml.safe_dump([{'ansible.builtin.fail': {'msg': 'Preparation failed'}}]))
                extra['pbm_restore_prepare_tasks'] = str(prepare)
            if cleanup:
                tasks = [{'ansible.builtin.copy': {'dest': str(home / 'cleanup-ran'), 'content': 'yes'}}]
                if fail_step == 'cleanup':
                    tasks.append({'ansible.builtin.fail': {'msg': 'Cleanup failed'}})
                cleanup_file = home / 'cleanup.yml'
                cleanup_file.write_text(yaml.safe_dump(tasks))
                extra['pbm_restore_cleanup_tasks'] = str(cleanup_file)
            env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(home / 'ansible-tmp'),
                       ANSIBLE_NOCOLOR='1', ANSIBLE_FORCE_COLOR='0')
            selected = f'playbooks/{playbook}.yml'
            if standalone:
                roles = home / 'standalone-roles'
                shutil.copytree(ROOT / 'playbooks/roles/pbm', roles / 'pbm')
                selected = str(home / 'standalone.yml')
                Path(selected).write_text(yaml.safe_dump([{
                    'hosts': 'target', 'gather_facts': False, 'tasks': [{
                        'ansible.builtin.include_role': {'name': 'pbm', 'tasks_from': 'restore'},
                        'vars': {'pbm_restore_confirm': 'target', 'pbm_restore_namespace': 'myapp.*',
                                 'pbm_restore_point': extra['restore_time'],
                                 'pbm_restore_report_path': str(home / 'report.json')},
                    }],
                }]))
                env['ANSIBLE_ROLES_PATH'] = str(roles)
            command = ['ansible-playbook', '-i', str(inv), selected, '-e', json.dumps(extra)]
            if check_mode:
                command.append('--check')
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    text=True, capture_output=True, timeout=90)
            log = home / 'commands.jsonl'
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            report = json.loads((home / 'report.json').read_text()) if (home / 'report.json').exists() else None
            metrics = (home / 'report.prom').read_text() if (home / 'report.prom').exists() else ''
            if cleanup:
                self.assertTrue((home / 'cleanup-ran').exists(), result.stdout + result.stderr)
            return result, commands, report, metrics

    def test_explicit_restore_uses_archive_and_target_without_source_connection(self):
        result, commands, report, metrics = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        restore = [args for args in commands if args[:2] == ['pbm', 'restore']]
        self.assertEqual(restore, [['pbm', 'restore', '--yes', '--wait', '--ns', 'myapp.*',
                                    '--time', '2026-11-20T12:00:00']])
        self.assertEqual(report['target'], 'target')
        self.assertIn('bucket=test', report['restore_output'])
        self.assertIsNone(report['verification'])
        self.assertTrue(report['success'])
        self.assertTrue(report['restore_success'])
        self.assertEqual(report['verification_status'], 'not_requested')
        self.assertIn('mongo_dr_restore_success{target="target"} 1', metrics)
        self.assertNotIn('mongo_dr_drill_', metrics)

    def test_wrong_confirmation_prevents_all_database_commands(self):
        result, commands, _, _ = self.run_restore(confirm='another-host')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])

    def test_backup_rejects_host_pattern_before_the_operation_play(self):
        result, commands, _, _ = self.run_restore(playbook='backup', backup_target='target:target')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])

    def test_system_database_cannot_be_restored(self):
        result, commands, report, _ = self.run_restore(database='admin')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])
        self.assertEqual(report['failed_stage'], 'validation')

    def test_actual_active_pitr_is_rejected_even_if_inventory_says_disabled(self):
        result, commands, report, _ = self.run_restore(actual_pitr=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(['pbm', 'config', '--list'], commands)
        self.assertFalse(any(args[:2] == ['pbm', 'restore'] for args in commands))
        self.assertFalse(any('--file' in args for args in commands))
        self.assertEqual(report['failed_stage'], 'validation')

    def test_restore_failure_persists_report_and_metrics_without_raw_errors(self):
        result, _, report, metrics = self.run_restore(fail_step='restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report['success'])
        self.assertFalse(report['restore_success'])
        self.assertEqual(report['failed_stage'], 'restore')
        self.assertFalse(report['running'])
        self.assertIn('mongo_dr_restore_success{target="target"} 0', metrics)
        self.assertNotIn('secret-diagnostic-not-for-report', json.dumps(report))

    def test_catalogue_failure_does_not_run_restore(self):
        result, commands, report, _ = self.run_restore(fail_step='resync')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report['failed_stage'], 'resync')
        self.assertFalse(any(args[:2] == ['pbm', 'restore'] for args in commands))

    def test_failed_verification_is_distinct_from_successful_database_restore(self):
        result, _, report, metrics = self.run_restore(verify=True, fail_step='verification')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(report['restore_success'])
        self.assertFalse(report['success'])
        self.assertEqual(report['failed_stage'], 'verification')
        self.assertEqual(report['verification_status'], 'failed')
        self.assertIn('mongo_dr_restore_verification_success{target="target"} 0', metrics)

    def test_verified_restore_records_both_successes(self):
        result, _, report, metrics = self.run_restore(verify=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report['success'])
        self.assertTrue(report['restore_success'])
        self.assertEqual(report['verification_status'], 'passed')
        self.assertTrue(report['verification']['valid'])
        self.assertIn('mongo_dr_restore_verification_success{target="target"} 1', metrics)

    def test_catalogue_view_preserves_previous_restore_result(self):
        result, commands, report, metrics = self.run_restore(list_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report, {'previous_result': True})
        self.assertEqual(metrics, 'previous_metric 1\n')
        self.assertFalse(any(args[:2] == ['pbm', 'restore'] for args in commands))

    def test_check_mode_does_not_publish_restore_success(self):
        result, commands, report, metrics = self.run_restore(check_mode=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(commands, [])
        self.assertIsNone(report)
        self.assertEqual(metrics, '')

    def test_copied_pbm_role_reports_failure_without_laboratory_playbook(self):
        result, _, report, metrics = self.run_restore(standalone=True, fail_step='restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNotNone(report, result.stdout + result.stderr)
        self.assertEqual(report['failed_stage'], 'restore')
        self.assertIn('mongo_dr_restore_success{target="target"} 0', metrics)

    def test_preparation_failure_is_reported_before_restore(self):
        result, commands, report, _ = self.run_restore(fail_step='preparation')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report['failed_stage'], 'preparation')
        self.assertFalse(any(args[:2] == ['pbm', 'restore'] for args in commands))

    def test_cleanup_runs_after_failed_restore_and_preserves_original_error(self):
        result, _, report, _ = self.run_restore(fail_step='restore', cleanup=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report['failed_stage'], 'restore')
        self.assertFalse(report['cleanup_failed'])

    def test_cleanup_failure_preserves_successful_database_restore(self):
        result, _, report, _ = self.run_restore(fail_step='cleanup', cleanup=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report['failed_stage'], 'cleanup')
        self.assertTrue(report['restore_success'])
        self.assertFalse(report['success'])
        self.assertTrue(report['cleanup_failed'])

    def test_connection_failure_is_visible_without_access_to_target(self):
        result, commands, report, metrics = self.run_restore(unreachable_target=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])
        self.assertIsNotNone(report, result.stdout + result.stderr)
        # Ansible versions may classify a transport error as unreachable or failed.
        # Both a pending attempt and a completed failure must remain observable.
        self.assertIsNot(report['success'], True)
        if report['running']:
            self.assertIn('mongo_dr_restore_running{target="target"} 1', metrics)
        else:
            self.assertFalse(report['success'])
            self.assertIn('mongo_dr_restore_success{target="target"} 0', metrics)


if __name__ == '__main__':
    unittest.main()
