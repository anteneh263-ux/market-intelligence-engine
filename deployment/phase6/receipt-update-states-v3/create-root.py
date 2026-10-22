"""Create ONLY the separately approved attempt006 local root; never call AWS.

Call only after the user approves the exact review/pins and new root/window.
--approval-text records their actual response; it does not obtain authorization.
No repair, deletion, reuse or cleanup of an existing/partial root is supported.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import context as m
require=m.live.control.require


def create(review_file,review_sha,pins_file,pins_sha,approval_text):
    require(__debug__ and type(approval_text) is str and bool(approval_text.strip()))
    review=m.live.read_regular(Path(review_file),1024*1024)
    pins_raw=m.live.read_regular(Path(pins_file),1024*1024)
    require(hashlib.sha256(review).hexdigest()==review_sha and hashlib.sha256(pins_raw).hexdigest()==pins_sha)
    pins=m.v2.parse(pins_raw)
    require(pins['attempt']=='006' and pins['control_version']==m.live.progress.VERSION and pins['evidence_root']==str(m.ROOT))
    require(pins['commit']==subprocess.check_output(['git','rev-parse','HEAD'],cwd=m.WORK,text=True).strip())
    require(not subprocess.check_output(['git','status','--porcelain'],cwd=m.WORK).strip())
    require(all('deployment/phase6/receipt-update-states-v3/'+n in pins['files'] for n in m.REQUIRED_SOURCES))
    for name,expected in pins['files'].items():
        require(type(name) is str and not name.startswith('/') and '..' not in Path(name).parts)
        raw=m.live.read_regular(m.WORK/name,8*1024*1024)
        require(hashlib.sha256(raw).hexdigest()==expected)
    require(str(m.ROOT).encode() in review and pins['commit'].encode() in review and pins_sha.encode() in review)
    require(m.ROOT.parent.resolve(strict=True)==m.ROOT.parent and not m.ROOT.exists() and not m.ROOT.is_symlink())
    authorization=dict(owner='Anteneh Hailu',approved=True,attempt='006',control_version=m.live.progress.VERSION,
        evidence_root=str(m.ROOT),source_commit=pins['commit'],source_pins_sha256=pins_sha,review_sha256=review_sha,
        user_response_verbatim=approval_text,recorded_utc=m.datetime.now(m.timezone.utc).isoformat(),
        scope='One approved attempt006; one frozen45minute window; exact receipt only; source stays off-small; no pilot',
        partial_root_must_not_be_reused=True)
    os.umask(0o077);m.ROOT.mkdir(mode=0o700)
    (m.ROOT/'operator').mkdir(mode=0o700);(m.ROOT/'operator-scripts').mkdir(mode=0o700)
    # All validation above precedes creation. Any subsequent IO error leaves an honest
    # partial root; never remove/reconstruct it or retry this creation automatically.
    m.write('authorization.json',authorization)
    m.write('EXECUTION-REVIEW.md',review);m.write('approved-source-pins.json',pins_raw)
    for name in m.REQUIRED_SOURCES:
        m.write('operator-scripts/'+name,(HERE/name).read_bytes())
    fd=os.open(m.ROOT,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    m.pins()
    return dict(status='APPROVED_LOCAL_ROOT_CREATED',evidence_root=str(m.ROOT),aws_calls=0,window_started=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('review-file','review-sha256','pins-file','pins-sha256','approval-text'):p.add_argument('--'+name,required=True)
    p.add_argument('--execute-approved',action='store_true',required=True)
    a=p.parse_args(argv)
    print(json.dumps(create(a.review_file,a.review_sha256,a.pins_file,a.pins_sha256,a.approval_text),sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
