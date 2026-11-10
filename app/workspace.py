"""Local daily workspace. Explicit read-only catalogue, no research/trading endpoints."""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.adapters.pine_import import import_pine
from app.research_core import versioning

ROOT = Path(__file__).resolve().parent.parent
UI = Path(__file__).with_name('workspace_ui')
app = FastAPI(title='MIE Arbeidsflate', docs_url=None, redoc_url=None, openapi_url=None)


def catalogue():
    items = {}
    for n in range(2, 25):
        key = f'H-{n:03d}'
        items[key] = (f'knowledge_base/results/{key}.srp-matrix-v2.json', None, 'strategy', 'srp-matrix-v2')
    items['btc-donchian'] = ('knowledge_base/studies/BTC_DONCHIAN_2026-10-27/SUMMARY.json',
                            'knowledge_base/studies/BTC_DONCHIAN_2026-10-27/REPORT.md', 'study', 'srp-matrix-v4')
    items['btc-patterns'] = ('knowledge_base/studies/BTC_CHART_PATTERNS_2026-10-27_v1_1/RESULTS.json',
                            'knowledge_base/studies/BTC_CHART_PATTERNS_2026-10-27_v1_1/OPPSUMMERING.md', 'study', 'mie-chart-patterns-v1.1')
    return items


def safe_file(relative):
    path = ROOT / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()) or any('holdout' in p.lower() or 'h-025' in p.lower() for p in resolved.parts):
        raise ValueError('Kilden er ikke tilgjengelig i arbeidsflaten.')
    # Refuse symlinks even within the root: the allowlist names the actual file.
    if resolved != path.absolute():
        raise ValueError('Symbolske lenker er ikke tillatt som rapportkilde.')
    return path


def load_item(key):
    entry = catalogue().get(key)
    if entry is None:
        raise HTTPException(404, 'Ukjent rapport')
    relative, report, kind, engine = entry
    path = safe_file(relative)
    raw = path.read_bytes()
    data = json.loads(raw)
    if kind == 'strategy':
        name, verdict = data.get('name', key), data.get('verdict', 'Ukjent')
        metrics = {'Handler': data.get('num_trades'), 'Maks fall': data.get('max_drawdown'),
                   'DSR': data.get('dsr')}
        reasons = data.get('verdict_reasons', [])
    elif key == 'btc-donchian':
        name, verdict = 'BTC Donchian 20/10', data['4h']['verdict']
        h = data['4h']['headline']
        metrics = {'Nettoavkastning': h['total_return'], 'Maks fall': h['max_drawdown'], 'Handler': h['num_trades']}
        reasons = data['4h'].get('reasons', [])
    else:
        name, verdict = 'BTC chartmønstre · fem regler', 'Ingen kvalifiserte'
        metrics = {'Regler': len(data['rules'])}
        reasons = ['Ingen kvalifiserte kandidater i den lagrede oppsummeringen. Eksperimentell motorutvidelse.']
    text = safe_file(report).read_text() if report else '\n'.join(reasons)
    return {'id': key, 'name': name, 'kind': kind, 'engine': engine, 'verdict': verdict,
            'metrics': metrics, 'reasons': reasons, 'report': text, 'data': data,
            'source': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
            'report_source': report, 'report_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'historical': True}


@app.middleware('http')
async def local_security(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail': 'Kun lokal tilgang'}, status_code=403)
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        if origin and origin != f'http://{request.headers.get("host")}':
            return JSONResponse({'detail': 'Ugyldig opprinnelse'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/status')
def status():
    return {'app_id': 'mie-local-workspace', 'mode': 'Les eksisterende forskning og beskriv strategier',
            'active_engine': versioning.ACTIVE_VERSION, 'versions': versioning.list_versions(),
            'can_run_research': False, 'can_trade': False,
            'scope': 'Arkiv H-002–H-024 (v2) og to eksplisitt valgte BTC-studier. Ikke hele forskningsarkivet.',
            'version_notice': 'Denne arbeidsmappen tilbyr v2. Den lagrede Donchian-studien brukte v4 fra en annen kodeversjon. Den kjøres ikke på nytt her.'}


@app.get('/api/catalogue')
def list_items():
    items, issues = [], []
    for key in catalogue():
        try:
            item = load_item(key)
            items.append({k: v for k, v in item.items() if k not in ('data', 'report')})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({'id': key, 'message': f'Kilden kunne ikke leses ({type(exc).__name__}).'})
    return {'items': items, 'issues': issues}


@app.get('/api/reports/{key}')
def detail(key: str):
    try:
        return load_item(key)
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(422, 'Rapportkilden mangler eller har ugyldig format')


@app.get('/api/reports/{key}/download')
def download(key: str):
    item = detail(key)
    return JSONResponse(item, headers={'Content-Disposition': f'attachment; filename="{item["id"]}-report.json"'})


@app.get('/api/pine-example')
def example():
    return {'text': safe_file('imports/bb_rsi_meanrev.pine').read_text()}


@app.post('/api/import-pine')
async def parse_pine(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, 'Maksimum 64 KB tekst')
    try:
        data = json.loads(body)
        text = data['text']
        if not isinstance(text, str) or not text.strip():
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, 'Legg inn Pine-kode som tekst')
    result = asdict(import_pine(text))
    result['interpretation_only'] = True
    result['notice'] = 'Forenklet indikatorgjenkjenning, ikke full Pine-kompilering. Koden kjøres ikke, lagres ikke og testes ikke mot markedet.'
    return result


@app.get('/')
def home():
    return FileResponse(UI / 'index.html')

app.mount('/assets', StaticFiles(directory=UI), name='assets')
