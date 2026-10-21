"""Versioned state-aware live entry point. Import/--help never call AWS.

Uses the unchanged v2 execution guard/API adapter; only the versioned watch-only
process, schema and state-aware supervision differ. Existing root/approval required.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

def load(name, path, sha):
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError('V3_DEPENDENCY_HASH_REFUSED')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

v2 = load('preserved_live_v2', HERE.parent/'receipt-medium-control-v2/execute-live.py',
          'f1bed0aad725163ef6fd069416c3660d6d05cd27fa1e8f82b71c73d6319c23c1')
progress = load('live_progress_v3', HERE/'progress.py', '28a26f9bed981ff351274698c00eea45169c1a792e484c4a00aa9d61825593f3')
operator = load('watch_operator_v3', HERE/'watch-operator.py', 'bfff2c9c5ca7ddb2246c486dd537692c104facea48e0c3a8e579f573a4828d61')
control, PYTHON, KIT_SHA = v2.control, v2.PYTHON, v2.KIT_SHA
root_check, read_regular, save = v2.root_check, v2.read_regular, v2.save
process_command, RecordedAPI, bounded_cli = v2.process_command, v2.RecordedAPI, v2.bounded_cli
CallUnknown = v2.CallUnknown


class Supervisor:
    def __init__(self, root, contract_sha, pid, *, utc_ms=lambda: time.time_ns()//1000000,
                 continuous_ns=operator.continuous_ns, command=process_command):
        self.root, self.sha, self.pid = root_check(root), contract_sha, pid
        self.utc_ms, self.continuous_ns, self.command = utc_ms, continuous_ns, command

    def __call__(self):
        return self.observe(min_seconds=2400, settled=True, receipt_transition=False)

    def observe(self, *, min_seconds, settled=True, receipt_transition=False):
        control.require(type(min_seconds) is int and 0 < min_seconds <= 2700)
        control.require(type(settled) is bool and type(receipt_transition) is bool)
        raw = read_regular(self.root/'launch-contract.json', 65536)
        control.require(hashlib.sha256(raw).hexdigest() == self.sha)
        now, continuous = self.utc_ms(), self.continuous_ns()
        control.require(type(continuous) is int and continuous >= 0)
        c = operator.contract(control.parse(raw), now)
        control.require(c['stack_id'] == control.STACK and c['operator_arn'] == f'arn:aws:iam::{control.ACCOUNT}:root'
                        and c['template_sha256'] == control.TEMPLATE_SHA and c['runtime_bundle_sha256'] == KIT_SHA
                        and c['stop_utc_ms']-c['not_before_utc_ms'] == 2700000)
        expected_command = [PYTHON, '-u', str(HERE/'watch-operator.py'),
            '--contract', str(self.root/'launch-contract.json'), '--sha256', self.sha,
            '--evidence-root', str(self.root/'operator'), '--execute-approved']
        control.require(self.command(self.pid) == expected_command)
        folder = root_check(self.root/'operator')
        header = control.parse(read_regular(folder/'writer.json', 65536))
        control.require(header == dict(schema_version='p6.object_store.v1', owner=control.ACCOUNT,
                        run_id=hashlib.sha256(control.canonical(c).encode()).hexdigest(), max_bytes=16*1024*1024))
        paths = list(folder.glob('*.blob'))
        control.require(2 <= len(paths) <= 801)
        entries = []
        for path in paths:
            raw = read_regular(path, 65536)
            control.require(hashlib.sha256(raw).hexdigest() == path.stem)
            entries.append((control.parse(raw), path.stem))
        entries.sort(key=lambda item: item[0]['sequence'])
        previous, last_wall, last_elapsed = '0'*64, None, None
        pair = {i:'stopped' for i in control.v1.INSTANCES.values()}
        for index, (event, digest) in enumerate(entries):
            control.require(set(event) == {'schema_version','sequence','previous_hash','kind','data'}
                            and event['schema_version'] == progress.EVENT_SCHEMA
                            and type(event['sequence']) is int and event['sequence'] == index+1
                            and event['previous_hash'] == previous)
            d = event['data']
            if index == 0:
                control.require(event['kind'] == 'armed' and d['contract'] == c
                                and d['observed_instances'] == pair and d['qualification_only'] is True
                                and d['control_version'] == progress.VERSION)
                armed_wall, armed_elapsed = d['utc_ms'], d['continuous_ns']
                control.require(type(armed_wall) is int and c['not_before_utc_ms'] <= armed_wall <= now)
            else:
                control.require(event['kind'] == 'observation' and d['clock_available'] is True
                                and set(d['instances']) == set(pair) and d['instances'][control.v1.INSTANCES['source']] == 'stopped'
                                and d['instances'][control.v1.INSTANCES['receipt']] in
                                    ({'stopped','pending','running','stopping'} if receipt_transition else {'stopped'}))
                progress.classify(d['stack_status'])
            wall, elapsed = d['utc_ms'], d['continuous_ns']
            control.require(type(wall) is int and type(elapsed) is int and elapsed >= 0)
            if last_wall is not None:
                control.require(wall >= last_wall and elapsed > last_elapsed
                    and abs((wall-last_wall)*1000000-(elapsed-last_elapsed)) <= 2000000000)
            previous, last_wall, last_elapsed = digest, wall, elapsed
        latest = entries[-1][0]['data']
        control.require(not settled or progress.classify(latest['stack_status']) == 'SETTLED')
        control.require(0 <= now-last_wall <= 25000 and 0 <= continuous-last_elapsed <= 25000000000)
        control.require(abs((now-armed_wall)*1000000-(continuous-armed_elapsed)) <= 2000000000)
        deadline = armed_elapsed+(c['stop_utc_ms']-armed_wall)*1000000
        control.require(min_seconds*1000000000 <= deadline-continuous <= 2700000000000)
        # Recheck live process after reading the entire chain, before returning evidence.
        control.require(self.command(self.pid) == expected_command)
        return dict(status='ARMED', stop_utc_ms=c['stop_utc_ms'], stack_id=c['stack_id'],
                    stack_status=latest['stack_status'], instances=latest['instances'], observed_utc_ms=last_wall,
                    control_version=progress.VERSION,
                    operator_pid=self.pid, operator_head=previous, continuous_remaining_ns=deadline-continuous)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--contract-sha256', required=True)
    p.add_argument('--operator-pid', required=True, type=int)
    p.add_argument('--change-set-id', required=True)
    p.add_argument('--stop-expression', required=True)
    p.add_argument('--execute-approved', action='store_true', required=True)
    a = p.parse_args(argv)
    root = root_check(a.evidence_root)
    supervisor = Supervisor(root, a.contract_sha256, a.operator_pid)
    answer = control.execute_once(RecordedAPI(root), expected_id=a.change_set_id,
        stop_expression=a.stop_expression, supervisor=supervisor, evidence_dir=str(root))
    save(root, 'medium-v3-execute-response.json', answer)
    print(control.canonical(answer))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
