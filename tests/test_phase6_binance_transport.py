import pytest
pytest.importorskip("websockets", reason="Install the phase6-source extra")
from test_phase6_clockbound_intake import binding,Native
from test_phase6_binance_source import raw,message
from app.research import phase6_binance_transport as module


class Socket:
    def __init__(self,frames):self.frames=iter(frames);self.sent=[];self.closed=False;self.reads=0
    def send(self,value):self.sent.append(value)
    def recv(self,**kwargs):
        assert kwargs['decode'] is False and 0<kwargs['timeout']<=10
        self.reads+=1;v=next(self.frames)
        if isinstance(v,BaseException):raise v
        return v
    def close(self):self.closed=True


def setup(tmp_path,monkeypatch,frames):
    intake,root=binding(tmp_path,monkeypatch)
    monkeypatch.setattr(module,'ClockBoundSourceIntake',lambda *a,**kw:intake)
    sock=Socket(frames);connections=[]
    def connect():connections.append(True);return sock
    monkeypatch.setattr(module,'_connect',connect)
    owner=module.BinanceClockBoundTransport(None,plan={},reservation={},library_path='test',library_sha256='test')
    return owner,intake,root,sock,connections


def test_raw_frame_to_native_intake_and_callback(tmp_path,monkeypatch):
    b=raw(message());owner,intake,root,sock,connections=setup(tmp_path,monkeypatch,[b'{"result":null,"id":1}',b])
    seen=[];out=owner.run(max_messages=1,duration_seconds=10,on_commit=seen.append)
    assert len(seen)==1 and out['messages']==1 and not out['runtime_accepted']
    assert (root/(seen[0]['commit']['raw_payload_hash']+'.blob')).read_bytes()==b
    assert sock.sent==[module.SUBSCRIBE] and sock.closed and Native.instances[0].closed
    with pytest.raises(ValueError):owner.run(max_messages=1,duration_seconds=10,on_commit=seen.append)
    assert connections==[True]


@pytest.mark.parametrize('fault',['bad_ack','timeout','bad_frame','callback','reentry','close_reentry'])
def test_failure_never_reconnects_and_closes_resources(tmp_path,monkeypatch,fault):
    ack=b'{"result":null,"id":true}' if fault=='bad_ack' else b'{"result":null,"id":1}'
    frame=TimeoutError('idle') if fault=='timeout' else b'{}' if fault=='bad_frame' else raw(message())
    owner,intake,root,sock,connections=setup(tmp_path,monkeypatch,[ack,frame])
    def callback(result):
        if fault=='callback':raise RuntimeError('lost downstream ack')
        if fault in ('reentry','close_reentry'):
            try:owner.close() if fault=='close_reentry' else owner.run(max_messages=1,duration_seconds=10,on_commit=callback)
            except ValueError:pass
    with pytest.raises((ValueError,RuntimeError,TimeoutError)):
        owner.run(max_messages=1,duration_seconds=10,on_commit=callback)
    assert owner.stopped and intake.stopped and sock.closed and Native.instances[0].closed
    with pytest.raises(ValueError):owner.run(max_messages=1,duration_seconds=10,on_commit=callback)
    assert len(connections)==1


def test_pinned_connection_settings(monkeypatch):
    import websockets
    import websockets.sync.client
    calls=[]
    monkeypatch.setattr(websockets.sync.client,'connect',lambda *a,**kw:calls.append((a,kw)))
    monkeypatch.setattr(websockets,'__version__',module.VERSION)
    module._connect();args,kw=calls[0]
    assert args==(module.ENDPOINT,) and kw['proxy'] is None and kw['compression'] is None
    assert kw['max_size']==65536 and kw['max_queue']==1
    assert kw['ssl'].check_hostname and kw['ssl'].verify_mode==module.ssl.CERT_REQUIRED
    monkeypatch.setattr(websockets,'__version__','unreviewed')
    with pytest.raises(ValueError):module._connect()
    assert len(calls)==1


@pytest.mark.parametrize('fault',['connect','subscribe','ack','cleanup'])
def test_setup_and_cleanup_errors_preserve_primary_failure(tmp_path,monkeypatch,fault):
    owner,intake,root,sock,connections=setup(tmp_path,monkeypatch,[b'{"result":null,"id":1}',raw(message())])
    def fail(*a,**kw):raise OSError('primary')
    if fault=='connect':monkeypatch.setattr(module,'_connect',fail)
    elif fault=='subscribe':monkeypatch.setattr(sock,'send',fail)
    elif fault=='ack':monkeypatch.setattr(sock,'recv',fail)
    else:monkeypatch.setattr(sock,'close',fail)
    with pytest.raises(OSError,match='primary'):
        owner.run(max_messages=1,duration_seconds=10,on_commit=lambda _:None)
    assert intake.stopped and Native.instances[0].closed and owner.stopped


def test_deadline_overrun_preserves_received_message_without_callback(tmp_path,monkeypatch):
    b=raw(message());owner,intake,root,sock,connections=setup(tmp_path,monkeypatch,[b'{"result":null,"id":1}',b])
    values=iter([0,0,11]);monkeypatch.setattr(module.time,'monotonic',lambda:next(values))
    seen=[]
    with pytest.raises(ValueError):owner.run(max_messages=1,duration_seconds=10,on_commit=seen.append)
    assert not seen and intake._intake.packet_count==1 and sock.closed


def test_actual_loopback_websocket_to_durable_intake(tmp_path,monkeypatch):
    from threading import Thread
    from websockets.sync.server import serve
    from websockets.sync.client import connect
    intake,root=binding(tmp_path,monkeypatch)
    monkeypatch.setattr(module,'ClockBoundSourceIntake',lambda *a,**kw:intake)
    b=raw(message());received=[]
    def handler(ws):
        received.append(ws.recv());ws.send('{"result":null,"id":1}');ws.send(b.decode('ascii'))
        try:ws.recv()
        except Exception:pass
    with serve(handler,'127.0.0.1',0) as server:
        thread=Thread(target=server.serve_forever);thread.start()
        try:
            port=server.socket.getsockname()[1]
            monkeypatch.setattr(module,'_connect',lambda:connect(f'ws://127.0.0.1:{port}',proxy=None,compression=None,max_size=65536,max_queue=1))
            owner=module.BinanceClockBoundTransport(None,plan={},reservation={},library_path='synthetic',library_sha256='synthetic')
            seen=[];owner.run(max_messages=1,duration_seconds=10,on_commit=seen.append)
            assert received==[module.SUBSCRIBE]
            assert (root/(seen[0]['commit']['raw_payload_hash']+'.blob')).read_bytes()==b
        finally:server.shutdown();thread.join(timeout=5)
        assert not thread.is_alive()


def test_callback_cannot_rewrite_reported_durable_head(tmp_path,monkeypatch):
    owner,intake,root,sock,connections=setup(tmp_path,monkeypatch,[b'{"result":null,"id":1}',raw(message())])
    actual=[]
    def mutate(result):
        actual.append(result['commit_hash']);result['commit_hash']='f'*64
    result=owner.run(max_messages=1,duration_seconds=10,on_commit=mutate)
    assert result['last_commit_hash']==actual[0]
    assert (root/(result['last_commit_hash']+'.blob')).is_file()
