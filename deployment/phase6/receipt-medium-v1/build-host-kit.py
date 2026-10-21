"""Build the local preparation kit. No uploads, installs, credentials or AWS calls."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile


def build(repo, runtime, crates):
    prefix = repo / 'deployment/phase6/receipt-medium-v1'
    spec = importlib.util.spec_from_file_location('host_preparation', prefix / 'prepare-qualification-host.py')
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    raw = prep.read_file(runtime)
    prep.bundle_files(raw)
    files = {'runtime.tar.gz': raw}
    for name, sha in prep.CRATES.items():
        raw = prep.read_file(crates / (name + '.crate'))
        prep.unpack(raw, sha)
        files['crates/' + name + '.crate'] = raw
    for name in ('prepare-qualification-host.py', 'host-clock-readiness.py', 'build-host-kit.py'):
        files[name] = prep.read_file((prefix.parent if name == 'host-clock-readiness.py' else prefix) / name)
    plan = dict(schema_version='p6.host_preparation_plan.receipt_medium.v1', purpose='HOST_PREPARATION_ONLY',
        account=prep.ACCOUNT, region=prep.REGION, ami=prep.AMI, instance_types=prep.INSTANCE_TYPES, instances=prep.INSTANCES,
        allowed_preparation_roles=['receipt'], deployment_variant='receipt-medium-v1',
        cpu_credits='standard', roles=prep.ROLES, profiles=prep.PROFILES,
        installation_root=str(prep.BASE), operational_logs=str(prep.BASE / 'operations'),
        runtime_bundle_sha256=prep.BUNDLE_SHA, native_source_sha256=prep.CRATES,
        preparation_budget_seconds=1800, python_wheels_offline=True,
        rpm_and_cargo_network_required=True, native_binaries_included=False,
        runtime_evidence_roots_created=False, identity_values_included=False,
        receipt_listener_started=False, runtime_accepted=False,
        prospective_evidence=False, cloud_execution_approved=False)
    files['host-plan.json'] = (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode()
    manifest = dict(schema_version='p6.host_kit.receipt_medium.v1', purpose='HOST_PREPARATION_ONLY',
        files={n: dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for n, raw in sorted(files.items())})
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
    p.add_argument('--runtime', required=True, type=Path)
    p.add_argument('--crates', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    raw, manifest = build(a.repo.resolve(strict=True), a.runtime.resolve(strict=True), a.crates.resolve(strict=True))
    with a.output.open('xb') as f:
        f.write(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                         files=len(manifest['files']), cloud_executed=False), sort_keys=True))


if __name__ == '__main__':
    main()
