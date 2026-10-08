import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=2) as response:
            existing = json.load(response)
        if existing.get("application") == "imagen-a-historia":
            print("La app ya está abierta: " + url)
            if not args.no_browser:
                webbrowser.open(url)
            sys.exit(0)
    except (OSError, ValueError):
        pass
    if not args.no_browser:
        timer = threading.Timer(1.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        access_log=False,
    )
