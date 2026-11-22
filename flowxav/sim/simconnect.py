"""Client SimConnect de FlowXav, en ctypes, sur la DLL officielle du SDK MSFS 2024.

FlowXav n'a besoin que de trois choses du simulateur :
- lire des variables de l'avion (SimVars) et son titre, pour reconnaître l'appareil ;
- lire les zones *ClientData* qu'un avion tiers publie (SDK PMDG…) ;
- piloter la caméra pour montrer une commande.

La DLL n'est jamais installée dans le système : elle voyage avec FlowXav et
parle au service SimConnect fourni par MSFS.
"""

from __future__ import annotations

import ctypes as ct
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from flowxav.paths import resource_path

RECV_ID_EXCEPTION = 1
RECV_ID_QUIT = 3
RECV_ID_SIMOBJECT_DATA = 8
RECV_ID_CLIENT_DATA = 16
RECV_ID_CAMERA_DATA = 40
RECV_ID_CAMERA_STATUS = 41

HEADER_SIZE = 12                       # SIMCONNECT_RECV : taille, version, identifiant
_PAYLOAD_OFFSET = (3 + 7) * 4          # en-tête + 7 DWORD des réponses de données

DATATYPE_FLOAT64 = 4
DATATYPE_STRING256 = 9
PERIOD_ONCE = 1
OBJECT_ID_USER = 0
SIMCONNECT_UNUSED = 0xFFFFFFFF
SIMCONNECT_OPEN_CONFIGINDEX_LOCAL = 0xFFFFFFFF
SIMCONNECT_GROUP_PRIORITY_HIGHEST = 1
SIMCONNECT_EVENT_FLAG_GROUPID_IS_PRIORITY = 0x00000010

MSFS2024_REQUIRED_EXPORTS = ("SimConnect_AICreateNonATCAircraft_EX1", "SimConnect_EnumerateSimObjectsAndLiveries")
CAMERA_EXPORTS = ("SimConnect_CameraAcquire", "SimConnect_CameraRelease", "SimConnect_CameraSet",
                  "SimConnect_CameraGet", "SimConnect_SubscribeToCameraStatusUpdate")

CAMERA_REFERENTIAL_SIMOBJECT = 1
CAMERA_MASK_POSITION = 1
CAMERA_MASK_ROTATION = 2
CAMERA_MASK_FOV = 8
EXCEPTION_CAMERA_API = 46
# SIMCONNECT_DATA_CAMERA compactée sur 84 octets : position (3 double),
# référentiel et objet, cible (3 double), trois angles (3 float), référentiel et
# objet de la rotation, champ de vision (double, en radians).
CAMERA_LAYOUT = struct.Struct("<3diI3d3fiId")

EXCEPTION_NAMES = {
    1: "ERROR", 2: "SIZE_MISMATCH", 3: "UNRECOGNIZED_ID", 4: "UNOPENED", 5: "VERSION_MISMATCH",
    7: "NAME_UNRECOGNIZED", 18: "INVALID_DATA_TYPE", 19: "INVALID_DATA_SIZE", 20: "DATA_ERROR",
    21: "INVALID_ARRAY", EXCEPTION_CAMERA_API: "CAMERA_API",
}
EXCEPTION_GRACE_S = 0.25


class SimConnectError(RuntimeError):
    """Le simulateur est absent, ou refuse la demande."""


class SimConnectRefused(SimConnectError):
    """Le simulateur a répondu, en refusant la demande."""

    def __init__(self, message: str, codes: Sequence[int] = ()) -> None:
        super().__init__(message)
        self.codes = tuple(codes)


@dataclass(frozen=True)
class Pose:
    """Vue de la caméra dans le repère de l'avion.

    `position` en mètres (x vers la droite, y vers le haut, z vers l'avant) ;
    `pitch` en degrés, NÉGATIF vers le haut ; `heading` en degrés, positif vers
    la droite ; `fov` en radians.

    Piège établi en vol : CameraGet renvoie les angles dans l'ordre
    (tangage, cap, inclinaison) alors que CameraSet attend (tangage,
    inclinaison, cap). Cette classe est le seul endroit qui en tient compte.
    """

    position: tuple[float, float, float]
    pitch: float
    heading: float
    bank: float
    fov: float

    @classmethod
    def from_get(cls, values: Sequence[float]) -> "Pose":
        """Depuis la charge CAMERA_LAYOUT d'une réponse CameraGet."""
        return cls(tuple(values[0:3]), values[8], values[9], values[10], values[13])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pose":
        """Depuis le format des fichiers de vue : pbh = (tangage, cap, inclinaison), comme CameraGet."""
        pitch, heading, bank = data["pbh"]
        return cls(tuple(data["position"]), pitch, heading, bank, data["fov"])

    def to_dict(self) -> dict[str, Any]:
        return {"position": [round(v, 4) for v in self.position],
                "pbh": [round(self.pitch, 4), round(self.heading, 4), round(self.bank, 4)],
                "fov": round(self.fov, 4)}

    def set_payload(self) -> bytes:
        """Charge de CameraSet : angles dans l'ordre (tangage, INCLINAISON, cap)."""
        return CAMERA_LAYOUT.pack(*self.position, CAMERA_REFERENTIAL_SIMOBJECT, OBJECT_ID_USER, 0.0, 0.0, 0.0,
                                  self.pitch, self.bank, self.heading,
                                  CAMERA_REFERENTIAL_SIMOBJECT, OBJECT_ID_USER, self.fov)


class _RECV(ct.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("dwVersion", wintypes.DWORD), ("dwID", wintypes.DWORD)]


class _RECV_EXCEPTION(ct.Structure):
    _fields_ = _RECV._fields_ + [("dwException", wintypes.DWORD), ("dwSendID", wintypes.DWORD),
                                 ("dwIndex", wintypes.DWORD)]


class _RECV_DATA(ct.Structure):
    _fields_ = _RECV._fields_ + [("dwRequestID", wintypes.DWORD), ("dwObjectID", wintypes.DWORD),
                                 ("dwDefineID", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                                 ("dwentrynumber", wintypes.DWORD), ("dwoutof", wintypes.DWORD),
                                 ("dwDefineCount", wintypes.DWORD)]


def dll_candidates() -> tuple[Path, ...]:
    candidates = [resource_path("SimConnect", "SimConnect.dll"), resource_path("SimConnect.dll")]
    sdk_root = os.getenv("MSFS2024_SDK", "").strip()
    if sdk_root:
        candidates.append(Path(sdk_root) / "SimConnect SDK" / "lib" / "SimConnect.dll")
    candidates.append(Path(r"C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll"))
    return tuple(candidates)


class SimConnectClient:
    """Une connexion au simulateur ; chaque fil de FlowXav ouvre la sienne."""

    def __init__(self, name: str = "FlowXav", dll_path: Path | str | None = None) -> None:
        self._dll = _load(dll_path)
        self._handle = ct.c_void_p()
        self._next_id = 1
        self._simvar_definitions: dict[tuple[tuple[str, str], ...], int] = {}
        self._string_definitions: dict[str, int] = {}
        self._client_data_definitions: dict[str, tuple[int, int]] = {}
        self._events: dict[str, int] = {}
        if self._dll.SimConnect_Open(ct.byref(self._handle), name.encode(), None, 0, None,
                                     SIMCONNECT_OPEN_CONFIGINDEX_LOCAL) != 0:
            raise SimConnectError("Microsoft Flight Simulator ne répond pas : lancez le simulateur et chargez un vol.")

    # ------------------------------------------------------------------ file de messages
    def messages(self) -> Iterator[tuple[int, bytes]]:
        """Vide la file SimConnect : (identifiant du message, octets bruts)."""
        pointer, size = ct.POINTER(_RECV)(), wintypes.DWORD()
        while self._dll.SimConnect_GetNextDispatch(self._handle, ct.byref(pointer), ct.byref(size)) == 0:
            recv = pointer.contents
            if recv.dwID == RECV_ID_QUIT:
                raise SimConnectError("Le simulateur s'est fermé.")
            yield recv.dwID, ct.string_at(pointer, recv.dwSize)

    def _wait(self, request_id: int, wanted: int, timeout_s: float, what: str) -> bytes:
        deadline = time.monotonic() + timeout_s
        exceptions: list[int] = []
        while time.monotonic() < deadline:
            received = False
            for recv_id, raw in self.messages():
                received = True
                if recv_id == wanted and struct.unpack_from("<I", raw, HEADER_SIZE)[0] == request_id:
                    return raw
                if recv_id == RECV_ID_EXCEPTION:
                    code = struct.unpack_from("<I", raw, HEADER_SIZE)[0]
                    if code not in exceptions:
                        exceptions.append(code)
                    deadline = min(deadline, time.monotonic() + EXCEPTION_GRACE_S)
            if not received:
                time.sleep(0.002)
        if exceptions:
            names = ", ".join(EXCEPTION_NAMES.get(code, str(code)) for code in exceptions)
            raise SimConnectRefused(f"SimConnect a refusé {what} ({names}).", exceptions)
        raise SimConnectError(f"Aucune réponse du simulateur pour {what}.")

    # ------------------------------------------------------------------ variables de l'avion
    def read_simvars(self, variables: Sequence[tuple[str, str]], timeout_s: float = 3.0) -> dict[str, float]:
        """Lit des SimVars de l'avion du joueur, toutes en FLOAT64 (un booléen vaut 0.0 ou 1.0)."""
        if not variables:
            return {}
        key = tuple(variables)
        definition_id = self._simvar_definitions.get(key)
        if definition_id is None:
            definition_id = self._new_id()
            for name, unit in variables:
                if self._dll.SimConnect_AddToDataDefinition(self._handle, definition_id, name.encode(), unit.encode(),
                                                            DATATYPE_FLOAT64, 0.0, SIMCONNECT_UNUSED) != 0:
                    self._dll.SimConnect_ClearDataDefinition(self._handle, definition_id)
                    raise SimConnectError(f"Variable refusée : {name} ({unit}).")
            self._simvar_definitions[key] = definition_id
        request_id = self._new_id()
        self._dll.SimConnect_RequestDataOnSimObject(self._handle, request_id, definition_id, OBJECT_ID_USER,
                                                    PERIOD_ONCE, 0, 0, 0, 0)
        try:
            raw = self._wait(request_id, RECV_ID_SIMOBJECT_DATA, timeout_s, "les variables de l'avion")
        except SimConnectRefused:
            self._simvar_definitions.pop(key, None)
            self._dll.SimConnect_ClearDataDefinition(self._handle, definition_id)
            raise
        values = struct.unpack_from(f"<{len(variables)}d", raw, _PAYLOAD_OFFSET)
        return {name: value for (name, _unit), value in zip(variables, values)}

    def read_title(self, timeout_s: float = 3.0) -> str:
        """Titre de l'avion chargé (SimVar TITLE), pour reconnaître le profil."""
        definition_id = self._string_definitions.get("TITLE")
        if definition_id is None:
            definition_id = self._new_id()
            if self._dll.SimConnect_AddToDataDefinition(self._handle, definition_id, b"TITLE", None,
                                                        DATATYPE_STRING256, 0.0, SIMCONNECT_UNUSED) != 0:
                raise SimConnectError("Impossible de lire le titre de l'avion.")
            self._string_definitions["TITLE"] = definition_id
        request_id = self._new_id()
        self._dll.SimConnect_RequestDataOnSimObject(self._handle, request_id, definition_id, OBJECT_ID_USER,
                                                    PERIOD_ONCE, 0, 0, 0, 0)
        raw = self._wait(request_id, RECV_ID_SIMOBJECT_DATA, timeout_s, "le titre de l'avion")
        return raw[_PAYLOAD_OFFSET:_PAYLOAD_OFFSET + 256].split(b"\0")[0].decode("utf-8", "replace")

    # ------------------------------------------------------------------ zones publiées par un avion tiers
    def read_client_data(self, name: str, size: int, timeout_s: float = 2.0) -> bytes:
        """Lit une zone ClientData nommée (ex. « PMDG_NG3_Data »). Une zone absente lève une erreur."""
        existing = self._client_data_definitions.get(name)
        if existing is None:
            area_id, definition_id = self._new_id(), self._new_id()
            if self._dll.SimConnect_MapClientDataNameToID(self._handle, name.encode(), area_id) != 0:
                raise SimConnectError(f"Zone {name} inconnue.")
            if self._dll.SimConnect_AddToClientDataDefinition(self._handle, definition_id, 0, size, 0.0,
                                                              SIMCONNECT_UNUSED) != 0:
                raise SimConnectError(f"Zone {name} : définition refusée.")
            existing = self._client_data_definitions[name] = (area_id, definition_id)
        area_id, definition_id = existing
        request_id = self._new_id()
        self._dll.SimConnect_RequestClientData(self._handle, area_id, request_id, definition_id, PERIOD_ONCE,
                                               0, 0, 0, 0)
        raw = self._wait(request_id, RECV_ID_CLIENT_DATA, timeout_s, f"la zone {name}")
        payload = raw[_PAYLOAD_OFFSET:]
        if len(payload) < size:
            raise SimConnectError(f"Zone {name} : {len(payload)} octets pour {size} attendus.")
        return payload[:size]

    # ------------------------------------------------------------------ événements
    def transmit_event(self, sim_event: str, data: int = 0) -> None:
        """Envoie un événement au simulateur ; « #69633 » désigne un événement tiers par son numéro."""
        event_id = self._events.get(sim_event)
        if event_id is None:
            event_id = self._new_id()
            if self._dll.SimConnect_MapClientEventToSimEvent(self._handle, event_id, sim_event.encode("ascii")) != 0:
                raise SimConnectError(f"Événement refusé : {sim_event}.")
            self._events[sim_event] = event_id
        if self._dll.SimConnect_TransmitClientEvent(self._handle, OBJECT_ID_USER, event_id, data & 0xFFFFFFFF,
                                                    SIMCONNECT_GROUP_PRIORITY_HIGHEST,
                                                    SIMCONNECT_EVENT_FLAG_GROUPID_IS_PRIORITY) != 0:
            raise SimConnectError(f"Envoi impossible : {sim_event}.")

    # ------------------------------------------------------------------ caméra
    @property
    def camera_supported(self) -> bool:
        return all(hasattr(self._dll, name) for name in CAMERA_EXPORTS)

    def _camera_call(self, name: str, *args: Any) -> None:
        if not self.camera_supported:
            raise SimConnectError("Cette DLL SimConnect ne porte pas l'API caméra de MSFS 2024.")
        if getattr(self._dll, name)(self._handle, *args) != 0:
            raise SimConnectError(f"Commande caméra refusée ({name}).")

    def camera_acquire(self, client_name: str = "FlowXav") -> None:
        self._camera_call("SimConnect_CameraAcquire", client_name.encode("ascii"))

    def camera_release(self, camera_definition: str = "Pilot") -> None:
        self._camera_call("SimConnect_CameraRelease", camera_definition.encode("ascii"))

    def camera_set(self, pose: Pose) -> None:
        buffer = ct.create_string_buffer(pose.set_payload(), CAMERA_LAYOUT.size)
        self._camera_call("SimConnect_CameraSet", buffer, CAMERA_MASK_POSITION | CAMERA_MASK_ROTATION | CAMERA_MASK_FOV)

    def camera_get(self, timeout_s: float = 3.0) -> Pose:
        """Vue actuelle du joueur dans le repère de l'avion."""
        self._camera_call("SimConnect_CameraGet", CAMERA_REFERENTIAL_SIMOBJECT)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for recv_id, raw in self.messages():
                if recv_id == RECV_ID_CAMERA_DATA:
                    return Pose.from_get(CAMERA_LAYOUT.unpack_from(raw, HEADER_SIZE))
            time.sleep(0.01)
        raise SimConnectError("Aucune réponse de la caméra.")

    # ------------------------------------------------------------------
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def close(self) -> None:
        if self._handle:
            self._dll.SimConnect_Close(self._handle)
            self._handle = ct.c_void_p()

    def __enter__(self) -> "SimConnectClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _load(dll_path: Path | str | None):
    candidates = [Path(dll_path)] if dll_path else list(dll_candidates())
    for path in candidates:
        if not path.is_file():
            continue
        dll = ct.WinDLL(str(path))
        if all(hasattr(dll, name) for name in MSFS2024_REQUIRED_EXPORTS):
            _declare(dll)
            return dll
    raise SimConnectError("SimConnect.dll de MSFS 2024 introuvable : installez le SDK MSFS 2024 à jour.")


def _declare(dll) -> None:
    """Signatures explicites, indispensables en 64 bits. Les structures passées
    « par valeur » (caméra) le sont par adresse : c_void_p reçoit le tampon."""
    signatures = [
        ("SimConnect_Open", [ct.POINTER(ct.c_void_p), ct.c_char_p, ct.c_void_p, ct.c_ulong, ct.c_void_p, ct.c_ulong]),
        ("SimConnect_GetNextDispatch", [ct.c_void_p, ct.POINTER(ct.POINTER(_RECV)), ct.POINTER(wintypes.DWORD)]),
        ("SimConnect_AddToDataDefinition",
         [ct.c_void_p, ct.c_ulong, ct.c_char_p, ct.c_char_p, ct.c_ulong, ct.c_float, ct.c_ulong]),
        ("SimConnect_RequestDataOnSimObject",
         [ct.c_void_p, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong]),
        ("SimConnect_ClearDataDefinition", [ct.c_void_p, ct.c_ulong]),
        ("SimConnect_MapClientDataNameToID", [ct.c_void_p, ct.c_char_p, ct.c_ulong]),
        ("SimConnect_AddToClientDataDefinition", [ct.c_void_p, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_float, ct.c_ulong]),
        ("SimConnect_RequestClientData",
         [ct.c_void_p, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong]),
        ("SimConnect_MapClientEventToSimEvent", [ct.c_void_p, ct.c_ulong, ct.c_char_p]),
        ("SimConnect_TransmitClientEvent", [ct.c_void_p, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong, ct.c_ulong]),
        ("SimConnect_Close", [ct.c_void_p]),
    ]
    camera = [
        ("SimConnect_CameraAcquire", [ct.c_void_p, ct.c_char_p]),
        ("SimConnect_CameraRelease", [ct.c_void_p, ct.c_char_p]),
        ("SimConnect_CameraSet", [ct.c_void_p, ct.c_void_p, ct.c_ulong]),
        ("SimConnect_CameraGet", [ct.c_void_p, ct.c_ulong]),
        ("SimConnect_SubscribeToCameraStatusUpdate", [ct.c_void_p]),
    ]
    signatures += [entry for entry in camera if hasattr(dll, entry[0])]
    for name, argtypes in signatures:
        function = getattr(dll, name)
        function.restype = ct.c_long
        function.argtypes = argtypes
