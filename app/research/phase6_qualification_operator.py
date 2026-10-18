"""Bounded operator for the exact qualification stack; explicit execution only."""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from app.research.phase6_qualification_runtime import ACCOUNT, REGION, PREFIX, parse, require, snapshot
from app.research.phase6_anchor_validation import _hash, _hex
from app.research.phase6_object_store import ObjectWriter, _root, _read
from app.research.phase6_source_intake import encoded
from app.research.phase6_qualification_runtime import inspect_writer

LOGICAL_IDS = {'ReceiptInstance', 'SourceInstance'}
ACTIVE = {'pending', 'running'}
OFF = {'stopped', 'terminated'}
STABLE = {'CREATE_COMPLETE', 'CREATE_FAILED', 'ROLLBACK_COMPLETE', 'ROLLBACK_FAILED',
          'UPDATE_COMPLETE', 'UPDATE_FAILED', 'UPDATE_ROLLBACK_COMPLETE', 'UPDATE_ROLLBACK_FAILED'}


def continuous_ns():
    if platform.system() == 'Linux':
        return time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    require(platform.system() == 'Darwin')
    class Timebase(ctypes.Structure):
        _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    lib.mach_continuous_time.argtypes = []
    lib.mach_continuous_time.restype = ctypes.c_uint64
    lib.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
    lib.mach_timebase_info.restype = ctypes.c_int
    base = Timebase()
    require(lib.mach_timebase_info(ctypes.byref(base)) == 0 and base.numer > 0 and base.denom > 0)
    return lib.mach_continuous_time() * base.numer // base.denom


def contract(value, now_ms):
    c = snapshot(value)
    require(set(c) == {'schema_version', 'account_id', 'region', 'stack_id', 'operator_arn',
                       'not_before_utc_ms', 'stop_utc_ms', 'template_sha256', 'runtime_bundle_sha256'})
    require(c['schema_version'] == 'p6.qualification_launch.v1'
            and c['account_id'] == ACCOUNT and c['region'] == REGION)
    require(type(c['stack_id']) is str and re.fullmatch('arn:aws:cloudformation:' + REGION + ':' + ACCOUNT
        + ':stack/' + PREFIX + '/[0-9a-f-]{36}', c['stack_id']))
    require(type(c['operator_arn']) is str and c['operator_arn'].startswith(('arn:aws:iam::' + ACCOUNT + ':',
                                                                          'arn:aws:sts::' + ACCOUNT + ':')))
    for field in ('not_before_utc_ms', 'stop_utc_ms'):
        require(type(c[field]) is int and c[field] % 1000 == 0)
    require(c['not_before_utc_ms'] <= now_ms and 30000 <= c['stop_utc_ms'] - now_ms <= 3600000
            and 0 < c['stop_utc_ms'] - c['not_before_utc_ms'] <= 3600000)
    require(_hex(c['template_sha256']) and _hex(c['runtime_bundle_sha256']))
    return c


class AwsCli:
    """No shell, no request retries; never return stderr/credentials in evidence."""
    def __init__(self, path='/opt/homebrew/bin/aws'):
        self.path = path

    def call(self, service, operation, payload):
        allowed = {('sts','get-caller-identity'), ('cloudformation','describe-stacks'),
                   ('cloudformation','update-stack'), ('ec2','describe-instances'), ('ec2','stop-instances')}
        require((service, operation) in allowed)
        env = dict(os.environ, AWS_MAX_ATTEMPTS='1', AWS_RETRY_MODE='standard')
        completed = subprocess.run([self.path, service, operation, '--profile', 'mie-phase6',
            '--region', REGION, '--endpoint-url', 'https://' + service + '.' + REGION + '.amazonaws.com',
            '--output', 'json', '--no-cli-pager', '--no-cli-auto-prompt', '--cli-connect-timeout', '3',
            '--cli-read-timeout', '5', '--cli-input-json', json.dumps(payload)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        require(completed.returncode == 0 and len(completed.stdout) <= 1024 * 1024)
        return json.loads(completed.stdout)


def stack_status(api, c):
    response = api.call('cloudformation', 'describe-stacks', {'StackName': c['stack_id']})
    stacks = response['Stacks']
    require(len(stacks) == 1 and stacks[0]['StackId'] == c['stack_id'])
    return stacks[0]['StackStatus']


def instances(api, c, ids=None):
    payload = {'InstanceIds': sorted(ids)} if ids else {'Filters': [
        {'Name': 'tag:aws:cloudformation:stack-id', 'Values': [c['stack_id']]}]}
    response = api.call('ec2', 'describe-instances', payload)
    require(not response.get('NextToken'))  # Never treat a truncated inventory as complete.
    found = {}
    for reservation in response.get('Reservations', []):
        require(reservation.get('OwnerId') == ACCOUNT)
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
            require(tags.get('aws:cloudformation:stack-id') == c['stack_id']
                    and tags.get('aws:cloudformation:logical-id') in LOGICAL_IDS
                    and tags.get('Purpose') == 'phase6-qualification-only'
                    and re.fullmatch('i-[0-9a-f]{17}', instance_id) and instance_id not in found)
            state = instance['State']['Name']
            require(state in ACTIVE | OFF | {'stopping', 'shutting-down'})
            found[instance_id] = state
    require(len(found) <= 4)  # At most the pair plus replacements; all remain exact-stack scoped.
    if ids is not None:
        require(set(found) == set(ids))
    return found


def supervise(c, *, api, writer, template=None, bundle=None, utc_ms=lambda: time.time_ns() // 1000000,
              elapsed_ns=continuous_ns, sleep=time.sleep, armed=None):
    start_utc, start_elapsed = utc_ms(), elapsed_ns()
    c = contract(c, start_utc)
    require(type(writer) is ObjectWriter and writer.header['owner'] == ACCOUNT
            and writer.header['run_id'] == _hash(c))
    identity = api.call('sts', 'get-caller-identity', {})
    require(identity.get('Account') == ACCOUNT and identity.get('Arn') == c['operator_arn'])
    state = stack_status(api, c)
    require(state in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'})
    known = instances(api, c)
    if template is not None:
        require(not known and type(template) is bytes and len(template) <= 51200
                and hashlib.sha256(template).hexdigest() == c['template_sha256'])
        require(type(bundle) is bytes and 0 < len(bundle) <= 64 * 1024 * 1024
                and hashlib.sha256(bundle).hexdigest() == c['runtime_bundle_sha256'])
    seq = 0
    trace_failed = False
    previous = '0' * 64
    requested = set()
    reason = None
    deadline_ns = start_elapsed + (c['stop_utc_ms'] - start_utc) * 1000000
    end_ns = deadline_ns + 180000000000
    last_utc, last_elapsed = start_utc, start_elapsed

    def record(kind, data):
        nonlocal seq, previous, trace_failed
        if trace_failed:
            return None
        try:
            digest = writer.put(encoded(dict(schema_version='p6.qualification_operator_event.v1',
                sequence=seq + 1, previous_hash=previous, kind=kind, data=data)))
        except BaseException:
            trace_failed = True
            return None
        seq += 1
        previous = digest
        return digest

    ready = record('armed', dict(contract=c, observed_instances=known, utc_ms=start_utc,
                                continuous_ns=start_elapsed, qualification_only=True))
    require(not trace_failed)
    if armed:
        armed(ready)
    if template is not None:
        # Fresh guard after preflight/arming, immediately before the only launch request.
        contract(c, utc_ms())
        require(start_elapsed <= elapsed_ns() < deadline_ns)
        stop = datetime.datetime.fromtimestamp(c['stop_utc_ms'] // 1000, datetime.timezone.utc).strftime('at(%Y-%m-%dT%H:%M:%S)')
        record('update_requested', dict(template_sha256=c['template_sha256'], stop_at_utc=stop))
        require(not trace_failed)
        try:
            response = api.call('cloudformation', 'update-stack', dict(StackName=c['stack_id'],
                TemplateBody=template.decode('utf-8'), Capabilities=['CAPABILITY_IAM'],
                Parameters=[{'ParameterKey':'LaunchCompute','ParameterValue':'yes'},
                            {'ParameterKey':'StopAtUtc','ParameterValue':stop}], ClientRequestToken=_hash(c)))
            require(response.get('StackId') == c['stack_id'])
            record('update_accepted', dict(stack_id=c['stack_id']))
        except BaseException:
            reason = 'UPDATE_OUTCOME_UNKNOWN'
            record('update_unknown', dict(retry_allowed=False))
    status = 'UNRESOLVED'
    for iteration in range(800):  # A finite observation fallback, not a hard elapsed-time cap.
        clock_available = True
        try:
            wall, continuous = utc_ms(), elapsed_ns()
            require(type(wall) is int and type(continuous) is int and wall >= 0 and continuous >= 0)
        except BaseException:
            clock_available = False
            wall, continuous = last_utc, last_elapsed
            reason = reason or 'CLOCK_UNAVAILABLE'
        if iteration > 0 and continuous == last_elapsed:
            reason = reason or 'CLOCK_STALLED'
        drift = abs((wall - last_utc) * 1000000 - (continuous - last_elapsed))
        if wall < last_utc or continuous < last_elapsed or drift > 2000000000:
            reason = reason or 'CLOCK_ANOMALY'
        if wall >= c['stop_utc_ms'] or continuous >= deadline_ns:
            reason = reason or 'DEADLINE'
        last_utc, last_elapsed = wall, continuous
        if trace_failed:
            reason = reason or 'EVIDENCE_FAILURE'
        observed = None
        try:
            state = stack_status(api, c)
            observed = instances(api, c)
            known.update(observed)
            if state not in {'CREATE_COMPLETE', 'UPDATE_COMPLETE', 'UPDATE_IN_PROGRESS'}:
                reason = reason or 'STACK_FAILURE'
            record('observation', dict(utc_ms=wall if clock_available else None,
                continuous_ns=continuous if clock_available else None, clock_available=clock_available,
                stack_status=state, instances=observed))
        except BaseException:
            reason = reason or 'OBSERVATION_UNAVAILABLE'
            record('observation_unavailable', dict(utc_ms=wall if clock_available else None,
                continuous_ns=continuous if clock_available else None, clock_available=clock_available))
        if trace_failed:
            reason = reason or 'EVIDENCE_FAILURE'
        if reason:
            for instance_id in sorted(known):
                if instance_id in requested:
                    continue
                try:
                    fresh = instances(api, c, {instance_id})  # Recheck ownership before each mutation.
                    if fresh[instance_id] not in ACTIVE:
                        continue
                    record('stop_requested', dict(instance_id=instance_id))
                    requested.add(instance_id)  # Unknown response must not cause an automatic repeat.
                    api.call('ec2', 'stop-instances', {'InstanceIds': [instance_id]})
                    record('stop_response_received', dict(instance_id=instance_id, stopped_confirmed=False))
                except BaseException:
                    record('stop_unresolved', dict(instance_id=instance_id))
            # A successful API return is insufficient. Require fresh actual states and settled CFN.
            if ((continuous >= deadline_ns or (bool(known) and state != 'CREATE_COMPLETE'))
                    and observed is not None and state in STABLE
                    and set(observed) == set(known) and all(s in OFF for s in observed.values())):
                status = 'STOP_CONFIRMED' if observed else 'NO_INSTANCES_OBSERVED'
                break
        if continuous >= end_ns:
            break
        try:
            sleep(5)
        except BaseException:
            reason = reason or 'OPERATOR_INTERRUPTED'
    digest = record('finished', dict(status=status, reason=reason, known_instances=known,
                                     runtime_accepted=False, resources_deleted=False))
    writer.close()
    if trace_failed:
        return dict(status='UNRESOLVED', observed_stop_status=status, evidence_complete=False,
                    operator_action_required=True, runtime_accepted=False)
    inventory = inspect_writer(writer)
    return dict(status=status, final_event_hash=digest, inventory=inventory,
                operator_action_required=status == 'UNRESOLVED', runtime_accepted=False)


def read_file(path, limit):
    require(type(path) is str and path.startswith('/') and os.path.normpath(path) == path)
    parent, name = os.path.split(path)
    fd = _root(parent)
    try:
        size = os.stat(name, dir_fd=fd, follow_symlinks=False).st_size
        require(size <= limit)
        return _read(fd, name, size)
    finally:
        os.close(fd)


def main(argv=None):
    p = argparse.ArgumentParser(description='Approved qualification stack operator; never deletes resources.')
    p.add_argument('--contract', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--template', help='Explicitly update the existing foundation to launch compute.')
    p.add_argument('--runtime-bundle', help='Existing immutable archive; required with --template.')
    p.add_argument('--execute-approved', action='store_true', required=True)
    a = p.parse_args(argv)
    writer = None
    try:
        raw = read_file(a.contract, 65536)
        require(_hex(a.sha256) and hashlib.sha256(raw).hexdigest() == a.sha256)
        c = contract(parse(raw), time.time_ns() // 1000000)
        require(bool(a.template) == bool(a.runtime_bundle))
        template = read_file(a.template, 51200) if a.template else None
        bundle = read_file(a.runtime_bundle, 64 * 1024 * 1024) if a.runtime_bundle else None
        writer = ObjectWriter(a.evidence_root, owner=ACCOUNT, run_id=_hash(c), max_bytes=16 * 1024 * 1024)
        answer = supervise(c, api=AwsCli(), writer=writer, template=template, bundle=bundle,
            armed=lambda digest: print(json.dumps(dict(status='ARMED', ready_hash=digest)), flush=True))
        print(json.dumps(answer, sort_keys=True), flush=True)
        return 0 if answer['status'] == 'STOP_CONFIRMED' else 2
    except BaseException:
        print(json.dumps(dict(status='UNRESOLVED', operator_action_required=True, runtime_accepted=False)), flush=True)
        return 2
    finally:
        if writer:
            try:
                writer.close()
            except BaseException:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
