"""Strict Function URL event boundary. AWS_IAM deployment is required upstream."""
import json
from app.research.phase6_anchor_validation import _snapshot, _hex
from app.research.phase6_receipt_request import validate_append_request


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def _reject_number(value):
    raise ValueError('Noninteger JSON number')


def _response(code, data):
    return {'statusCode': code, 'headers': {'content-type': 'application/json', 'cache-control': 'no-store'},
            'isBase64Encoded': False, 'body': json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)}


class ReceiptHttpBoundary:
    """Dispatch only trusted AWS-generated IAM events to service-owned callbacks.

    This check does NOT authenticate arbitrary JSON. Deployment must enforce an
    AWS_IAM URL and forbid untrusted direct Lambda invocation or event injection.
    Exact approved principal ARNs are supplied in deployment configuration.
    """
    def __init__(self, *, account_id, domain_name, principal_arns, append, lookup, head):
        if (type(account_id) is not str or len(account_id) != 12 or not account_id.isdigit()
                or type(domain_name) is not str or not domain_name.endswith('.on.aws')
                or not principal_arns or any(type(x) is not str or not x.startswith('arn:aws:') or x.endswith(':root') for x in principal_arns)):
            raise ValueError('Explicit non-root endpoint identity required')
        self.account, self.domain, self.principals = account_id, domain_name, frozenset(principal_arns)
        self.handlers = {'/append': append, '/lookup': lookup, '/head': head}

    def handle(self, event):
        try:
            context = event['requestContext']
            iam = context['authorizer']['iam']
            if (context['accountId'] != self.account or context['domainName'] != self.domain
                    or iam['userArn'] not in self.principals or event['version'] != '2.0'):
                raise ValueError('Unauthorized')
        except (KeyError, TypeError, ValueError):
            return _response(403, {'error': 'ACCESS_REFUSED', 'retry_allowed': False})
        try:
            path=event['rawPath'];body=event['body']
            if (context['http']['method']!='POST' or context['http']['path']!=path
                    or event['isBase64Encoded'] is not False or event.get('rawQueryString','')!=''):
                raise ValueError('Invalid transport envelope')
        except (KeyError,TypeError,ValueError):
            return _response(400,{'error':'REQUEST_REFUSED','retry_allowed':False})
        return ReceiptRequestDispatcher(**{k[1:]:v for k,v in self.handlers.items()}).handle(path,body)


class ReceiptRequestDispatcher:
    """Schema/operation boundary only. Authentication belongs to trusted ingress."""
    def __init__(self,*,append,lookup,head):
        self.handlers={'/append':append,'/lookup':lookup,'/head':head}

    def handle(self,path,body,*,authorize=None):
        try:
            if (type(path) is not str or path not in self.handlers or type(body) is not str
                    or not body.isascii() or not 0<len(body)<=16384):
                raise ValueError('Invalid request')
            payload = _snapshot(json.loads(body, object_pairs_hook=_pairs,
                parse_float=_reject_number, parse_constant=_reject_number))
            if path == '/append':
                parsed = validate_append_request(payload)
                if parsed['status'] != 'VALID':
                    raise ValueError('Invalid append')
                payload = parsed['request']
            elif path == '/lookup':
                if type(payload) is not dict or set(payload) != {'request','nonce'} or not _hex(payload['nonce']):
                    raise ValueError('Invalid lookup')
                parsed = validate_append_request(payload['request'])
                if parsed['status'] != 'VALID':
                    raise ValueError('Invalid lookup request')
                payload['request'] = parsed['request']
            elif type(payload) is not dict or set(payload) != {'run_id','nonce'} or not all(_hex(v) for v in payload.values()):
                raise ValueError('Invalid head request')
        except Exception:
            return _response(400, {'error': 'REQUEST_REFUSED', 'retry_allowed': False})
        if authorize is not None:
            try:allowed=authorize(path,payload) is True
            except Exception:allowed=False
            if not allowed:return _response(403,{'error':'ACCESS_REFUSED','retry_allowed':False})
        try:
            result = self.handlers[path](payload)
            return _response(200, result)
        except Exception:
            # Append might have committed; never expose internal secrets or retry it.
            return _response(409, {'error': 'OPERATION_UNCONFIRMED', 'retry_allowed': False})
