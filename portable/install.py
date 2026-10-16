#!/usr/bin/env python3
"""Offline allowlist builder/installer; no downloads or global writes."""
import argparse
import gzip
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

BASE = Path(__file__).resolve().parent.parent


def manifest():
    paths = json.loads((BASE / 'portable/files.json').read_text(encoding='utf-8'))
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate allowlist member')
    for name in paths:
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts):
            raise ValueError('unsafe allowlist member: ' + name)
        source = BASE / p
        if not source.is_file() or any(x.is_symlink() for x in [source, *source.parents]):
            raise ValueError('allowlist requires regular non-symlink files: ' + name)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='action', required=True)
    sub.add_parser('build').add_argument('--output', required=True, type=Path)
    sub.add_parser('install').add_argument('--prefix', required=True, type=Path)
    args = ap.parse_args()
    paths = manifest()
    if args.action == 'build':
        # Exclusive creation: never overwrite an existing archive.
        with args.output.open('xb') as raw:
            with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w') as archive:
                    for name in paths:
                        info = tarfile.TarInfo('agent-platform-minimal/' + name)
                        data = (BASE / name).read_bytes()
                        info.size = len(data)
                        info.mode = 0o644
                        archive.addfile(info, io.BytesIO(data))
        print(args.output.resolve())
    else:
        prefix = args.prefix.absolute()
        # mkdir is exclusive, including symlinks and existing empty directories.
        prefix.mkdir()
        for name in paths:
            target = prefix / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / name, target)
        (prefix / 'workspace/agents').mkdir(parents=True)
        shutil.copyfile(prefix / 'portable/example.json', prefix / 'config.json')
        print('Installed at ' + str(prefix))
        print('Next: python3 -I -S ' + str(prefix / 'portable/cli.py') + ' doctor')


if __name__ == '__main__':
    try:
        if sys.version_info < (3, 10):
            raise ValueError('Python >= 3.10 required; select your own Python interpreter')
        main()
    except (OSError, ValueError) as exc:
        print('portable install: ' + str(exc), file=sys.stderr)
        sys.exit(2)
