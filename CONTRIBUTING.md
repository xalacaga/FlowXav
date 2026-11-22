# Contribuer à FlowXav

## Architecture

```
flowxav/
  desktop.py        fenêtre native (pywebview / WebView2) + service local, arrêt propre
  server.py         API FastAPI sur 127.0.0.1 et interface
  simulator.py      connexion MSFS, reconnaissance de l'avion, lecture de l'état (4 Hz)
  guide.py          moteur de guidage : étapes, conditions, validation
  overlay.py        anneau clignotant transparent aux clics, par-dessus MSFS
  sim/simconnect.py client SimConnect ctypes (SimVars, ClientData, caméra, événements)
  sim/camera.py     réalisateur caméra : vue verrouillée tant qu'une commande est montrée
  sim/window.py     zone client de la fenêtre MSFS
  state/            sources d'état : pmdg.py (SDK PMDG), simvars.py (variables MSFS)
  aircraft/<id>/    un dossier par avion : profile.json, carte.json, procedures/*.json
  web/static/       interface sans dépendance (HTML/CSS/JS), textes dans i18n.js
tools/
  generate_pmdg_layout.py  disposition d'une zone PMDG depuis l'en-tête du SDK
  cartographie/            relevé des vues et des commandes d'un cockpit
tests/              tests reproductibles, sans simulateur
```

Le moteur, la caméra et la surimpression sont communs à tous les avions.
Ajouter un avion revient à ajouter un dossier dans `flowxav/aircraft/`.

## Faits établis en vol (MSFS 2024)

- `CameraGet` renvoie les angles dans l'ordre (tangage, cap, inclinaison),
  alors que `CameraSet` attend (tangage, **inclinaison**, cap). Le tangage
  négatif correspond à un regard vers le haut, et le champ de vision est en
  radians. Seule la classe `Pose` gère cette différence.
- Pendant que FlowXav tient la caméra, les clics dans le cockpit passent,
  mais la vue à la souris est bloquée.
- La barre d'outils de MSFS, en haut de l'écran, bloque les clics : aucune
  commande ne doit se trouver dans les 8 % supérieurs d'une vue.
- Sans verrou, la vue dérive pendant une cartographie. On cartographie donc
  toujours avec la vue verrouillée.
- On montre un panneau **de face** (une vue de référence), avec l'anneau
  posé au point mémorisé. On ne fait pas pivoter la tête vers le bouton.

## Ajouter un avion

1. Créez `flowxav/aircraft/<id>/profile.json` avec le nom, le constructeur,
   la règle de reconnaissance (`titre_contient`, à comparer avec le titre
   réel que donne la SimVar `TITLE`) et la source d'état (`pmdg_ng3` ou
   `simvars`).
2. Relevez les vues et les commandes (voir plus bas), puis générez
   `carte.json` avec `tools/cartographie/build_map.py <id>`.
3. Écrivez les procédures dans `procedures/*.json`. Chaque étape nomme une
   commande de la carte, la valeur attendue (`egal`, `parmi`, `different`,
   `min`, `max`) et ses textes en français et en anglais. `verifier` permet
   de valider sur un autre champ que la commande elle-même, par exemple un
   voyant pour un interrupteur à ressort.

## Cartographier un cockpit

Tous les outils prennent l'identifiant de l'avion et le nom de la vue.

| Étape | Outil |
|---|---|
| Relever la vue que le joueur a cadrée | `record_view.py <avion> <vue>` |
| Capturer la vue verrouillée en PNG | `snap_view.py <avion> <vue>` |
| Relier les clics aux champs d'état (interrupteurs) | `map_panel.py <avion> <vue> <durée_s>` |
| Repérer les boutons sans état sur la capture | `points_image.json`, puis `vision.py <avion> <vue>` |
| Assembler la carte | `build_map.py <avion>` (sources dans `<avion>/sources.json`) |

Le débutant ne connaît pas les boutons. Les boutons-poussoirs se repèrent
donc sur la capture, et l'utilisateur ne fait que vérifier le résultat avec
« Montre-moi ».

## Développement

```powershell
FlowXav.bat                                    # premier lancement : crée .venv et installe
.venv\Scripts\python.exe -m pytest -m "not live_msfs"
.venv\Scripts\python.exe -m flowxav.desktop --no-open   # service seul, pour le diagnostic
```

### Tests d'intégration opt-in

Ces tests ne s'exécutent jamais sans variable d'environnement explicite :

```powershell
# Lecture non destructive du titre de l'avion et de la caméra
$env:FLOWXAV_RUN_LIVE_MSFS = "1"
.venv\Scripts\python.exe -m pytest tests/test_live_integrations.py -m live_msfs -k reads

# Reconnexion réelle : le test demande de fermer puis relancer MSFS (180 s par défaut)
$env:FLOWXAV_TEST_RECONNECT = "1"
.venv\Scripts\python.exe -m pytest tests/test_live_integrations.py -k disconnect -s

# Test visuel de deux secondes de la caméra et de l'overlay
$env:FLOWXAV_TEST_CAMERA_OVERLAY = "1"
.venv\Scripts\python.exe -m pytest tests/test_live_integrations.py -k camera_overlay

# Import SimBrief réel, confiné au dossier temporaire de pytest
$env:FLOWXAV_RUN_LIVE_SIMBRIEF = "1"
$env:FLOWXAV_SIMBRIEF_ID = "votre_identifiant"
.venv\Scripts\python.exe -m pytest tests/test_live_integrations.py -m live_network

# Smoke test d'un exécutable construit ou réellement installé
$env:FLOWXAV_INSTALLED_EXE = "C:\chemin\vers\FlowXav.exe"
.venv\Scripts\python.exe -m pytest tests/test_live_integrations.py -m installed
```

Le script de distribution exécute aussi automatiquement le smoke test sur le
binaire PyInstaller avant de créer l'archive et l'installateur.

## Construire la distribution

```powershell
scripts\build_windows.ps1
```

Le script exige le SDK MSFS 2024 (`C:\MSFS 2024 SDK`), vérifie la signature
Microsoft du programme d'installation de WebView2 et lance les tests. Il
produit ensuite l'application autonome (PyInstaller), l'archive portable,
l'installateur (Inno Setup) et les sommes SHA-256 dans `release/`.

Les scripts PowerShell qui contiennent des accents doivent être enregistrés
en UTF-8 **avec BOM** : Windows PowerShell 5.1 lit sinon le fichier en
Windows-1252.
