"""Serve the common portable replays on a local-only comparison page."""

import argparse
import html
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware


def create_viewer(directory: Path, logs: Path | None = None) -> FastAPI:
    """Expose pre-rendered HTML files and, only when asked, top-level .eval downloads from one folder."""
    directory = directory.resolve()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """List locally rendered team replays without reading evaluation contents."""
        links = "".join(
            f'<li><a href="/{html.escape(path.name, quote=True)}">{html.escape(path.stem)}</a></li>'
            for path in sorted(directory.glob("*.html"))
            if path.is_file() and not path.is_symlink()
        )
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8"><title>Collaboration Index</title><style>body{font:16px system-ui;background:#f7faf7;color:#28372d;max-width:750px;margin:80px auto}a{color:#32694a}li{margin:20px 0}</style><h1>Collaboration Index</h1><p>One team · one computer · one evaluation. Open a replay to inspect the shared board, global messages, DMs and collective progress.</p><ul>'
            + links
            + "</ul>",
            headers=headers,
        )

    @app.get("/logs/{filename}")
    def native_log(filename: str) -> FileResponse:
        """Serve one ordinary top-level .eval file from the opted-in logs folder as a download."""
        # native logs can hold reference answers, so this route exists only with --logs
        path = (logs.resolve() / filename) if logs else None
        if (
            path is None
            or path.suffix != ".eval"
            or path.is_symlink()
            or path.parent != logs.resolve()  # type: ignore[union-attr]
            or not path.is_file()
        ):
            raise HTTPException(404, "Log not found")
        return FileResponse(
            path, media_type="application/zip", filename=path.name, headers=headers
        )

    @app.get("/{filename}")
    def replay(filename: str) -> FileResponse:
        """Allow only ordinary top-level HTML replay files inside the selected directory."""
        path = directory / filename
        if (
            path.suffix != ".html"
            or path.is_symlink()
            or path.parent != directory
            or not path.is_file()
        ):
            raise HTTPException(404, "Replay not found")
        return FileResponse(path, media_type="text/html", headers=headers)

    return app


def main() -> None:
    """Start a local replay chooser without public exposure or live model execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--port", type=int, default=14368)
    parser.add_argument(
        "--logs",
        type=Path,
        help="Also serve top-level .eval files from this folder for replay source links",
    )
    args = parser.parse_args()
    uvicorn.run(
        create_viewer(args.artifacts, args.logs),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
