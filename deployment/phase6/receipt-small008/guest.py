"""Small008 exact existing kit/guest helpers; no AWS calls on import."""
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
g=module('small008_guest_guard',HERE/'guard.py')
generator=module('small008_small_generator',HERE.parent/'host-preparation-command.py')

def kit_request():
    return dict(Bucket=g.BUCKET,Key='code/'+g.KIT_SHA+'.tar.gz',VersionId=g.KIT_VERSION,ExpectedBucketOwner=g.ACCOUNT,ChecksumMode='ENABLED')
def verify_kit(value,raw):
    g.require(value['VersionId']==g.KIT_VERSION and value['ContentLength']==g.KIT_BYTES and value['ServerSideEncryption']=='AES256' and
        value['ChecksumSHA256']==base64.b64encode(bytes.fromhex(g.KIT_SHA)).decode() and len(raw)==g.KIT_BYTES and hashlib.sha256(raw).hexdigest()==g.KIT_SHA,'SMALL008_KIT')
def request(code,comment,timeout=60):
    return dict(DocumentName='AWS-RunShellScript',DocumentVersion='1',InstanceIds=[g.RECEIPT],TimeoutSeconds=60,
        Comment=comment,Parameters={'commands':["set -eu\n/usr/bin/python3 - <<'MIE_READONLY'\n"+code+"\nMIE_READONLY\n"],'executionTimeout':[str(timeout)]})
def admission():
    code="""import json,os,pathlib,pwd,stat
assert os.geteuid()==0
for name in ('/opt/mie/qualification-001-host-v1','/var/lib/mie/qualification-001/host-preparation-delivery-v2'):
 p=pathlib.Path(name);assert not p.exists() and not p.is_symlink()
try:pwd.getpwnam('mie-qualification')
except KeyError:pass
else:raise RuntimeError('existing runtime user')
p=pathlib.Path('/var/lib/mie/qualification-001')
assert p.resolve()==p and p.stat().st_uid==0 and stat.S_IMODE(p.stat().st_mode)==0o700
p=pathlib.Path('/opt/mie');assert not p.is_symlink()
if p.exists():assert p.resolve()==p and p.stat().st_uid==0 and stat.S_IMODE(p.stat().st_mode)==0o755
assert stat.S_ISCHR(os.stat('/dev/vmclock0').st_mode)
print(json.dumps(dict(status='FRESH_TARGET_ADMITTED',installation_absent=True,runtime_user_absent=True,delivery_v2_absent=True,vmclock_character_device=True,read_only=True)))
"""
    return request(code,'MIE small008 read-only receipt admission; no installation')
def answer(response):
    g.require(response['Status']=='Success' and response['ResponseCode']==0 and not response.get('StandardOutputUrl'),'SMALL008_SSM_SUCCESS')
    text=response['StandardOutputContent'];g.require(len(text)<24000,'SMALL008_SSM_TRUNCATION')
    return g.parse(text.splitlines()[-1])
def timer_ok(t,stop):
    g.require(t['status']=='GUEST_STOP_VERIFIED' and t['instance_id']==g.RECEIPT and t['stop_utc_ms']==stop and
        t['timer']['active']=='active' and t['timer']['substate']=='waiting' and
        type(t['timer']['next_us']) is int and t['timer']['next_us']>0 and t['remaining_us']>=2100000000,'SMALL008_TIMER')

def log_request(part):
    g.require(part is None or (type(part) is int and 0<=part<44),'SMALL008_LOG_PART')
    return request('PART='+repr(part)+'\n'+COLLECT,'MIE small008 read-only receipt operational logs')
def inventory_ok(inv):
    g.require(inv['status']=='OPERATIONS_INVENTORY' and inv['format']=='gzip-base64-json-filemap' and
        inv['read_only'] is True and type(inv['chunk_count']) is int and 0<inv['chunk_count']<=44 and
        inv['chunk_bytes']==12000 and 0<inv['compressed_bytes']<=512*1024 and
        inv['chunk_count']==(inv['compressed_bytes']+11999)//12000 and len(inv['files'])<=128,'SMALL008_LOG_INVENTORY')
    rows=g.old.unique(inv['files'],'name')
    g.require(sum(r['bytes'] for r in rows.values())<=16*1024*1024 and all(
        type(n) is str and Path(n).name==n and n not in ('.','..') and type(r['bytes']) is int and
        0<=r['bytes']<=4*1024*1024 for n,r in rows.items()),'SMALL008_LOG_NAMES_SIZE')
def decode(inv,chunks):
    inventory_ok(inv);g.require(len(chunks)==inv['chunk_count'],'SMALL008_LOG_COUNT');parts=[]
    for i,c in enumerate(chunks):
        raw=base64.b64decode(c['payload'],validate=True)
        g.require(c['part']==i and c['bytes']==len(raw) and len(raw)<=12000 and
            hashlib.sha256(raw).hexdigest()==c['sha256'] and c['combined_sha256']==inv['compressed_sha256'],'SMALL008_LOG_PART_HASH')
        parts.append(raw)
    raw=b''.join(parts)
    g.require(len(raw)==inv['compressed_bytes'] and hashlib.sha256(raw).hexdigest()==inv['compressed_sha256'],'SMALL008_LOG_COMBINED')
    # Bound decompression before JSON allocation, even when a remote log is bad.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
        unpacked=f.read(24*1024*1024+1)
    g.require(len(unpacked)<=24*1024*1024,'SMALL008_LOG_INFLATE')
    files=g.parse(unpacked);expected={r['name']:r for r in inv['files']}
    g.require(set(files)==set(expected),'SMALL008_LOG_FILES')
    for name,value in files.items():
        data=base64.b64decode(value,validate=True)
        g.require(len(data)==expected[name]['bytes'] and hashlib.sha256(data).hexdigest()==expected[name]['sha256'],'SMALL008_LOG_FILE_HASH')
    return files

COLLECT = "import base64,gzip,hashlib,json,pathlib,stat\np=pathlib.Path('/opt/mie/qualification-001-host-v1/operations')\nassert p.resolve()==p and p.is_dir()\nfiles={};inventory=[]\nfor f in sorted(p.iterdir()):\n assert f.is_file() and not f.is_symlink() and f.name not in ('.','..') and '/' not in f.name\n raw=f.read_bytes();assert len(raw)<=4*1024*1024\n inventory.append(dict(name=f.name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))\n files[f.name]=base64.b64encode(raw).decode()\nassert len(files)<=128 and sum(x['bytes'] for x in inventory)<=16*1024*1024\ncompressed=gzip.compress(json.dumps(files,sort_keys=True,separators=(',',':')).encode(),mtime=0)\nassert len(compressed)<=512*1024\nsha=hashlib.sha256(compressed).hexdigest()\nif PART is None:\n answer=dict(status='OPERATIONS_INVENTORY',files=inventory,format='gzip-base64-json-filemap',compressed_bytes=len(compressed),compressed_sha256=sha,chunk_count=(len(compressed)+11999)//12000,chunk_bytes=12000,read_only=True)\nelse:\n assert type(PART) is int and 0<=PART<(len(compressed)+11999)//12000\n raw=compressed[PART*12000:(PART+1)*12000]\n answer=dict(part=PART,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),combined_sha256=sha,payload=base64.b64encode(raw).decode(),read_only=True)\nencoded=json.dumps(answer,sort_keys=True);assert len(encoded)<23000\nprint(encoded)\n"
