# -*- mode: python ; coding: utf-8 -*-

import ctypes
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_root = Path(SPECPATH)

# Ressource VERSIONINFO : un binaire Windows sans métadonnées d'éditeur est
# traité comme suspect par les moteurs heuristiques.
version_source = (project_root / "flowxav" / "__init__.py").read_text(encoding="utf-8")
version_match = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', version_source)
if not version_match:
    raise SystemExit("Version FlowXav introuvable dans flowxav/__init__.py.")
version_tuple = tuple(int(part) for part in version_match.groups()) + (0,)
version_text = ".".join(str(part) for part in version_tuple)
version_resource = project_root / "build" / "version_info.txt"
version_resource.parent.mkdir(parents=True, exist_ok=True)
version_resource.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'Xalacaga'),
      StringStruct('FileDescription', 'FlowXav - guidage interactif du cockpit pour MSFS'),
      StringStruct('FileVersion', '{version_text}'),
      StringStruct('InternalName', 'FlowXav'),
      StringStruct('LegalCopyright', 'Xalacaga'),
      StringStruct('OriginalFilename', 'FlowXav.exe'),
      StringStruct('ProductName', 'FlowXav'),
      StringStruct('ProductVersion', '{version_text}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
""",
    encoding="utf-8",
)

webview_datas, webview_binaries, webview_hidden = collect_all("webview")
sdk_simconnect = Path(r"C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll")
if not sdk_simconnect.is_file():
    raise SystemExit("SimConnect.dll introuvable : installez le SDK MSFS 2024 avant de construire la distribution.")
sdk_dll = ctypes.WinDLL(str(sdk_simconnect))
missing = [name for name in ("SimConnect_AICreateNonATCAircraft_EX1", "SimConnect_CameraSet") if not hasattr(sdk_dll, name)]
if missing:
    raise SystemExit("La DLL du SDK installé n'est pas celle de MSFS 2024 : API absentes : " + ", ".join(missing))

# DLL privée, jamais installée dans le système : elle parle au service SimConnect de MSFS.
binaries = [(str(sdk_simconnect), "SimConnect")] + webview_binaries
datas = [
    (str(project_root / "flowxav" / "web" / "static"), "flowxav/web/static"),
    # Profils d'avion : identité, carte des commandes, procédures, disposition des données.
    (str(project_root / "flowxav" / "aircraft"), "flowxav/aircraft"),
    (str(project_root / "assets" / "flowxav.ico"), "assets"),
] + webview_datas
hiddenimports = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                 "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"] + webview_hidden

a = Analysis(
    ["flowxav/desktop.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FlowXav",
    icon=str(project_root / "assets" / "flowxav.ico"),
    version=str(version_resource),
    debug=False,
    strip=False,
    upx=False,          # la compression d'exécutable déclenche les détections heuristiques
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FlowXav")
