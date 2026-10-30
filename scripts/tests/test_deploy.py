import importlib.util
import contextlib
import hashlib
import io
import os
import json
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('deploy', ROOT / 'scripts/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.media, self.target_data, self.target_media = [self.root / name for name in ('data', 'media', 'new-data', 'new-media')]
        for p in (self.data, self.media, self.target_data, self.target_media):
            p.mkdir()
        (self.data / 'bt.sqlite3').write_bytes(b'example database bytes')
        (self.data / '.secret').write_text('signing-secret')
        (self.media / 'video.mp4').write_bytes(b'video')
        self.config = self.root / '.env'
        self.config.write_text('BT_PASSWORD=original\nBT_PORT=7777\n')

    def test_full_backup_roundtrip_and_configuration_preserved(self):
        archive = deploy.backup(self.root / 'backups', self.data, self.media, self.config, True)
        deploy.restore(archive, self.target_data, self.target_media, self.config)
        self.assertEqual((self.target_data / 'bt.sqlite3').read_bytes(), b'example database bytes')
        self.assertEqual((self.target_data / '.secret').read_text(), 'signing-secret')
        self.assertEqual((self.target_media / 'video.mp4').read_bytes(), b'video')
        self.assertEqual(self.config.read_text(), 'BT_PASSWORD=original\nBT_PORT=7777\n')
        self.assertTrue(Path(str(archive) + '.configuration.env').is_file())

    def test_data_only_backup(self):
        archive = deploy.backup(self.root / 'backups', self.data, self.media, self.config)
        deploy.restore(archive, self.target_data, self.target_media, self.config)
        self.assertFalse(list(self.target_media.iterdir()))

    def test_restore_refuses_overwrite(self):
        archive = deploy.backup(self.root / 'backups', self.data, self.media, self.config)
        (self.target_data / 'keep').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'empty'):
            deploy.restore(archive, self.target_data, self.target_media, self.config)
        self.assertEqual((self.target_data / 'keep').read_text(), 'keep')

    def test_restore_rejects_unsafe_paths_and_links_before_writing(self):
        for name, kind in [('../escaped', tarfile.REGTYPE), ('data/link', tarfile.SYMTYPE), ('/escaped', tarfile.REGTYPE)]:
            archive = self.root / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as f:
                safe = tarfile.TarInfo('data/first')
                safe.size = 4
                f.addfile(safe, io.BytesIO(b'safe'))
                bad = tarfile.TarInfo(name)
                bad.type = kind
                bad.linkname = '/tmp'
                f.addfile(bad)
            with self.assertRaises(ValueError):
                deploy.restore(archive, self.target_data, self.target_media, self.config)
            self.assertFalse(list(self.target_data.iterdir()))

    def test_corrupt_archive_leaves_destination_empty(self):
        archive = self.root / 'broken.tar.gz'
        archive.write_bytes(b'broken')
        with self.assertRaises(tarfile.TarError):
            deploy.restore(archive, self.target_data, self.target_media, self.config)
        self.assertFalse(list(self.target_data.iterdir()))

    def test_local_pin_reset_only_changes_the_selected_profile(self):
        database = self.root / 'bt.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE profile (id INTEGER PRIMARY KEY, name TEXT)')
            connection.execute('CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT)')
            connection.execute("INSERT INTO profile VALUES (1, 'Family')")
            connection.execute("INSERT INTO profile VALUES (2, 'Parents')")
            connection.execute("INSERT INTO setting VALUES ('profile_pin_1', 'first-hash')")
            connection.execute("INSERT INTO setting VALUES ('profile_pin_2', 'second-hash')")
        deploy.profile_pin(self.root, 2)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute('SELECT key FROM setting').fetchall(), [('profile_pin_1',)])
        with self.assertRaisesRegex(ValueError, 'No profile'):
            deploy.profile_pin(self.root, 999)

    def test_same_storage_refused(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            deploy.check(self.data, self.data)

    def test_parent_pin_recovery_preserves_child_protection(self):
        database = self.root / 'bt.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE profile (id INTEGER PRIMARY KEY, name TEXT, role TEXT)')
            connection.execute('CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT)')
            connection.execute("INSERT INTO profile VALUES (1, 'Parent', 'admin'), (2, 'Child', 'child')")
            connection.execute("INSERT INTO setting VALUES ('profile_pin_1', 'old-hash')")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            deploy.profile_pin(self.root, 1)
        pin = re.search(r'PIN reset to: ([0-9]{8})', output.getvalue()).group(1)
        with sqlite3.connect(database) as connection:
            record = json.loads(connection.execute("SELECT value FROM setting WHERE key='profile_pin_1'").fetchone()[0])
            self.assertEqual(record['hash'], hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(record['salt']), 260_000).hex())


@unittest.skipUnless(shutil.which('bash') and os.name != 'nt', 'Bash platform checks')
class ShellSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bt-setup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('scripts', 'bin'):
            (self.root / name).mkdir()
        for name in ('setup.sh', 'manage.sh'):
            shutil.copy(ROOT / name, self.root / name)
        shutil.copy(ROOT / 'scripts/common.sh', self.root / 'scripts/common.sh')
        shutil.copy(ROOT / 'scripts/deploy.py', self.root / 'scripts/deploy.py')
        fake = self.root / 'bin/docker'
        fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$BT_TEST_LOG"
case "$*" in
  'compose up --help') echo '--wait-timeout' ;;
  'compose port bt-api 6645') echo '0.0.0.0:7777' ;;
  'compose up -d --wait --wait-timeout 240') exit "${BT_TEST_START_EXIT:-0}" ;;
  'compose run '*) cat >/dev/null ;;
esac
exit 0
''')
        fake.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'], 'BT_NO_BROWSER': '1', 'BT_TEST_LOG': str(self.root / 'docker.log')}

    def run_setup(self, inputs='', **env):
        return subprocess.run(['bash', str(self.root / 'setup.sh')], input=inputs, text=True, capture_output=True, env={**self.env, **env})

    def test_first_run_preserves_special_characters_and_checks_readiness(self):
        password = "my $pass#word's"
        result = self.run_setup(f"./app data\n./video $folder\nwrong\n7777\ny\n{password}\n{password}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('http://localhost:7777', result.stdout)
        config = (self.root / '.env').read_text()
        self.assertIn('BT_PASSWORD="my $$pass#word\'s"', config)
        self.assertIn('video $$folder', config)
        self.assertNotIn(password, result.stdout)
        self.assertIn('compose up -d --wait --wait-timeout 240', (self.root / 'docker.log').read_text())
        rerun = self.run_setup()
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(config, (self.root / '.env').read_text())

    def test_shared_login_is_optional_and_off_by_default(self):
        result = self.run_setup('\n\n\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BT_PASSWORD=""', (self.root / '.env').read_text())
        self.assertNotIn('generated password', result.stdout)

    def test_startup_failure_does_not_claim_ready(self):
        (self.root / '.env').write_text('BT_PORT=7777\n')
        result = self.run_setup(BT_TEST_START_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('is ready', result.stdout)
        self.assertIn('port', result.stderr)


if __name__ == '__main__':
    unittest.main()
