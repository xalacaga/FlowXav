"""Point d'entrée natif : fenêtre Windows (pywebview / Edge WebView2) et service local.

Fermer la fenêtre arrête le service, rend la caméra à MSFS, ferme SimConnect,
libère le port et termine le processus.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request

from flowxav import __version__
from flowxav.logging_setup import configure_logging
from flowxav.paths import resource_path, user_data_path
from flowxav.server import DEFAULT_PORT, HOST, LAST_PORT, create_server

APP_USER_MODEL_ID = "Xalacaga.FlowXav"
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _message(text: str, icon: int) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, text, "FlowXav", icon)   # type: ignore[attr-defined]
    else:   # pragma: no cover
        print(text, file=sys.stderr)


def _webview2_version() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    for root, access in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
                         (winreg.HKEY_CURRENT_USER, winreg.KEY_READ)):
        try:
            with winreg.OpenKey(root, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}", 0, access) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0]).strip()
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return version
    return None


def _running_flowxav(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=0.8) as response:
            return json.load(response).get("application") == "FlowXav"
    except Exception:
        return False


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _select_port(preferred: int) -> tuple[int, bool]:
    """Le port à utiliser, et si une instance FlowXav l'occupe déjà."""
    if _running_flowxav(preferred):
        return preferred, True
    for port in dict.fromkeys([preferred, *range(DEFAULT_PORT, LAST_PORT + 1)]):
        if _port_available(port):
            return port, False
    raise RuntimeError(f"Aucun port local libre entre {DEFAULT_PORT} et {LAST_PORT}.")


def _wait_until_ready(port: int, timeout_s: float = 12.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _running_flowxav(port):
            return
        time.sleep(0.1)
    raise RuntimeError("Le service local FlowXav n'a pas démarré à temps.")


def _run_window(url: str, port: int, server) -> None:
    if sys.platform == "win32" and _webview2_version() is None:
        raise RuntimeError("Microsoft WebView2 est absent. Relancez l'installateur FlowXav : il l'installera.")
    import webview

    server_thread = threading.Thread(target=server.run, name="flowxav-serveur", daemon=False)
    server_thread.start()
    try:
        _wait_until_ready(port)
        window = webview.create_window("FlowXav", url, width=1120, height=780, min_size=(720, 560),
                                       background_color="#101418")
        window.events.closed += lambda: setattr(server, "should_exit", True)
        webview.start(gui="edgechromium", debug=False, private_mode=False,
                      storage_path=str(user_data_path("webview")),
                      icon=str(resource_path("assets", "flowxav.ico")))
    finally:
        server.should_exit = True
        server_thread.join(timeout=4)
        if server_thread.is_alive():
            server.force_exit = True
            server_thread.join(timeout=2)


def _run_smoke(port: int, server) -> None:
    """Démarre puis arrête réellement le service, pour tester un exécutable distribué."""
    server_thread = threading.Thread(target=server.run, name="flowxav-smoke-server", daemon=False)
    server_thread.start()
    try:
        _wait_until_ready(port)
    finally:
        server.should_exit = True
        server_thread.join(timeout=6)
        if server_thread.is_alive():
            server.force_exit = True
            server_thread.join(timeout=2)
    if server_thread.is_alive():
        raise RuntimeError("Le service FlowXav ne s'est pas arrêté après le smoke test.")


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="FlowXav")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="service sans fenêtre, pour le diagnostic")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    log_file = configure_logging()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)   # type: ignore[attr-defined]
        except Exception:
            logging.warning("Identité Windows FlowXav non définie")
    try:
        port, already_running = _select_port(args.port)
        if already_running:
            if not args.no_open:
                _message("FlowXav est déjà ouvert.", 0x40)
            return 0
        url = f"http://{HOST}:{port}"
        logging.info("Démarrage de FlowXav %s sur %s", __version__, url)
        server = create_server(HOST, port)
        if args.smoke_test:
            _run_smoke(port, server)
        elif args.no_open:
            server.run()
        else:
            _run_window(url, port, server)
        logging.info("Arrêt normal, port %s libéré", port)
        if not args.no_open and sys.platform == "win32":
            logging.shutdown()
            os._exit(0)
        return 0
    except Exception as exc:   # noqa: BLE001 - dernier rempart de l'application
        logging.critical("Erreur fatale\n%s", traceback.format_exc())
        _message(f"FlowXav n'a pas pu démarrer.\n\n{exc}\n\nJournal : {log_file}", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
