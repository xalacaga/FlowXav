"""Service local FlowXav : API JSON et interface, uniquement sur 127.0.0.1.

Le second écran (flowxav.companion) réutilise cette application en mode « distant » :
autre en-tête Host autorisé (l'adresse du PC dans le réseau local), appairage par code,
cookie de session dédié, et ni identifiant SimBrief ni remise à zéro de l'avion.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from flowxav import __version__
from flowxav.aircraft import list_profiles, load_profile
from flowxav.guide import Guide, journey, suggest
from flowxav.paths import resource_path
from flowxav import depart, licence, simbrief
from flowxav.sim.simconnect import SimConnectClient, SimConnectError
from flowxav.simulator import Simulator

LOGGER = logging.getLogger(__name__)
HOST = "127.0.0.1"
DEFAULT_PORT = 8790
LAST_PORT = 8799
STATIC = resource_path("flowxav", "web", "static")


def versioned_index() -> str:
    """index.html dont chaque /static/fichier porte l'empreinte de son contenu (?v=…).

    Une WebView peut garder un ancien app.js en cache et le juger encore frais :
    avec une adresse qui change à chaque modification, elle charge toujours le bon.
    """
    def stamp(match: re.Match) -> str:
        path = STATIC / match.group(2)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return match.group(0)
        return f'{match.group(1)}/static/{match.group(2)}?v={digest}"'
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return re.sub(r'((?:src|href)=")/static/([A-Za-z0-9_.-]+)"', stamp, html)


SESSION_COOKIE = "flowxav_session"
REMOTE_COOKIE = "flowxav_ecran"
SAFE_HOSTS = {"127.0.0.1", "localhost"}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                                "form-action 'self'"),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StartRequest(StrictRequest):
    profil: str = Field(min_length=1, max_length=80)
    procedure: str = Field(min_length=1, max_length=120)
    langue: str = Field(default="fr", pattern=r"^[a-z]{2,8}$")


class AutoRequest(StrictRequest):
    actif: bool


class ProfileRequest(StrictRequest):
    profil: str = Field(min_length=1, max_length=80)
    etat: str | None = Field(default=None, max_length=80)


class SimbriefRequest(StrictRequest):
    profil: str = Field(min_length=1, max_length=80)
    identifiant: str = Field(min_length=1, max_length=128)


class PairingRequest(StrictRequest):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class ScreenRequest(StrictRequest):
    actif: bool


class PreviewRequest(StrictRequest):
    profil: str = Field(min_length=1, max_length=80)
    commande: str = Field(min_length=1, max_length=160)


def create_app(simulator: Simulator | None = None, guide: Guide | None = None, remote=None) -> FastAPI:
    """Application FlowXav ; `remote` (un flowxav.companion.Companion) la construit pour le second écran."""
    simulator = simulator or Simulator()
    guide = guide or Guide(simulator)
    local = remote is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not local:                          # second écran : services partagés, gérés par l'application locale
            yield
            return
        simulator.start()
        guide.start_thread()
        try:
            yield
        finally:
            companion = getattr(_app.state, "companion", None)
            if companion is not None and companion.active:
                companion.stop()
            guide.stop()
            guide.close()
            simulator.close()
            LOGGER.info("Services FlowXav arrêtés")

    app = FastAPI(title="FlowXav", version=__version__, lifespan=lifespan, max_body_size=64 * 1024,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.simulator, app.state.guide = simulator, guide
    app.state.session_token = secrets.token_urlsafe(32)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        # Réponse volontairement générique : ne reflète ni entrée personnelle, ni NaN non sérialisable.
        return JSONResponse({"detail": "requête invalide"}, status_code=422)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        """Refuse DNS rebinding/CSRF et pose les en-têtes de confinement du WebView."""
        host_header = request.headers.get("host", "")
        hostname = host_header.rsplit(":", 1)[0].lower() if ":" in host_header else host_header.lower()
        allowed = SAFE_HOSTS if local else {remote.host}
        if hostname not in allowed:
            response: Response = JSONResponse({"detail": "hôte local refusé"}, status_code=400)
        elif not local and request.url.path.startswith("/api/") and request.url.path not in {"/api/status", "/api/appairage"} \
                and not remote.pairing.valid(request.cookies.get(REMOTE_COOKIE, "")):
            response = JSONResponse({"detail": "appairage requis"}, status_code=401)
        else:
            origin = request.headers.get("origin")
            expected_origin = f"http://{host_header}"
            if origin and origin.rstrip("/") != expected_origin:
                response = JSONResponse({"detail": "origine refusée"}, status_code=403)
            elif (local and request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}
                  and not secrets.compare_digest(request.cookies.get(SESSION_COOKIE, ""), app.state.session_token)):
                response = JSONResponse({"detail": "session locale requise"}, status_code=403)
            else:
                response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            # Toujours revalider (ETag) : un ancien app.js resté en cache avec un nouvel index.html casse l'interface.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def profile_or_404(profile_id: str):
        try:
            return load_profile(profile_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"version": __version__, "application": "FlowXav"}

    procedures_cache: dict[str, list[dict[str, Any]]] = {}

    @app.get("/api/etat")
    def state() -> dict[str, Any]:
        snapshot = simulator.snapshot()
        proposal, statuses = None, {}
        if snapshot.profile_id and snapshot.source_ok:
            if snapshot.profile_id not in procedures_cache:
                procedures_cache[snapshot.profile_id] = load_profile(snapshot.profile_id).procedures()
            procedures = procedures_cache[snapshot.profile_id]
            proposal, statuses = suggest(procedures, snapshot.values), journey(procedures, snapshot.values)
        return {"simulateur": snapshot.public(), "guide": guide.status(), "suggestion": proposal, "parcours": statuses,
                "distant": not local}

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

    @app.post("/api/guide/faire")
    def do_step() -> dict[str, Any]:
        """« Fais-le pour moi » : sur clic de l'utilisateur, FlowXav manœuvre la commande de l'étape."""
        return {**guide.do_step(), "etat": guide.status()}

    @app.post("/api/guide/auto")
    def auto_mode(request: AutoRequest) -> dict[str, Any]:
        guide.set_auto(request.actif)
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

    def depart_call(profile_id: str, action):
        profile = profile_or_404(profile_id)
        if not profile.data.get("depart"):
            raise HTTPException(400, detail={"code": "non_pris_en_charge"})
        try:
            with SimConnectClient("FlowXav depart") as client:
                return action(client, profile)
        except depart.DepartError as error:
            LOGGER.info("État de départ : %s", error.code)
            raise HTTPException(400, detail={"code": error.code, "ecran": error.screen}) from error
        except SimConnectError as error:
            raise HTTPException(400, detail={"code": "simulateur", "detail": str(error)}) from error

    @app.post("/api/depart/verifier")
    def depart_verify(request: ProfileRequest) -> dict[str, Any]:
        return {"verifications": depart_call(request.profil, depart.verify)}

    @app.post("/api/depart/reinitialiser")
    def depart_reset(request: ProfileRequest) -> dict[str, Any]:
        """Sur clic et confirmation de l'utilisateur : charge l'état de départ par le CDU, puis vérifie."""
        if not local:
            raise HTTPException(403, detail={"code": "reserve_au_pc"})   # second écran : pas sur un appareil distant
        if not licence.accepted(profile_or_404(request.profil)):
            raise HTTPException(403, detail={"code": "licence"})
        guide.stop()
        result = depart_call(request.profil, lambda client, profile: depart.reset(client, profile, request.etat))
        reset_flight = getattr(simulator, "reset_flight", None)
        if callable(reset_flight):
            reset_flight()                     # nouveau vol : la phase repart de « parking »
        return result

    @app.get("/api/simbrief")
    def simbrief_settings() -> dict[str, Any]:
        if not local:
            raise HTTPException(403, detail={"code": "reserve_au_pc"})   # second écran : pas sur un appareil distant
        return {"identifiant": simbrief.load_identifier()}

    @app.post("/api/simbrief/importer")
    def simbrief_import(request: SimbriefRequest) -> dict[str, Any]:
        """Sur clic de l'utilisateur seulement : jamais d'interrogation automatique de SimBrief."""
        if not local:
            raise HTTPException(403, detail={"code": "reserve_au_pc"})   # second écran : pas sur un appareil distant
        profile = profile_or_404(request.profil)
        config = profile.data.get("plan_de_vol")
        if not config:
            raise HTTPException(400, detail={"code": "non_pris_en_charge"})
        identifier = request.identifiant.strip()
        simbrief.save_identifier(identifier)
        try:
            result = simbrief.import_latest(identifier, config)
        except simbrief.SimbriefError as error:
            LOGGER.info("Import SimBrief refusé : %s", error.code)
            raise HTTPException(400, detail={"code": error.code, "detail": error.detail}) from error
        return {**result, "fmc": config.get("fmc", "")}

    # ------------------------------------------------------------ contrat de licence de l'éditeur
    if local:
        @app.get("/api/licence/{profile_id}")
        def licence_status(profile_id: str) -> dict[str, Any]:
            return licence.status(profile_or_404(profile_id))

        @app.post("/api/licence/accepter")
        def licence_accept(request: ProfileRequest) -> dict[str, Any]:
            """Acceptation manuelle, sur le PC : case cochée puis bouton."""
            LOGGER.info("Contrat de licence accepté pour %s", request.profil)
            return licence.accept(profile_or_404(request.profil))

    # ------------------------------------------------------------ second écran
    if local:
        from flowxav.companion import Companion
        app.state.companion = Companion(simulator, guide)

        @app.get("/api/ecran")
        def screen_status() -> dict[str, Any]:
            return app.state.companion.status()

        @app.post("/api/ecran")
        def screen_switch(request: ScreenRequest) -> dict[str, Any]:
            """Sur clic de l'utilisateur, depuis le PC seulement : ouvre ou ferme le second écran."""
            try:
                return app.state.companion.start() if request.actif else app.state.companion.stop()
            except RuntimeError as error:
                raise HTTPException(400, detail={"code": str(error)}) from error
    else:
        @app.post("/api/appairage")
        def pair(request: PairingRequest) -> JSONResponse:
            token = remote.pairing.pair(request.code)
            if token is None:
                return JSONResponse({"detail": "code refusé"}, status_code=403)
            response = JSONResponse({"ok": True})
            response.set_cookie(REMOTE_COOKIE, token, httponly=True, samesite="strict", path="/api", max_age=12 * 3600)
            LOGGER.info("Second écran : appareil appairé")
            return response

    @app.get("/")
    def index() -> HTMLResponse:
        response = HTMLResponse(versioned_index())
        if local:
            response.set_cookie(SESSION_COOKIE, app.state.session_token, httponly=True, samesite="strict",
                                path="/api", max_age=86400)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_server(host: str = HOST, port: int = DEFAULT_PORT):
    import uvicorn

    config = uvicorn.Config(create_app(), host=host, port=port, log_level="warning", log_config=None)
    return uvicorn.Server(config)
