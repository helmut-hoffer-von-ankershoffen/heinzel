"""Read-only checks binding installed runtime files to independently accepted wheel bytes."""
import hashlib
import json
import os
import re
import stat
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

_MAX_TREE_BYTES = 4 * 1024 * 1024 * 1024


def digest_file(path, expected):
    if (not re.fullmatch(r'[0-9a-f]{64}', expected) or path.is_symlink() or not path.is_file()
            or path.stat().st_size > 256 * 1024 * 1024):
        raise RuntimeError('Artifact identity is unavailable')
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != expected:
        raise RuntimeError('Artifact digest does not match the reviewed input')


def wheel_version(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata = [i for i in archive.infolist() if re.fullmatch(r'heinzel-[^/]+\.dist-info/METADATA', i.filename)]
        if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
            raise RuntimeError('Wheel metadata is unavailable')
        message = BytesParser().parsebytes(archive.read(metadata[0]))
        if message.get('Name', '').lower() != 'heinzel' or not message.get('Version'):
            raise RuntimeError('Wheel metadata is invalid')
        return str(message['Version'])


def verify_candidate_tree(prefix, manifest, accepted_sha256, proof_root, external_interpreter):
    """Compare every regular file and symlink to an independently accepted frozen tree manifest."""
    root = proof_root.resolve(strict=True)
    base = prefix.resolve(strict=True)
    if prefix.is_symlink() or not base.is_relative_to(root) or not manifest.resolve(strict=True).is_relative_to(root):
        raise RuntimeError('Candidate provenance is outside the isolated proof')
    if manifest.resolve(strict=True).is_relative_to(base):
        raise RuntimeError('Candidate tree manifest must be outside the candidate prefix')
    if manifest.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError('Candidate tree manifest is too large')
    digest_file(manifest, accepted_sha256)
    expected = json.loads(manifest.read_bytes())
    if not isinstance(expected, dict) or len(expected) > 200000:
        raise RuntimeError('Candidate tree manifest is invalid')
    actual = {}
    total_bytes = 0
    def walk_error(error):
        raise RuntimeError('Candidate tree cannot be inspected') from error
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in sorted(dirs + files):
            path = parent/name
            relative = path.relative_to(base).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise RuntimeError('Candidate symlink cannot be verified') from error
                if not resolved.is_relative_to(base) and resolved != external_interpreter:
                    raise RuntimeError('Candidate symlink points outside the verified environment')
                actual[relative] = {'symlink': os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                if info.st_size > 256 * 1024 * 1024:
                    raise RuntimeError('Candidate file is too large')
                total_bytes += info.st_size
                if total_bytes > _MAX_TREE_BYTES:
                    raise RuntimeError('Candidate tree contents are too large')
                hasher = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        hasher.update(block)
                actual[relative] = {'sha256': hasher.hexdigest(), 'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}
            elif stat.S_ISDIR(info.st_mode):
                actual[relative] = {'directory': True, 'mode': stat.S_IMODE(info.st_mode)}
            else:
                raise RuntimeError('Candidate tree contains a special file')
            if len(actual) > 200000:
                raise RuntimeError('Candidate tree is too large')
    if actual != expected:
        raise RuntimeError('Candidate environment differs from its independently reviewed tree')


def check_runtime(wheel, prefix, proof_root):
    root = proof_root.resolve(strict=True)
    target = prefix.resolve(strict=True)
    if prefix.is_symlink() or not target.is_relative_to(root):
        raise RuntimeError('Installed prefix is outside the isolated proof')
    sites = list((target / 'lib').glob('python*/site-packages'))
    if len(sites) != 1 or sites[0].is_symlink() or not sites[0].resolve(strict=True).is_relative_to(target):
        raise RuntimeError('Installed package directory is unavailable')
    metadata = list(sites[0].glob('heinzel-*.dist-info/METADATA'))
    if (len(metadata) != 1 or metadata[0].is_symlink()
            or not metadata[0].resolve(strict=True).is_relative_to(target)
            or metadata[0].stat().st_size > 1024 * 1024):
        raise RuntimeError('Installed distribution metadata is unavailable')
    installed_metadata = BytesParser().parsebytes(metadata[0].read_bytes())
    if installed_metadata.get('Name', '').lower() != 'heinzel' or installed_metadata.get('Version') != wheel_version(wheel):
        raise RuntimeError('Installed distribution metadata does not match the accepted wheel')
    seen = set()
    total = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir() or not (name.startswith('heinzel/') or name == '_heinzel_launcher.py'):
                continue
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or name in seen or stat.S_ISLNK(mode)
                    or info.file_size > 128 * 1024 * 1024):
                raise RuntimeError('Wheel runtime layout is unsafe')
            seen.add(name)
            total += info.file_size
            if total > 256 * 1024 * 1024:
                raise RuntimeError('Wheel runtime is too large')
            installed = sites[0].joinpath(*path.parts)
            if (installed.is_symlink() or not installed.is_file()
                    or not installed.resolve(strict=True).is_relative_to(target)
                    or installed.stat().st_size != info.file_size
                    or installed.read_bytes() != archive.read(info)):
                raise RuntimeError('Installed runtime does not match the accepted wheel')
    if '_heinzel_launcher.py' not in seen or 'heinzel/__init__.py' not in seen:
        raise RuntimeError('Accepted wheel lacks the required runtime')
    return len(seen)
