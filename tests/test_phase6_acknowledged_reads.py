import json
from pathlib import Path
import pytest
from test_phase6_genesis_admission import prepare
from app.research.phase6_object_store import ObjectWriter


def test_journal_read_is_detached_and_does_not_release_owner(tmp_path):
    j,w,p,q,pa,s,ss=prepare(tmp_path)
    digest=j.snapshot()['event_head_hash'];event=j.read_object(digest)
    event['kind']='forged'
    assert j.read_object(digest)['kind']=='BAR_ACCEPTED'
    with pytest.raises(ValueError):w.read(digest)
    assert j.snapshot()['lifecycle']=='OPEN'
    j.close()
    with pytest.raises(ValueError):j.read_object(digest)


@pytest.mark.parametrize('fault',['tamper','symlink','root_swap','extra','header'])
def test_acknowledged_read_refuses_changed_store(tmp_path,fault):
    j,w,p,q,pa,s,ss=prepare(tmp_path);digest=j.snapshot()['event_head_hash'];root=Path(w.root);target=root/(digest+'.blob')
    if fault=='tamper':target.chmod(0o600);target.write_bytes(b'x'*target.stat().st_size)
    if fault=='symlink':target.unlink();target.symlink_to(root/'writer.json')
    if fault=='root_swap':root.rename(tmp_path/'moved');root.mkdir()
    if fault=='extra':(root/'unexpected').write_bytes(b'x')
    if fault=='header':(root/'writer.json').chmod(0o600);(root/'writer.json').write_text('{}')
    with pytest.raises((ValueError,OSError)):j.read_object(digest)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_unknown_hash_does_not_read_arbitrary_object(tmp_path):
    j,w,p,q,pa,s,ss=prepare(tmp_path)
    with pytest.raises(ValueError):j.read_object('f'*64)
    assert j.snapshot()['lifecycle']=='OPEN';j.close()


def test_swallowed_read_reentry_does_not_acknowledge(tmp_path,monkeypatch):
    j,w,p,q,pa,s,ss=prepare(tmp_path);digest=j.snapshot()['event_head_hash'];original=w.read
    def reenter(*a,**kw):
        raw=original(*a,**kw)
        with pytest.raises(ValueError):j.read_object(digest)
        return raw
    monkeypatch.setattr(w,'read',reenter)
    with pytest.raises(ValueError):j.read_object(digest)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'
