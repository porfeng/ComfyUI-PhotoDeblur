"""Verified official checkpoint downloads. No model loading or pip installs."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path, PurePosixPath
import time
import uuid
import zipfile


def catalog():
    return json.loads(Path(__file__).with_name('download_catalog.json').read_text(encoding='utf-8'))


def file_hash(path, interrupt=lambda: None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(4 * 1024**2):
            interrupt()
            digest.update(chunk)
    return digest.hexdigest()


def matches(path, entry, interrupt=lambda: None):
    path = Path(path)
    return (path.is_file() and path.stat().st_size == entry['bytes']
            and file_hash(path, interrupt) == entry['sha256'])


@contextmanager
def download_lock(cache):
    """OS-released lock, including after process exit; no stale lock deletion."""
    with (cache / 'download.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another PhotoDeblur download is running. Queue this node again when it finishes.') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def fetch_drive(source, destination, progress, interrupt):
    try:
        import gdown
    except ImportError as exc:
        raise RuntimeError('Model downloading needs gdown in ComfyUI Python. Install requirements-download.txt '
                           'from this node folder. Existing complete weights do not need gdown.') from exc
    if 'progress' not in inspect.signature(gdown.download).parameters:
        raise RuntimeError('Please install requirements-download.txt to update gdown for progress and cancellation support.')

    def update(done, total):
        interrupt()
        if done > source['bytes']:
            raise ValueError('The download is larger than the verified official archive. Refusing it.')
        progress('downloading', done, source['bytes'])

    interrupt()
    logging.info('PhotoDeblur: downloading %s (%d bytes) from %s', source['name'], source['bytes'], source['url'])
    result = gdown.download(id=source['drive_id'], output=str(destination), quiet=True,
                            use_cookies=False, resume=True, progress=update,
                            timeout=(15, 20), retries=2)
    if result is None:
        raise RuntimeError('Google Drive did not return a downloadable file.')
    interrupt()


def ensure_weights(family, variant, models_dir, repair_invalid=False, *,
                   progress=lambda phase, done, total: None, interrupt=lambda: None,
                   fetch=fetch_drive, specification=None):
    """Download only when required; verify every member before publishing any."""
    spec = specification if specification is not None else catalog()
    entry = spec['models'][family][variant]
    source = spec['archives'][entry['archive']]
    root = Path(models_dir).resolve()
    folder = root / entry['directory']
    # Directory and filenames come from the bundled catalog, never ZIP paths.
    if not folder.resolve().is_relative_to(root):
        raise ValueError('Model destination must stay inside the configured models directory.')
    for member in entry['files']:
        if Path(member['name']).name != member['name'] or '\\' in member['name']:
            raise ValueError('Invalid filename in download catalog.')
    files = {member['name']: folder / member['name'] for member in entry['files']}
    cache = root / '.photo_deblur_downloads'
    if not cache.resolve().is_relative_to(root):
        raise ValueError('Download cache must stay inside the configured models directory.')
    cache.mkdir(parents=True, exist_ok=True)
    with download_lock(cache):
        pending, invalid = [], []
        total_files = sum(m['bytes'] for m in entry['files'])
        checked = 0
        for member in entry['files']:
            interrupt()
            path = files[member['name']]
            if path.exists() and not path.is_file():
                raise RuntimeError(f'Expected a checkpoint file, found a directory or special file: {path}')
            if not matches(path, member, interrupt):
                pending.append(member)
                if path.exists():
                    invalid.append(path)
            checked += member['bytes']
            progress('checking', checked, total_files)
        if not pending:
            status = f'{family} / {variant}: verified existing weights, skipped download.'
            logging.info('PhotoDeblur: %s', status)
            progress('complete', 1, 1)
            return {'family': family, 'variant': variant, 'files': files,
                    'model_dir': str(folder), 'status': status}
        if invalid and not repair_invalid:
            raise RuntimeError('Existing weights do not match the official SHA256: '
                               + ', '.join(str(p) for p in invalid)
                               + '. Move them aside, or enable repair_invalid to back them up before replacing.')

        archive = cache / (source['sha256'] + '.zip')
        archive_cached = matches(archive, source, interrupt)
        if not archive_cached:
            partial = archive.with_suffix('.zip.partial')
            try:
                fetch(source, partial, progress, interrupt)
            except Exception as exc:
                raise RuntimeError(f'Download failed for {family}/{variant}. Existing model files were kept. '
                                   f'Check network/Drive quota, or download manually from {source["url"]}. '
                                   f'Detail: {exc}') from exc
            if not matches(partial, source, interrupt):
                if partial.is_file():
                    partial.unlink()
                raise RuntimeError('Official archive size/SHA256 mismatch. No weights installed; queue again to retry.')
            partial.replace(archive)

        stage = cache / ('stage_' + uuid.uuid4().hex)
        stage.mkdir()
        staged = []
        try:
            with zipfile.ZipFile(archive) as zipped:
                for index, member in enumerate(pending):
                    interrupt()
                    candidates = [item for item in zipped.infolist() if not item.is_dir()
                                  and PurePosixPath(item.filename.replace('\\', '/')).name == member['name']]
                    if len(candidates) != 1:
                        raise RuntimeError(f'Archive must contain exactly one {member["name"]}.')
                    item = candidates[0]
                    if item.file_size != member['bytes']:
                        raise RuntimeError(f'Unexpected uncompressed size for {member["name"]}.')
                    target = stage / member['name']
                    with zipped.open(item) as src, target.open('wb') as dst:
                        while chunk := src.read(1024**2):
                            interrupt()
                            dst.write(chunk)
                    staged.append(target)
                    if not matches(target, member, interrupt):
                        raise RuntimeError(f'Checkpoint SHA256 mismatch: {member["name"]}. No files installed.')
                    progress('extracting', index + 1, len(pending))
            # All pending files verified before the first destination changes.
            interrupt()
            folder.mkdir(parents=True, exist_ok=True)
            backups = []
            for target in staged:
                destination = files[target.name]
                if destination.exists():
                    backup = destination.with_name(destination.name + f'.invalid-{time.time_ns()}.bak')
                    destination.replace(backup)
                    backups.append(str(backup))
                target.replace(destination)
        finally:
            # Only delete known temporary files in our uniquely created stage.
            for target in stage.iterdir():
                if target.is_file():
                    target.unlink()
            stage.rmdir()
        count = len(pending)
        status = f'{family} / {variant}: installed {count} verified file(s).'
        if archive_cached:
            status += ' Reused cached official archive.'
        if backups:
            status += ' Previous invalid files backed up: ' + ', '.join(backups)
        logging.info('PhotoDeblur: %s', status)
        progress('complete', 1, 1)
        return {'family': family, 'variant': variant, 'files': files,
                'model_dir': str(folder), 'status': status}
