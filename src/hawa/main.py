"""FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from hawa import __version__, settings_store
from hawa.api import health_router, router
from hawa.config import get_config
from hawa.db import get_session, init_db
from hawa.models import PollenReading

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"

DESCRIPTION = """
Open pollen and air-quality data for **Islamabad**.

The Pakistan Meteorological Department publishes daily sector-wise pollen counts
for H-8, E-8, G-6 and F-10, but only as a JavaScript-rendered page -- there is no
API, no history you can query, and nothing to build an alert on. This service
scrapes that page, keeps the history, joins it with community air-quality
sensors, and serves the result as a plain REST API that anyone can use.

Independent open-source project. **Not** an official PMD service, and not
medical advice.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    settings_store.refresh(force=True)
    from hawa.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


def create_app() -> FastAPI:
    config = get_config()
    app = FastAPI(
        title="Hawa - Islamabad Pollen & Air Quality API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        license_info={"name": "MIT"},
    )

    origins = [o.strip() for o in config.cors_allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(health_router)

    static_dir = WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request, session: Session = Depends(get_session)):
        newest = session.exec(
            select(col(PollenReading.observed_date)).order_by(
                col(PollenReading.observed_date).desc()
            )
        ).first()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "version": __version__,
                "has_data": newest is not None,
                "newest_date": newest.isoformat() if newest else None,
                "sectors": settings_store.get("sectors"),
            },
        )

    @app.get("/livez", include_in_schema=False)
    def livez() -> JSONResponse:
        """Process liveness only. Deliberately separate from /healthz.

        Container orchestrators restart on a failed liveness probe, and a
        restart does not fix an upstream that changed its markup -- so data
        health must not be wired to the thing that reboots the process.
        """
        return JSONResponse({"status": "alive", "version": __version__})

    return app


app = create_app()
