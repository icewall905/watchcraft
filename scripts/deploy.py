"""Container-side deployment checks and portable backups. No extra host dependencies."""
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import sqlite3
import secrets
import tarfile
import tempfile


def private_file(path):
    path.chmod(0o600)
    if owner := os.environ.get('BT_BACKUP_OWNER'):
        uid, gid = map(int, owner.split(':'))
        os.chown(path, uid, gid)


def check(data=Path('/data'), media=Path('/media')):
    if data.samefile(media):
        raise ValueError('App data and videos need separate folders.')
    for folder in (data, media):
        with tempfile.TemporaryFile(dir=folder) as f:
            f.write(b'BetterTube storage check')
            f.flush()
    print('Storage folders are writable.')


def backup(destination, data, media, config, include_media=False):
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = destination / f'bettertube-{stamp}.tar.gz'
    partial = output.with_suffix('.partial')
    partial.touch(mode=0o600, exist_ok=False)
    roots = [(data, 'data'), (config, 'configuration.env')]
    if include_media:
        roots.append((media, 'media'))
    def regular(info):
        if not (info.isfile() or info.isdir()):
            raise ValueError(f'Backup cannot include links or special files: {info.name}')
        return info
    try:
        with tarfile.open(partial, 'w:gz') as archive:
            metadata = json.dumps({'format': 1, 'media': include_media}).encode()
            import io
            info = tarfile.TarInfo('backup.json')
            info.size = len(metadata)
            archive.addfile(info, io.BytesIO(metadata))
            for source, name in roots:
                archive.add(source, arcname=name, filter=regular)
        private_file(partial)
        partial.rename(output)
    finally:
        partial.unlink(missing_ok=True)
    print(f'Backup saved: {output.name}', flush=True)
    return output


def restore(source, data, media, config):
    # Refuse overwrites. Setup has not run the app yet on a new destination.
    if any(data.iterdir()) or any(media.iterdir()):
        raise ValueError('Restore needs empty app-data and video folders. Choose new empty folders in setup; your current data will not be overwritten.')
    # Read and validate every header and all compressed bytes before writing anything.
    with tarfile.open(source, 'r:gz') as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if member.name in names or path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Invalid backup path.')
            names.add(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError('Backup contains a link or special file.')
            if not path.parts or path.parts[0] not in {'data', 'media', 'backup.json', 'configuration.env'}:
                raise ValueError('Unrecognized backup contents.')
            if path.parts[0] in {'backup.json', 'configuration.env'} and (len(path.parts) != 1 or not member.isfile()):
                raise ValueError('Invalid backup metadata.')
            if member.isfile():
                with archive.extractfile(member) as f:
                    while f.read(1024 * 1024):
                        pass
        if not {'backup.json', 'configuration.env', 'data'}.issubset(names):
            raise ValueError('Not a BetterTube backup.')
        metadata = json.load(archive.extractfile('backup.json'))
        if metadata.get('format') != 1:
            raise ValueError('Unsupported backup version.')
        # The target .env stays in place: its storage paths and port belong to this machine.
        saved_config = source.parent / (source.name + '.configuration.env')
        if saved_config.exists():
            raise ValueError(f'Move the existing {saved_config.name} before restoring again.')
        for member in members:
            path = PurePosixPath(member.name)
            if path.parts[0] not in {'data', 'media'}:
                continue
            root = data if path.parts[0] == 'data' else media
            target = root.joinpath(*path.parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as incoming, target.open('xb') as outgoing:
                    import shutil
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(member.mode & 0o777)
        with saved_config.open('xb') as outgoing:
            outgoing.write(archive.extractfile('configuration.env').read())
        private_file(saved_config)
    print('Restored profiles, settings and history' + (' and videos.' if metadata.get('media') else '. Videos were not included in this backup.'))
    print(f'Old configuration saved beside the archive as {saved_config.name}. The destination password, paths and port remain in use.')


def profile_pin(data, pid=None):
    database = data / 'bt.sqlite3'
    if not database.is_file():
        raise ValueError('Start BetterTube once before managing profile PINs.')
    with sqlite3.connect(database, timeout=30) as connection:
        if pid is None:
            for ident, name in connection.execute('SELECT id, name FROM profile ORDER BY id'):
                print(f'{ident}: {name}')
        else:
            if not connection.execute('SELECT id FROM profile WHERE id = ?', (pid,)).fetchone():
                raise ValueError('No profile has that ID.')
            has_roles = any(row[1] == 'role' for row in connection.execute('PRAGMA table_info(profile)'))
            if has_roles and connection.execute("SELECT 1 FROM profile WHERE role = 'child' LIMIT 1").fetchone():
                role = connection.execute('SELECT role FROM profile WHERE id = ?', (pid,)).fetchone()[0]
                if role != 'child':
                    # Removing a parent's PIN would bypass child restrictions. A local
                    # operator can recover access with a new PIN instead; old sessions revoke.
                    pin = f'{secrets.randbelow(100_000_000):08d}'
                    salt = secrets.token_hex(16)
                    digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 260_000).hex()
                    value = json.dumps({'salt': salt, 'hash': digest, 'failures': 0, 'retry_at': 0})
                    connection.execute('INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                       (f'profile_pin_{pid}', value))
                    print(f'Profile PIN reset to: {pin}. Unlock the profile, then change it in Settings → Profiles.')
                    return
            connection.execute('DELETE FROM setting WHERE key = ?', (f'profile_pin_{pid}',))
            print('Profile PIN removed. Reopen the profile in BetterTube to continue.')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        action = sys.argv[1]
        data, media = Path('/data'), Path('/media')
        if action == 'check':
            check(data, media)
        elif action == 'backup':
            backup(Path('/backup'), data, media, Path('/config.env'), '--media' in sys.argv)
        elif action == 'restore':
            name = sys.argv[2]
            if Path(name).name != name:
                raise ValueError('Choose an archive filename inside the backups folder.')
            restore(Path('/backup') / name, data, media, Path('/config.env'))
        elif action == 'list-profiles':
            profile_pin(data)
        elif action == 'reset-pin':
            profile_pin(data, int(sys.argv[2]))
        else:
            raise ValueError('Unknown maintenance action.')
    except (OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
        sys.exit(f'BetterTube: {exc}')
