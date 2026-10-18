"""Build a deterministic candidate archive from an explicit source closure and locked wheels.

This writes one new local archive. It neither installs nor uploads nor starts anything.
"""
import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


def checked_file(root, relative):
    path = root / relative
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError('nonregular or redirected input')
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('input too large')
    return path.read_bytes()


def build(repo, wheelroot):
    files = {}
    pending = {'app.research.phase6_qualification_runtime', 'app.research.phase6_qualification_operator'}
    visited = set()
    while pending:
        module = min(pending)
        pending.remove(module)
        if module in visited:
            continue
        visited.add(module)
        path = module.replace('.', '/') + '.py'
        if not (repo / path).is_file():
            path = module.replace('.', '/') + '/__init__.py'
        raw = checked_file(repo, path)
        files['code/' + path] = raw
        parts = module.split('.')
        pending.update('.'.join(parts[:i]) for i in range(1, len(parts)))
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                raise ValueError('relative import requires explicit closure review')
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('app.'):
                pending.add(node.module)
            if isinstance(node, ast.Import):
                pending.update(n.name for n in node.names if n.name.startswith('app.'))
    prefix = 'deployment/phase6/'
    for name in ('qualification-linux-py311-wheels.json', 'qualification-linux-py311.lock',
                 'qualification-v1.json', 'build-qualification-bundle.py'):
        files[prefix + name] = checked_file(repo, prefix + name)
    lock = json.loads(files[prefix + 'qualification-linux-py311-wheels.json'])
    if lock['schema_version'] != 'p6.qualification_wheels.v1' or len(lock['wheels']) != 11:
        raise ValueError('wrong dependency lock')
    for wheel in lock['wheels']:
        name = wheel['filename']
        if Path(name).name != name or not name.endswith('.whl') or 'wheels/' + name in files:
            raise ValueError('invalid wheel name')
        raw = checked_file(wheelroot, name)
        if len(raw) != wheel['bytes'] or hashlib.sha256(raw).hexdigest() != wheel['sha256']:
            raise ValueError('wheel differs from lock')
        files['wheels/' + name] = raw
    if sum(map(len, files.values())) > 64 * 1024 * 1024:
        raise ValueError('archive too large')
    manifest = dict(schema_version='p6.qualification_bundle.v1',
        purpose='SYNTHETIC_RECEIPT_QUALIFICATION_CANDIDATE_ONLY',
        target='CPython 3.11 Linux aarch64 glibc >= 2.28',
        native_clockbound_included=False, identities_included=False,
        files={name: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
               for name, raw in sorted(files.items())})
    files['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', filename='', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue(), manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--wheels', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    raw, manifest = build(a.repo.resolve(strict=True), a.wheels.resolve(strict=True))
    with a.output.open('xb') as f:
        f.write(raw)
    print(json.dumps(dict(archive=str(a.output.resolve()), bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), files=len(manifest['files']),
        runtime_accepted=False), sort_keys=True))


if __name__ == '__main__':
    main()
