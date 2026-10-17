"""mTLS request boundary candidate; requires an actual server-side TLS socket."""
import hashlib
import ssl
from app.research.phase6_receipt_http import ReceiptRequestDispatcher, _response
from app.research.phase6_anchor_validation import _hex


class ReceiptMtlsBoundary:
    def __init__(self,*,client_runs,append,lookup,head):
        # Exact leaf-certificate digest -> explicitly authorized run identities.
        if (type(client_runs) is not dict or not client_runs
                or any(not _hex(cert) or type(runs) not in (list,tuple) or not runs
                       or any(not _hex(run) for run in runs) for cert,runs in client_runs.items())):
            raise ValueError('Explicit certificate and run allowlist required')
        self._runs={cert:frozenset(runs) for cert,runs in client_runs.items()}
        self._dispatch=ReceiptRequestDispatcher(append=append,lookup=lookup,head=head)

    def handle(self,connection,*,method,path,body):
        try:
            if (type(connection) is not ssl.SSLSocket or not connection.server_side
                    or connection.context.verify_mode!=ssl.CERT_REQUIRED
                    or connection.version() not in ('TLSv1.2','TLSv1.3')):
                raise ValueError('Verified server TLS connection required')
            certificate=connection.getpeercert(binary_form=True)
            if not certificate:raise ValueError('Peer certificate required')
            allowed=self._runs[hashlib.sha256(certificate).hexdigest()]
        except Exception:
            return _response(403,{'error':'ACCESS_REFUSED','retry_allowed':False})
        if method!='POST':return _response(400,{'error':'REQUEST_REFUSED','retry_allowed':False})
        def authorize(route,payload):
            request=payload['request'] if route=='/lookup' else payload
            return request['run_id'] in allowed
        return self._dispatch.handle(path,body,authorize=authorize)
