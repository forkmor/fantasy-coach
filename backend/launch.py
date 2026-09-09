import logging
import os
import secrets
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from app.main import create_app


def main():
    token = secrets.token_urlsafe(32)
    port = int(os.environ.get("HARNESS_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    app = create_app(launch_token=token)
    print(f"\nFantasy Football Harness\nLocal launch link (keep private): {url}/#token={token}\n", flush=True)

    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(f"{url}/#token={token}")
                        return
            except (urllib.error.URLError, TimeoutError):
                threading.Event().wait(0.5)
        logging.getLogger("harness").error("Browser launch timed out; inspect startup errors")

    if os.environ.get("HARNESS_NO_BROWSER") != "1":
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
