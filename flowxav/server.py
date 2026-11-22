"""Service local FlowXav : API JSON et interface, uniquement sur 127.0.0.1."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from flowxav import __version__
from flowxav.aircraft import list_profiles, load_profile
from flowxav.guide import Guide
from flowxav.paths import resource_path
from flowxav.simulator import Simulator

LOGGER = logging.getLogger(__name__)
HOST = "127.0.0.1"
DEFAULT_PORT = 8790
LAST_PORT = 8799
STATIC = resource_path("flowxav", "web", "static")


class StartRequest(BaseModel):
    profil: str
    procedure: str
    langue: str = "fr"


class PreviewRequest(BaseModel):
    profil: str
    commande: str


def create_app(simulator: Simulator | None = None, guide: Guide | None = None) -> FastAPI:
    simulator = simulator or Simulator()
    guide = guide or Guide(simulator)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        simulator.start()
        guide.start_thread()
        yield
        guide.stop()
        guide.close()
        simulator.close()
        LOGGER.info("Services FlowXav arrêtés")

    app = FastAPI(title="FlowXav", version=__version__, lifespan=lifespan)
    app.state.simulator, app.state.guide = simulator, guide

    def profile_or_404(profile_id: str):
        try:
            return load_profile(profile_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"version": __version__, "application": "FlowXav"}

    @app.get("/api/etat")
    def state() -> dict[str, Any]:
        return {"simulateur": simulator.snapshot().public(), "guide": guide.status()}

    @app.get("/api/profils")
    def profiles() -> list[dict[str, Any]]:
        return [profile.summary() for profile in list_profiles()]

    @app.get("/api/profils/{profile_id}/commandes")
    def controls(profile_id: str) -> list[dict[str, Any]]:
        profile = profile_or_404(profile_id)
        return [{"id": key, "libelle": c.get("libelle", key), "vue": c["vue"]}
                for key, c in profile.carte["commandes"].items()]

    @app.post("/api/guide/demarrer")
    def start(request: StartRequest) -> dict[str, Any]:
        profile = profile_or_404(request.profil)
        try:
            guide.start(profile, request.procedure, request.langue)
        except KeyError as error:
            raise HTTPException(404, f"Procédure inconnue : {error}") from error
        return guide.status()

    @app.post("/api/guide/fait")
    def confirm() -> dict[str, Any]:
        guide.confirm()
        return guide.status()

    @app.post("/api/guide/suivante")
    def next_step() -> dict[str, Any]:
        guide.go(+1)
        return guide.status()

    @app.post("/api/guide/precedente")
    def previous_step() -> dict[str, Any]:
        guide.go(-1)
        return guide.status()

    @app.post("/api/guide/arreter")
    def stop() -> dict[str, Any]:
        guide.stop()
        return guide.status()

    @app.post("/api/montrer")
    def preview(request: PreviewRequest) -> dict[str, Any]:
        profile = profile_or_404(request.profil)
        if request.commande not in profile.carte["commandes"]:
            raise HTTPException(404, f"Commande inconnue : {request.commande}")
        guide.preview(profile, request.commande)
        return guide.status()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_server(host: str = HOST, port: int = DEFAULT_PORT):
    import uvicorn

    config = uvicorn.Config(create_app(), host=host, port=port, log_level="warning", log_config=None)
    return uvicorn.Server(config)
