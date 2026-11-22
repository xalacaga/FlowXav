"""Second écran : l'interface sur une tablette ou un téléphone du réseau local, sur demande.

Désactivé par défaut. Quand l'utilisateur l'active depuis FlowXav (sur le PC), un second
service démarre sur l'adresse du PC dans le réseau local. Il partage le guidage du service
principal mais il est cloisonné :

- il n'accepte que l'en-tête Host de cette adresse (pas de DNS rebinding) ;
- un code d'appairage à 6 chiffres, affiché sur le PC, ouvre une session (cookie dédié) ;
  après 5 codes faux, le code change et il faut le relire sur le PC ;
- il ne publie ni l'identifiant SimBrief ni la remise à zéro de l'avion ;
- il s'arrête quand l'utilisateur le désactive ou ferme FlowXav.
"""

from __future__ import annotations

import logging
import secrets
import socket
import threading
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)
FIRST_PORT, LAST_PORT = 8800, 8809
MAX_ATTEMPTS = 5


def lan_address() -> str | None:
    """Adresse IPv4 du PC dans le réseau local (aucun paquet n'est envoyé)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))        # adresse de documentation : sert seulement à choisir l'interface
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") else address


def free_port(host: str) -> int | None:
    for port in range(FIRST_PORT, LAST_PORT + 1):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((host, port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    return None


@dataclass
class Pairing:
    """Code à 6 chiffres et sessions ouvertes ; le code change après trop d'erreurs."""
    code: str = field(default_factory=lambda: f"{secrets.randbelow(10 ** 6):06d}")
    sessions: set[str] = field(default_factory=set)
    failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def pair(self, code: str) -> str | None:
        with self.lock:
            if secrets.compare_digest(code.strip(), self.code):
                token = secrets.token_urlsafe(32)
                self.sessions.add(token)
                self.failures = 0
                return token
            self.failures += 1
            if self.failures >= MAX_ATTEMPTS:
                self.code = f"{secrets.randbelow(10 ** 6):06d}"
                self.failures = 0
                LOGGER.warning("Second écran : trop de codes faux, nouveau code affiché sur le PC")
            return None

    def valid(self, token: str) -> bool:
        with self.lock:
            return any(secrets.compare_digest(token, known) for known in self.sessions) if token else False


class Companion:
    """Service du second écran, démarré et arrêté depuis l'interface locale."""

    def __init__(self, simulator, guide) -> None:
        self.simulator, self.guide = simulator, guide
        self.pairing = Pairing()
        self.host: str | None = None
        self.port: int | None = None
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        if not self.active:
            return {"actif": False}
        return {"actif": True, "url": f"http://{self.host}:{self.port}", "code": self.pairing.code,
                "appareils": len(self.pairing.sessions)}

    def start(self) -> dict:
        if self.active:
            return self.status()
        host = lan_address()
        if not host:
            raise RuntimeError("reseau_absent")
        port = free_port(host)
        if port is None:
            raise RuntimeError("port_occupe")
        import uvicorn
        from flowxav.server import create_app

        self.host, self.port, self.pairing = host, port, Pairing()
        app = create_app(self.simulator, self.guide, remote=self)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", log_config=None)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="flowxav-second-ecran", daemon=True)
        self._thread.start()
        LOGGER.info("Second écran activé sur le réseau local (port %d)", port)
        return self.status()

    def stop(self) -> dict:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=4)
        self._server = self._thread = None
        self.pairing = Pairing()                 # les appareils appairés devront se réappairer
        LOGGER.info("Second écran désactivé")
        return {"actif": False}
