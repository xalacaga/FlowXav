"""CDU du PMDG 737 : lire l'écran et appuyer sur les touches par le SDK.

L'écran est publié dans la zone ClientData « PMDG_NG3_CDU_0 » (CDU gauche) si
737_Options.ini contient EnableCDUBroadcast.0=1 dans sa section [SDK] :
24 colonnes × 14 lignes de cellules (symbole, couleur, attributs), rangées
colonne par colonne, puis un octet « alimenté ». Les touches sont des
événements tiers « #<69632 + n> » envoyés avec un clic gauche puis son
relâchement, comme un clic de souris.

Ligne 0 : titre ; la touche latérale Lk/Rk est en face de la ligne 2k ;
ligne 13 : bloc-notes.
"""

from __future__ import annotations

import time
from typing import Iterable

COLUMNS, ROWS = 24, 14
SCREEN_SIZE = COLUMNS * ROWS * 3 + 1
THIRD_PARTY_EVENT_ID_MIN = 69632
MOUSE_LEFT_SINGLE, MOUSE_LEFT_RELEASE = 0x20000000, 0x00020000

# Numéros PMDG_NG3_SDK.h (EVT_CDU_L_* = THIRD_PARTY_EVENT_ID_MIN + n).
KEYS_LEFT = {**{f"L{i}": 533 + i for i in range(1, 7)}, **{f"R{i}": 539 + i for i in range(1, 7)},
             "INIT_REF": 546, "RTE": 547, "MENU": 551, "LEGS": 552, "DEP_ARR": 553, "EXEC": 556,
             "PREV_PAGE": 559, "NEXT_PAGE": 560, "CLR": 602}


def decode_screen(raw: bytes) -> tuple[bool, list[str]]:
    """(alimenté, 14 lignes de texte) depuis la zone brute."""
    lines = []
    for row in range(ROWS):
        chars = []
        for column in range(COLUMNS):
            symbol = raw[(column * ROWS + row) * 3]
            chars.append(chr(symbol) if 32 <= symbol < 127 else " ")
        lines.append("".join(chars))
    return bool(raw[SCREEN_SIZE - 1]), lines


def find_key(lines: list[str], text: str, whole_word: bool = False) -> str | None:
    """Touche latérale (« L5 », « R2 »…) en face du texte cherché, ou None.

    Le texte est cherché sur la ligne de la touche (2k) et sur sa ligne
    d'en-tête (2k − 1), sans tenir compte de la casse. Le côté se déduit de la
    position du texte : moitié gauche → L, moitié droite → R.
    """
    wanted = text.upper()
    for k in range(1, 7):
        for row in (2 * k, 2 * k - 1):
            line = lines[row].upper()
            index = _locate(line, wanted, whole_word)
            if index is not None:
                return _side(line, index, len(wanted)) + str(k)
        # Invite écrite sur deux lignes : « PMDG » en en-tête, « SETUP> » en face de la touche.
        for header, data in _halves(lines[2 * k - 1].upper(), lines[2 * k].upper()):
            joined = f"{header} {data}".strip()
            if header and data and _locate(joined, wanted, whole_word) is not None:
                return ("R" if data.endswith(">") or not data.startswith("<") and _right_half(lines[2 * k], data)
                        else "L") + str(k)
    return None


def _halves(header: str, data: str) -> list[tuple[str, str]]:
    """Moitiés gauche et droite de l'en-tête et de la ligne de touche, débarrassées des blancs."""
    middle = COLUMNS // 2
    return [(header[:middle].strip(), data[:middle].strip()), (header[middle:].strip(), data[middle:].strip())]


def _right_half(line: str, data: str) -> bool:
    return line.upper().find(data) >= COLUMNS // 2


def _side(line: str, index: int, length: int) -> str:
    """Côté de la touche : une invite « …> » est à droite, « <… » à gauche, sinon selon la position."""
    if line[index + length:].lstrip().startswith(">"):
        return "R"
    if line[:index].rstrip().endswith("<"):
        return "L"
    return "L" if index + length / 2 < COLUMNS / 2 else "R"


def _locate(line: str, wanted: str, whole_word: bool) -> int | None:
    start = line.find(wanted)
    while start >= 0:
        end = start + len(wanted)
        before = line[start - 1] if start > 0 else " "
        after = line[end] if end < len(line) else " "
        if not whole_word or (not before.isalnum() and not after.isalnum()):
            return start
        start = line.find(wanted, start + 1)
    return None


class PmdgCdu:
    def __init__(self, client, area: str = "PMDG_NG3_CDU_0", keys: dict[str, int] | None = None) -> None:
        self.client, self.area, self.keys = client, area, keys or KEYS_LEFT

    def screen(self) -> tuple[bool, list[str]]:
        return decode_screen(self.client.read_client_data(self.area, SCREEN_SIZE))

    def press(self, key: str) -> None:
        event = f"#{THIRD_PARTY_EVENT_ID_MIN + self.keys[key]}"
        self.client.transmit_event(event, MOUSE_LEFT_SINGLE)
        time.sleep(0.05)
        self.client.transmit_event(event, MOUSE_LEFT_RELEASE)

    def press_and_wait(self, key: str, timeout_s: float = 2.5) -> list[str]:
        """Appuie et attend que l'écran change (titre ou contenu)."""
        _powered, before = self.screen()
        self.press(key)
        deadline = time.monotonic() + timeout_s
        lines = before
        while time.monotonic() < deadline:
            time.sleep(0.15)
            _powered, lines = self.screen()
            if lines != before:
                time.sleep(0.2)            # laisse la page finir de s'afficher
                return self.screen()[1]
        return lines


def text_of(lines: Iterable[str]) -> str:
    return "\n".join(line.rstrip() for line in lines)
