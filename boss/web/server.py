from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from boss.web.routes import create_routes


class SwarmLogHandler(logging.Handler):
    def __init__(self, swarm_manager) -> None:
        super().__init__()
        self.swarm_manager = swarm_manager

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self.swarm_manager.append_log(
                run_id=None,
                message=message,
                level=record.levelname.lower(),
                agent=record.name,
            )
        except Exception:
            return


def create_app(orchestrator) -> FastAPI:
    app = FastAPI(title="BOSS Command Center", version="0.1.0")
    app.state.orchestrator = orchestrator
    app.state.swarm_manager = orchestrator.swarm_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://localhost",
            "https://tauri.localhost",
        ],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ui_dir = Path(__file__).resolve().parent / "ui"
    app.mount("/ui", StaticFiles(directory=str(ui_dir)), name="ui")
    app.include_router(create_routes(orchestrator, orchestrator.swarm_manager))

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(
            ui_dir / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return app


def run_server(
    orchestrator,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> None:
    app = create_app(orchestrator)
    root_logger = logging.getLogger()
    if not any(isinstance(handler, SwarmLogHandler) for handler in root_logger.handlers):
        handler = SwarmLogHandler(orchestrator.swarm_manager)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(handler)

    url = f"http://{host}:{port}/"
    if open_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()

    uvicorn.run(app, host=host, port=port, log_level="info")
