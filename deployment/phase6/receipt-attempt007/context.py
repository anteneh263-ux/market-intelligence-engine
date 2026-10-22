"""Candidate007 procedural context; imports never create a root or call AWS.

Requires separately approved root, authorization, exact source pins and preflight.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,time
P=Path('/Users/antenehhailu/.codex/visualizations/2026/10/01/01a0f6c9-d66b-7d51-85fa-da98aabc6ae7/mie-planning')
ROOT=P/'phase6-host-preparation-run-007'
CANDIDATE=P/'phase6-receipt-recovery-20261021'
WORK=Path('/Users/antenehhailu/MIE_RECEIPT_MEDIUM_WORKTREE')
PYTHON='/Users/antenehhailu/Desktop/market-intelligence-engine/.venv/bin/python'
KIT=CANDIDATE/'receipt-host-kit-medium-v1.tar.gz'
SHA='c3a024b3e09483f3c2341ec87cb10e6843dbd39dc174fa7b2aac660666bf216e'
ACCOUNT='989879505232';REGION='eu-north-1'
STACK='arn:aws:cloudformation:eu-north-1:989879505232:stack/mie-p6-qualification-001/c0769b00-caf6-11f1-8e71-0affdc797e93'
RECEIPT='i-0c5a2a75ca70db228';SOURCE='i-08e89ad2e3feedb8a'
BUCKET='mie-p6-qualification-001-989879505232-artifacts'
ENV=dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='/private/tmp/mie-phase6-qualification-test-20261018')
ATTEMPT='007'
PACKAGE_VERSION='receipt-host-preparation.007.v1'
HERE=Path(__file__).resolve().parent
PROCESS_DIR=HERE.parent/'receipt-process-identity-v4'
def require_open():
    live.root_check(ROOT)
    live.control.require(not any((ROOT/name).exists() or (ROOT/name).is_symlink()
        for name in ('operation-closure.json','closed-files-inventory.json')))
def write(name,data):
    require_open()
    path=ROOT/name
    live.control.require(not Path(name).is_absolute() and '..' not in Path(name).parts
        and path.parent.resolve(strict=True).is_relative_to(ROOT))
    raw=data if isinstance(data,bytes) else (json.dumps(data,indent=2,sort_keys=True)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def read(name):return binding.read(ROOT/name)
def call(name,service,operation,payload,extra=(),timeout=30):
    require_open()
    cmd=['/opt/homebrew/bin/aws',service,operation,'--profile','mie-phase6','--region',('us-east-1' if service=='iam' else REGION),'--endpoint-url',('https://iam.amazonaws.com' if service=='iam' else 'https://'+('s3' if service=='s3api' else service)+'.'+REGION+'.amazonaws.com'),'--output','json','--no-cli-pager','--no-cli-auto-prompt','--cli-connect-timeout','3','--cli-read-timeout','15','--cli-input-json',json.dumps(payload),*extra]
    write(name+'-request.json',dict(service=service,operation=operation,payload=payload,signing_region=('us-east-1' if service=='iam' else REGION),extra=list(extra),requested_utc=datetime.now(timezone.utc).isoformat(),aws_attempt_limit=1))
    try:r=live.bounded_cli(cmd,ENV,timeout=timeout)
    except BaseException as exc:
        if isinstance(exc,live.CallUnknown):
            write(name+'-stdout.json',exc.stdout);write(name+'-stderr.txt',exc.stderr)
        write(name+'-unknown.json',dict(status='OUTCOME_UNKNOWN',exception_type=type(exc).__name__,automatic_mutation_retry_allowed=False));raise
    write(name+'-stdout.json',r.stdout);write(name+'-stderr.txt',r.stderr)
    assert r.returncode==0,(name,r.returncode,'see preserved stderr; no automatic mutation retry')
    return json.loads(r.stdout) if r.stdout.strip() else {}
def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def observer_command():
    sha=hashlib.sha256((ROOT/'launch-contract.json').read_bytes()).hexdigest()
    return sha,[PYTHON,'-u',str(PROCESS_DIR/'watch-operator.py'),
        '--contract',str(ROOT/'launch-contract.json'),'--sha256',sha,
        '--evidence-root',str(ROOT/'operator'),'--execute-approved']

def operator_pid():
    sha,_=observer_command()
    return process.lookup(ROOT,sha)

def health(min_seconds=1,*,settled=True):
    sha,_=observer_command()
    checked=live.Supervisor(ROOT,sha,operator_pid()).observe(
        min_seconds=min_seconds,settled=settled,receipt_transition=True)
    return {'data':checked}


import importlib.util
VARIANT=WORK/'deployment/phase6/receipt-medium-v1'
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
guard=module('medium_guard',VARIANT/'host-control.py')
generator=module('medium_generator',VARIANT/'host-preparation-command.py')
process=module('attempt007_process',PROCESS_DIR/'process-control.py')
live=process.live
binding=process.binding
native_probe=module('attempt007_probe',PROCESS_DIR/'local-preflight.py')
CONTROL_VERSION=binding.VERSION
def local_preflight():
    pin=binding.read(PROCESS_DIR/'macos-python-pin.json')
    result=native_probe.run(pin)
    # Protective interruption also needs the pid/ppid inventory permission.
    rows=subprocess.check_output(['/bin/ps','-axo','pid=,ppid='],text=True,timeout=3)
    parsed=[tuple(map(int,line.split())) for line in rows.splitlines()]
    binding.require(all(len(row)==2 for row in parsed) and any(row[0]==os.getpid() for row in parsed))
    binding.require(result['status']=='LOCAL_PROCESS_PREFLIGHT_PASS' and result['aws_calls']==0
                    and result['window_started'] is False)
    return dict(result,pid_inventory_readable=True,package_version=PACKAGE_VERSION)
v2=live.control
def ids(value):
    assert not value.get('NextToken')
    rows=value['StackResourceSummaries']
    result={r['LogicalResourceId']:r['PhysicalResourceId'] for r in rows}
    assert len(result)==len(rows)==18
    return result
def body(value):
    v=value['TemplateBody'];return json.loads(v) if isinstance(v,str) else v
def states(value):
    return {i['InstanceId']:i['State']['Name'] for r in value['Reservations'] for i in r['Instances']}
def pair(prefix,stage):
    # Sequential reads avoid simultaneous OAuth token refresh bursts.
    jobs=[('instances','ec2','describe-instances',dict(InstanceIds=[SOURCE,RECEIPT])),
          ('volumes','ec2','describe-volumes',dict(VolumeIds=list(guard.VOLUMES.values()))),
          ('credits','ec2','describe-instance-credit-specifications',dict(InstanceIds=[SOURCE,RECEIPT]))]
    out={n:call(prefix+'-'+n,s,o,p) for n,s,o,p in jobs}
    checked=guard.validate_pair(out['instances'],out['volumes'],out['credits'],stage=stage)
    write(prefix+'-pair-checked.json',checked);return out

def schedule_ok(s,expression):
    prior=json.loads((P/'phase6-host-preparation-run-003/final-schedule-stdout.json').read_bytes())
    ignore={'ScheduleExpression','LastModificationDate'}
    assert {k:v for k,v in s.items() if k not in ignore}=={k:v for k,v in prior.items() if k not in ignore}
    assert s['ScheduleExpression']==expression and s['State']=='ENABLED' and s['ScheduleExpressionTimezone']=='UTC' and s['FlexibleTimeWindow']=={'Mode':'OFF'}
    assert s['Target']['Arn']=='arn:aws:scheduler:::aws-sdk:ec2:stopInstances' and set(json.loads(s['Target']['Input'])['InstanceIds'])=={SOURCE,RECEIPT}
    return True
def stop_expression():
    return datetime.fromtimestamp(read('launch-contract.json')['stop_utc_ms']//1000,timezone.utc).strftime('at(%Y-%m-%dT%H:%M:%S)')
REQUIRED_SOURCES=['context.py','procedure.py','guest.py','checks.py','create-root.py',
                  'baseline006.json','ATTEMPT007-RUNBOOK.md']
def required_paths():
    return ['deployment/phase6/receipt-attempt007/'+n for n in REQUIRED_SOURCES]+[
        'deployment/phase6/receipt-process-identity-v4/'+n for n in binding.REQUIRED]
def validate_manifest(expected):
    binding.require(expected['attempt']==ATTEMPT and expected['control_version']==CONTROL_VERSION
        and expected['package_version']==PACKAGE_VERSION and expected['evidence_root']==str(ROOT))
    binding.require(all(n in expected['files'] for n in required_paths()))
    binding.source_pins(expected)

def pins():
    binding.require(__debug__)
    require_open()
    authorization=read('authorization.json')
    binding.require(authorization['evidence_root']==str(ROOT)
        and authorization['owner']=='Anteneh Hailu' and authorization['approved'] is True
        and authorization['control_version']==CONTROL_VERSION
        and authorization['attempt']==ATTEMPT and authorization['package_version']==PACKAGE_VERSION)
    for filename,field in [('EXECUTION-REVIEW.md','review_sha256'),
            ('approved-source-pins.json','source_pins_sha256'),('interpreter-pin.json','interpreter_pin_sha256')]:
        binding.require(hashlib.sha256(binding.raw_file(ROOT/filename)).hexdigest()==authorization[field])
    expected=read('approved-source-pins.json');validate_manifest(expected)
    binding.require(expected['commit']==authorization['source_commit'])
    binding.require(authorization['interpreter_pin_sha256']==expected['files'][
        'deployment/phase6/receipt-process-identity-v4/macos-python-pin.json'])
    binding.identity.validate_pin(read('interpreter-pin.json'))
    original=json.loads((CANDIDATE/'medium-v1-artifact-verification.json').read_bytes())
    for path,h in original['files'].items():
        binding.require(hashlib.sha256((WORK/path).read_bytes()).hexdigest()==h)
    binding.require(hashlib.sha256(KIT.read_bytes()).hexdigest()==SHA and len(KIT.read_bytes())==21585861)
    guard.validate_templates((VARIANT.parent/'qualification-v1.json').read_bytes(),(VARIANT/'qualification-v1.json').read_bytes())
