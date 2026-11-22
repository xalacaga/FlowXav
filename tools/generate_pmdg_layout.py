"""Génère la disposition d'une zone de données PMDG depuis l'en-tête C de son SDK.

    python tools/generate_pmdg_layout.py <en-tête.h> <structure> <sortie.json>
    ex. : "D:\\MSFS2024\\Community\\pmdg-aircraft-739\\Documentation\\SDK\\PMDG_NG3_SDK.h" PMDG_NG3_Data
          flowxav/aircraft/pmdg_737_900/pmdg_ng3_layout.json

Alignement naturel de MSVC (empaquetage par défaut) : chaque champ est aligné
sur sa taille, la structure sur son plus grand champ. Pour le 737-900 BW cela
donne 916 octets, vérifié en vol (AircraftModel = 9 lu au bon endroit).
"""

import json
import re
import sys
from pathlib import Path

SIZES = {"bool": 1, "char": 1, "unsigned char": 1, "short": 2, "unsigned short": 2,
         "int": 4, "unsigned int": 4, "float": 4, "double": 8}
DECL = re.compile(r"(unsigned (?:char|short|int)|bool|char|short|int|float|double) (\w+)(?:\[(\d+)\])?$")


def layout(header: str, structure: str) -> dict:
    text = Path(header).read_text(encoding="latin-1")
    body = text[text.index(f"struct {structure}"):]
    body = re.sub(r"//[^\n]*", "", body[body.index("{") + 1: body.index("};")])
    fields, offset, max_align = {}, 0, 1
    for raw in body.split(";"):
        decl = " ".join(raw.split())
        if not decl:
            continue
        match = DECL.match(decl)
        if not match:
            raise SystemExit(f"déclaration non comprise : {decl!r}")
        ctype, name, count = match.group(1), match.group(2), int(match.group(3) or 1)
        size = SIZES[ctype]
        offset = (offset + size - 1) // size * size
        max_align = max(max_align, size)
        if name != "reserved":
            fields[name] = [offset, ctype, count]
        offset += size * count
    total = (offset + max_align - 1) // max_align * max_align
    return {"structure": structure, "taille": total, "champs": fields}


if __name__ == "__main__":
    result = layout(sys.argv[1], sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"{len(result['champs'])} champs, {result['taille']} octets -> {sys.argv[3]}")
