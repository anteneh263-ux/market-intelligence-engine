"""One local entry point; no installation, migration or research execution."""
import argparse
import json
import threading
import time
import urllib.request
import webbrowser
import uvicorn


def main():
    parser = argparse.ArgumentParser(description='Start MIEs lokale arbeidsflate')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    url = 'http://127.0.0.1:8765'
    try:
        with urllib.request.urlopen(url+'/api/status',timeout=1) as response:
            existing=json.load(response)
    except Exception:
        existing=None
    if existing and existing.get('app_id') == 'mie-local-workspace':
        print('MIE kjører allerede: '+url)
        if not args.no_browser: webbrowser.open(url)
        return
    def show():
        for _ in range(40):
            time.sleep(.25)
            try:
                with urllib.request.urlopen(url+'/api/status',timeout=1) as response:
                    if json.load(response).get('app_id')=='mie-local-workspace':
                        webbrowser.open(url);return
            except Exception: pass
    if not args.no_browser: threading.Thread(target=show,daemon=True).start()
    print('MIE: '+url+' — Stopp med Ctrl+C i dette vinduet.')
    uvicorn.run('app.workspace:app',host='127.0.0.1',port=8765,access_log=False)


if __name__=='__main__': main()
