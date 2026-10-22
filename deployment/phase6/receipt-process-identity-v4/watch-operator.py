"""V4 process-bound watch entry; preserved v3 AWS/state/event semantics.

No runtime root creation. Identity is verified and durably published BEFORE any
AWS read or ObjectWriter creation. Local probes use a separate harmless script.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


binding = load('watch_binding_v4', HERE/'binding.py')
V3 = HERE.parent/'receipt-update-states-v3/watch-operator.py'
binding.require(hashlib.sha256(V3.read_bytes()).hexdigest() ==
    'bfff2c9c5ca7ddb2246c486dd537692c104facea48e0c3a8e579f573a4828d61')
operator = load('preserved_watch_v3_for_v4', V3)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('contract', 'sha256', 'evidence-root'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--execute-approved', action='store_true', required=True)
    a = p.parse_args(argv)
    writer = None
    try:
        root = binding.root_check(Path(a.evidence_root).parent)
        binding.require(a.evidence_root == str(root/'operator') and a.contract == str(root/'launch-contract.json'))
        want = binding.expected(root, a.sha256, os.getpid())
        bound = binding.identity.bind(want)
        binding.save(root, 'process-identity.json', bound)
        binding.identity.verify(binding.read(root/'process-identity.json'), want)
        raw = operator.read_file(a.contract, 65536)
        operator.require(hashlib.sha256(raw).hexdigest() == a.sha256)
        c = operator.contract(operator.parse(raw), time.time_ns()//1000000)
        writer = operator.ObjectWriter(a.evidence_root, owner=operator.ACCOUNT,
            run_id=operator._hash(c), max_bytes=16*1024*1024)
        answer = operator.supervise(c, api=operator.AwsCli(), writer=writer,
            armed=lambda digest: print(json.dumps(dict(status='ARMED', ready_hash=digest,
                process_control_version=binding.VERSION)), flush=True))
        print(json.dumps(answer, sort_keys=True), flush=True)
        return 0 if answer['status'] == 'STOP_CONFIRMED' else 2
    except BaseException:
        print(json.dumps(dict(status='UNRESOLVED', operator_action_required=True,
                             runtime_accepted=False)), flush=True)
        return 2
    finally:
        if writer:
            writer.close()


if __name__ == '__main__':
    raise SystemExit(main())
