"""Bounded loopback tunnel connection lifecycle; one request, no reconnect."""
import socket
import ssl
import sys
import time
from app.research.phase6_receipt_wire import exchange,serve_one,need,_host


def _close(resource):
    failed=sys.exc_info()[0] is not None
    if resource is not None:
        try:resource.close()
        except BaseException:
            if not failed:raise


def request_once(*,port,context,hostname,server_cert_sha256,path,body):
    """Connect only to the local authenticated tunnel; verify remote TLS identity."""
    need(type(port) is int and 1<=port<=65535)
    _host(hostname)
    need(type(context) is ssl.SSLContext and context.check_hostname
         and context.verify_mode==ssl.CERT_REQUIRED)
    connection=None;deadline=time.monotonic()+10
    try:
        connection=socket.create_connection(('127.0.0.1',port),timeout=10)
        remaining=deadline-time.monotonic();need(remaining>0);connection.settimeout(remaining)
        connection=context.wrap_socket(connection,server_hostname=hostname)
        need(time.monotonic()<=deadline)
        return exchange(connection,expected_hostname=hostname,server_cert_sha256=server_cert_sha256,path=path,body=body)
    finally:_close(connection)


def accept_once(listener,*,context,boundary,hostname,idle_timeout_seconds=120):
    """Runs callbacks in caller thread. Listener retained; accepted socket owned."""
    _host(hostname)
    need(type(listener) is socket.socket and listener.family==socket.AF_INET
         and listener.getsockname()[0]=='127.0.0.1')
    need(type(context) is ssl.SSLContext and context.verify_mode==ssl.CERT_REQUIRED
         and context.minimum_version>=ssl.TLSVersion.TLSv1_2)
    need(type(idle_timeout_seconds) is int and 1<=idle_timeout_seconds<=120)
    connection=None
    try:
        # Waiting between one-minute bars is distinct from a peer's handshake.
        listener.settimeout(idle_timeout_seconds);connection,_=listener.accept()
        deadline=time.monotonic()+10;connection.settimeout(10)
        connection=context.wrap_socket(connection,server_side=True)
        need(time.monotonic()<=deadline)
        return serve_one(connection,boundary=boundary,expected_host=hostname)
    finally:_close(connection)
