import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from app import workspace as W


def request(body=b'',host='127.0.0.1:8765',origin=None,method='POST'):
    headers=[(b'host',host.encode())]
    if origin: headers.append((b'origin',origin.encode()))
    async def receive(): return {'type':'http.request','body':body,'more_body':False}
    return Request({'type':'http','method':method,'path':'/api/import-pine','headers':headers},receive)


class WorkspaceTests(unittest.TestCase):
    def test_catalogue_is_explicit_and_excludes_protected_data(self):
        self.assertEqual(len(W.catalogue()),25)
        self.assertNotIn('H-025',W.catalogue())
        with self.assertRaises(HTTPException): W.load_item('../../.env')
        with self.assertRaises(HTTPException): W.load_item('H-025')

    def test_symlink_and_outside_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'actual').write_text('x');(root/'link').symlink_to(root/'actual')
            with patch.object(W,'ROOT',root):
                for p in ('link','../outside','holdout/data','H-025.json'):
                    with self.subTest(path=p),self.assertRaises(ValueError): W.safe_file(p)

    def test_report_hashes_and_version(self):
        import hashlib
        result=W.load_item('H-009')
        self.assertEqual(result['engine'],'srp-matrix-v2')
        self.assertEqual(result['sha256'],hashlib.sha256((W.ROOT/result['source']).read_bytes()).hexdigest())
        self.assertFalse(W.status()['can_run_research'])
        self.assertFalse(W.status()['can_trade'])

    def test_missing_report_is_visible_not_silently_counted(self):
        with patch.object(W,'catalogue',return_value={'missing':('absent.json',None,'strategy','v2')}):
            result=W.list_items()
        self.assertEqual(result['items'],[])
        self.assertEqual(result['issues'][0]['id'],'missing')

    def test_import_interprets_without_executing(self):
        text='strategy("Demo"); x = ta.rsi(close,14)'
        result=asyncio.run(W.parse_pine(request(json.dumps({'text':text}).encode())))
        self.assertIn('RSI',result['detected'])
        self.assertTrue(result['interpretation_only'])
        unknown=asyncio.run(W.parse_pine(request(b'{"text":"unknown contents"}')))
        self.assertIsNone(unknown['recipe'])

    def test_bad_and_oversize_input_rejected(self):
        for body,code in [(b'{}',400),(b'[]',400),(b'{"text":2}',400),(b'x'*65537,413)]:
            with self.subTest(code=code), self.assertRaises(HTTPException) as caught:
                asyncio.run(W.parse_pine(request(body)))
            self.assertEqual(caught.exception.status_code,code)

    def test_foreign_origin_and_host_blocked(self):
        async def next_handler(req): return Response('ok')
        for req in [request(host='evil.example'),request(origin='https://evil.example')]:
            result=asyncio.run(W.local_security(req,next_handler))
            self.assertEqual(result.status_code,403)
        result=asyncio.run(W.local_security(request(origin='http://127.0.0.1:8765'),next_handler))
        self.assertEqual(result.status_code,200)
        self.assertIn("script-src 'self'",result.headers['content-security-policy'])

    def test_no_research_or_trading_routes_exposed(self):
        paths={r.path for r in W.app.routes}
        self.assertNotIn('/api/pipeline/run',paths)
        self.assertNotIn('/api/engine/evaluate',paths)
        self.assertEqual({r.path for r in W.app.routes if 'POST' in (getattr(r,'methods',None) or [])},{'/api/import-pine'})


if __name__=='__main__': unittest.main()
