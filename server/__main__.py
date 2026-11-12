"""Start the VaaniSetu AI web app: ``python -m server``."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from config.settings import get_settings


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the VaaniSetu AI web app.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically.")
    parser.add_argument("--reload", action="store_true", help="Restart on code changes (development).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    if not settings.openai_enabled:
        print("Note: OPENAI_API_KEY is not set in .env, so the assistant won't be able to answer yet.")
    if not (settings.frontend_dist / "index.html").exists():
        print("The web interface hasn't been built yet. Run:  cd frontend && npm install && npm run build")
        print(f"The API is still available at {url}/api/docs")
    else:
        print(f"VaaniSetu AI is starting at {url}")
        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("server.main:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
