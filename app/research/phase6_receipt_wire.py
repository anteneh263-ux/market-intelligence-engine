"""One request per verified TLS connection; no retry or connection reuse."""
import hashlib
import re
import ssl
import sys
import time
from app.research.phase6_anchor_validation import _hex


def need(ok):
    if not ok:raise ValueError('RECEIPT_WIRE_REFUSED')


class _IO:
    def __init__(self,connection):
        self.connection=connection;self.deadline=time.monotonic()+10
    def ready(self):
        remaining=self.deadline-time.monotonic();need(remaining>0)
        self.connection.settimeout(remaining)
    def read(self,n):
        result=bytearray()
        while len(result)<n:
            self.ready();block=self.connection.recv(n-len(result));need(bool(block));result.extend(block)
        self.ready();return bytes(result)
    def header(self):
        data=bytearray()
        while not data.endswith(b'\r\n\r\n'):
            need(len(data)<8192);data.extend(self.read(1))
        lines=data[:-4].decode('ascii').split('\r\n');fields={}
        for line in lines[1:]:
            key,sep,value=line.partition(':')
            need(sep==':' and re.fullmatch(r'[A-Za-z0-9-]+',key) is not None)
            key=key.lower();need(key not in fields)
            value=value.strip(' ');need(all(32<=ord(c)<127 for c in value))
            fields[key]=value
        return lines[0],fields
    def send(self,data):self.ready();self.connection.sendall(data);self.ready()


def _length(fields,maximum):
    value=fields.get('content-length','')
    need(re.fullmatch(r'[1-9][0-9]{0,6}',value) is not None)
    size=int(value);need(size<=maximum);return size


def _host(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9.-]{1,253}',value) is not None)


def serve_one(connection,*,boundary,expected_host):
    """Caller supplies accepted TLS socket in the service clock's owner thread."""
    try:
        _host(expected_host)
        need(type(connection) is ssl.SSLSocket and connection.server_side
             and connection.context.verify_mode==ssl.CERT_REQUIRED)
        io=_IO(connection);start,fields=io.header()
        parts=start.split(' ')
        need(len(parts)==3 and parts[0]=='POST' and parts[1] in ('/append','/lookup','/head') and parts[2]=='HTTP/1.1')
        need(set(fields)=={'host','content-type','content-length','connection'})
        need(fields['host']==expected_host and fields['content-type']=='application/json' and fields['connection']=='close')
        body=io.read(_length(fields,16384)).decode('ascii')
        response=boundary.handle(connection,method='POST',path=parts[1],body=body)
        payload=response['body'].encode('ascii');code=response['statusCode']
        need(type(code) is int and code in (200,400,403,409) and 0<len(payload)<=1048576)
        header=f'HTTP/1.1 {code} Result\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode('ascii')
        io.send(header+payload)
    finally:
        failed=sys.exc_info()[0] is not None
        try:connection.close()
        except BaseException:
            if not failed:raise


def exchange(connection,*,expected_hostname,server_cert_sha256,path,body):
    """No retries. Any transport error leaves append outcome unconfirmed."""
    try:
        _host(expected_hostname)
        need(_hex(server_cert_sha256) and type(connection) is ssl.SSLSocket
             and not connection.server_side and connection.context.verify_mode==ssl.CERT_REQUIRED
             and connection.context.check_hostname and connection.server_hostname==expected_hostname
             and connection.version() in ('TLSv1.2','TLSv1.3'))
        cert=connection.getpeercert(binary_form=True)
        need(bool(cert) and hashlib.sha256(cert).hexdigest()==server_cert_sha256)
        need(path in ('/append','/lookup','/head') and type(body) is str and body.isascii() and 0<len(body)<=16384)
        io=_IO(connection)
        header=f'POST {path} HTTP/1.1\r\nHost: {expected_hostname}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'
        io.send((header+body).encode('ascii'))
        start,fields=io.header();parts=start.split(' ',2)
        need(len(parts)==3 and parts[0]=='HTTP/1.1' and parts[1] in ('200','400','403','409') and parts[2]=='Result')
        need(set(fields)=={'content-type','content-length','connection'} and fields['content-type']=='application/json' and fields['connection']=='close')
        raw=io.read(_length(fields,1048576)).decode('ascii')
        # Return untouched JSON for the existing receipt/signature validators.
        # HTTP200 is not evidence of a verified or committed receipt.
        return dict(status_code=int(parts[1]),body=raw,receipt_verified=False,retry_allowed=False)
    finally:
        failed=sys.exc_info()[0] is not None
        try:connection.close()
        except BaseException:
            if not failed:raise
