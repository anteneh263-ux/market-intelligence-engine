"""Start the local archive app; refuse reuse of a different checkout or source version."""
import argparse
import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import uvicorn
from app.workspace import WORKSPACE_IDENTITY


def compatible_server(existing, expected):
    return (isinstance(existing, dict)
            and existing.get('app_id') == 'mie-local-workspace'
            and existing.get('workspace_identity') == expected)


def main():
    parser = argparse.ArgumentParser(description='Start MIEs lokale arbeidsflate')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check', action='store_true', help='Vis valgt arbeidsmappe og motor uten serverstart')
    args = parser.parse_args()
    if args.check:
        print(json.dumps(WORKSPACE_IDENTITY, ensure_ascii=False))
        return
    url = 'http://127.0.0.1:8765'
    try:
        with urllib.request.urlopen(url + '/api/status', timeout=1) as response:
            existing = json.load(response)
    except (OSError, ValueError):
        existing = None
    if existing is not None:
        if not compatible_server(existing, WORKSPACE_IDENTITY):
            raise SystemExit('En annen eller eldre arbeidsflate bruker port 8765. '
                             'Stopp den fra terminalen der den kjører, og start MIE på nytt.')
        print('MIE kjører allerede fra riktig arbeidsmappe: ' + url)
        if not args.no_browser:
            webbrowser.open(url)
        return

    def show():
        for _ in range(40):
            time.sleep(.25)
            try:
                with urllib.request.urlopen(url + '/api/status', timeout=1) as response:
                    if compatible_server(json.load(response), WORKSPACE_IDENTITY):
                        webbrowser.open(url)
                        return
            except (OSError, ValueError):
                pass
    if not args.no_browser:
        threading.Thread(target=show, daemon=True).start()
    print('MIE: ' + url + ' — Stopp med Ctrl+C i dette vinduet.')
    uvicorn.run('app.workspace:app', host='127.0.0.1', port=8765, access_log=False)


if __name__ == '__main__':
    main()
