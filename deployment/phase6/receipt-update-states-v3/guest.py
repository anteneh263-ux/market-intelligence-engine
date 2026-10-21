"""Candidate006 guest preparation calls; same medium-v1 guest commands/kit.

No root creation, AWS calls or guest actions on import. Separate approval required.
"""
import sys,json,time,hashlib,base64,gzip,subprocess,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent));import context as m
def request(code,comment,timeout=60):
    return dict(DocumentName='AWS-RunShellScript',DocumentVersion='1',InstanceIds=[m.RECEIPT],TimeoutSeconds=60,Comment=comment,Parameters={'commands':["set -eu\n/usr/bin/python3 - <<'MIE_READONLY'\n"+code+"\nMIE_READONLY\n"],'executionTimeout':[str(timeout)]})
def send(kind,payload):
    assert payload['InstanceIds']==[m.RECEIPT] and payload['DocumentName']=='AWS-RunShellScript' and payload['DocumentVersion']=='1'
    r=m.call(kind+'-send-once','ssm','send-command',payload)
    c=r['Command'];assert c['InstanceIds']==[m.RECEIPT]
    m.write(kind+'-command.json',dict(command_id=c['CommandId'],instance_id=m.RECEIPT,send_attempts=1,variant='receipt-medium-v1',operation_attempt='006'))
    print(json.dumps(dict(kind=kind,command_id=c['CommandId'])),flush=True)
def poll(kind):
    cmd=m.read(kind+'-command.json')
    r=m.call(kind+'-poll-'+m.stamp(),'ssm','get-command-invocation',dict(CommandId=cmd['command_id'],InstanceId=cmd['instance_id']))
    assert r['CommandId']==cmd['command_id'] and r['InstanceId']==m.RECEIPT
    print(json.dumps({k:r.get(k) for k in ('Status','ResponseCode','ExecutionElapsedTime')}),flush=True)
    if r['Status'] in ('Success','Failed','Cancelled','TimedOut'):
        m.write(kind+'-result.json',r)
    return r
def success(kind):
    r=m.read(kind+'-result.json');assert r['Status']=='Success' and r['ResponseCode']==0
    return json.loads(r['StandardOutputContent'].splitlines()[-1])
def admission():
    m.pins();m.health(2230);m.pair('admission','delivery')
    ss=m.call('admission-ssm-online','ssm','describe-instance-information',dict(Filters=[dict(Key='InstanceIds',Values=[m.RECEIPT])]))
    rows=ss['InstanceInformationList'];assert len(rows)==1 and rows[0]['InstanceId']==m.RECEIPT and rows[0]['PingStatus']=='Online'
    code="""import json,os,pathlib,pwd,stat
assert os.geteuid()==0
for name in ('/opt/mie/qualification-001-host-v1','/var/lib/mie/qualification-001/host-preparation-delivery-medium-v1'):
 p=pathlib.Path(name);assert not p.exists() and not p.is_symlink()
try:pwd.getpwnam('mie-qualification')
except KeyError:pass
else:raise RuntimeError('existing runtime user')
p=pathlib.Path('/var/lib/mie/qualification-001')
assert p.resolve()==p and p.stat().st_uid==0 and stat.S_IMODE(p.stat().st_mode)==0o700
p=pathlib.Path('/opt/mie');assert not p.is_symlink()
if p.exists():assert p.resolve()==p and p.stat().st_uid==0 and stat.S_IMODE(p.stat().st_mode)==0o755
assert stat.S_ISCHR(os.stat('/dev/vmclock0').st_mode)
print(json.dumps(dict(status='FRESH_TARGET_ADMITTED',installation_absent=True,runtime_user_absent=True,delivery_medium_absent=True,vmclock_character_device=True,read_only=True)))
"""
    m.health(2200);send('receipt-admission',request(code,'MIE receipt-medium read-only admission; no installation'))
def timer():
    a=success('receipt-admission');assert a['status']=='FRESH_TARGET_ADMITTED'
    m.pins();m.health(2200);m.pair('timer-before','delivery');m.health(2180)
    send('receipt-timer',m.generator.timer_request(m.read('launch-contract.json')['stop_utc_ms']))
def prepare():
    r=success('receipt-timer');c=m.read('launch-contract.json')
    assert r['status']=='GUEST_STOP_VERIFIED' and r['instance_id']==m.RECEIPT and r['stop_utc_ms']==c['stop_utc_ms']
    assert r['timer']['active']=='active' and r['timer']['substate']=='waiting' and r['remaining_us']>=2100000000
    m.pins();m.health(2080);m.pair('preparation-before','delivery')
    kit=m.read('kit-version-verification.json');assert kit['sha256']==m.SHA and kit['status']=='EXACT_VERSION_BYTES_VERIFIED'
    p=m.generator.request('receipt',m.RECEIPT,kit['version_id'],stop_utc_ms=c['stop_utc_ms'],expected_timer_us=r['timer']['next_us'])
    subprocess.run(['/bin/bash','-n'],input=p['Parameters']['commands'][0],text=True,check=True)
    m.health(2080);send('receipt-preparation',p)
COLLECT=r"""import base64,gzip,hashlib,json,pathlib,stat
p=pathlib.Path('/opt/mie/qualification-001-host-v1/operations')
assert p.resolve()==p and p.is_dir()
files={};inventory=[]
for f in sorted(p.iterdir()):
 assert f.is_file() and not f.is_symlink() and f.name not in ('.','..') and '/' not in f.name
 raw=f.read_bytes();assert len(raw)<=4*1024*1024
 inventory.append(dict(name=f.name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
 files[f.name]=base64.b64encode(raw).decode()
assert len(files)<=128 and sum(x['bytes'] for x in inventory)<=16*1024*1024
compressed=gzip.compress(json.dumps(files,sort_keys=True,separators=(',',':')).encode(),mtime=0)
assert len(compressed)<=512*1024
sha=hashlib.sha256(compressed).hexdigest()
if PART is None:
 answer=dict(status='OPERATIONS_INVENTORY',files=inventory,format='gzip-base64-json-filemap',compressed_bytes=len(compressed),compressed_sha256=sha,chunk_count=(len(compressed)+11999)//12000,chunk_bytes=12000,read_only=True)
else:
 assert type(PART) is int and 0<=PART<(len(compressed)+11999)//12000
 raw=compressed[PART*12000:(PART+1)*12000]
 answer=dict(part=PART,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),combined_sha256=sha,payload=base64.b64encode(raw).decode(),read_only=True)
encoded=json.dumps(answer,sort_keys=True);assert len(encoded)<23000
print(encoded)
"""
def collect(part=None):
    r=m.read('receipt-preparation-result.json');assert r['Status'] in ('Success','Failed','Cancelled','TimedOut')
    m.health(90)
    code='PART='+repr(part)+'\n'+COLLECT
    send('receipt-operations-inventory' if part is None else 'receipt-operations-chunk-'+str(part),request(code,'MIE read-only receipt operational logs; no evidence repair'))
def decode():
    inv=success('receipt-operations-inventory')
    assert inv['status']=='OPERATIONS_INVENTORY' and 0<inv['chunk_count']<=44
    parts=[]
    for i in range(inv['chunk_count']):
        r=success('receipt-operations-chunk-'+str(i));raw=base64.b64decode(r['payload'],validate=True)
        assert r['part']==i and r['bytes']==len(raw) and hashlib.sha256(raw).hexdigest()==r['sha256'] and r['combined_sha256']==inv['compressed_sha256']
        parts.append(raw)
    combined=b''.join(parts);assert len(combined)==inv['compressed_bytes'] and hashlib.sha256(combined).hexdigest()==inv['compressed_sha256']
    files=json.loads(gzip.decompress(combined));expected={f['name']:f for f in inv['files']}
    assert len(expected)==len(inv['files']) and set(expected)==set(files)
    out=m.ROOT/'receipt-operations-exact';out.mkdir(mode=0o700)
    for name,data in files.items():
        assert name not in ('.','..') and '/' not in name
        raw=base64.b64decode(data,validate=True);assert len(raw)==expected[name]['bytes'] and hashlib.sha256(raw).hexdigest()==expected[name]['sha256']
        m.write('receipt-operations-exact/'+name,raw)
    m.write('receipt-exact-log-verification.json',dict(status='ALL_OPERATION_FILES_BYTE_HASH_VERIFIED',files=len(files),bytes=sum(x['bytes'] for x in expected.values()),read_only_export=True))
    print('ALL_OPERATION_FILES_BYTE_HASH_VERIFIED',len(files),flush=True)
if __name__=='__main__':
    m.pins()
    op=sys.argv[1]
    if op=='poll':poll(sys.argv[2])
    elif op=='collect':collect(None if len(sys.argv)==2 else int(sys.argv[2]))
    else:{'admission':admission,'timer':timer,'prepare':prepare,'decode':decode}[op]()

