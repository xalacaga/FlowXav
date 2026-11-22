# FlowXav

**Français** · [English](README.en.md)

FlowXav vous apprend à mettre en route un avion de ligne dans Microsoft Flight
Simulator, **dans le vrai cockpit**. Pour chaque étape, la caméra se place
sur la bonne commande, un anneau orange clignote dessus, et FlowXav attend
que vous la manœuvriez. Dès que l'avion change d'état, l'anneau passe au
vert et l'étape suivante commence.

Pas besoin de connaître le cockpit à l'avance, ni de lire un manuel sur un
second écran. FlowXav montre, vous manœuvrez.

> Simulation uniquement : FlowXav ne doit jamais servir en vol réel.

## Ce que fait FlowXav

- **Guidage étape par étape** : vue de la commande, anneau clignotant, puis
  validation automatique quand l'état de l'avion correspond.
- **« Montre-moi »** : cherchez une commande (BAT, IRS, APU…) et FlowXav vous
  la montre pendant quelques secondes.
- **Reconnaissance de l'avion** chargé dans MSFS, par son titre.
- **Une source d'état par avion** : le SDK de l'éditeur quand il existe (PMDG),
  sinon les variables du simulateur.

## Avions pris en charge

| Avion | État | Carte des commandes |
|---|---|---|
| PMDG 737-900 (MSFS 2024) | SDK PMDG | 408 commandes : panneau supérieur, panneau supérieur arrière (IRS), tableau central (MCP, EFIS), pylône, pylône arrière (radios, transpondeur, audio), CDU gauche |

D'autres avions, Boeing comme Airbus, viendront en ajoutant leur profil.
Pour le PMDG 737, la procédure livrée est pour l'instant une **démonstration**
des premières mises sous tension. La procédure complète, de Cold & Dark à
prêt au départ, est en cours d'écriture.

## Prérequis

- Windows 10 ou 11, 64 bits.
- Microsoft Flight Simulator 2024 en **fenêtré** ou en **plein écran fenêtré**.
  L'anneau ne s'affiche ni en plein écran exclusif ni en réalité virtuelle.
- Pour le PMDG 737 : la diffusion du SDK doit être activée. Dans le fichier
  `737_Options.ini` de l'avion
  (`%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalState\WASM\MSFS2024\pmdg-aircraft-739\work`),
  ajoutez ces lignes puis rechargez l'avion :

  ```ini
  [SDK]
  EnableDataBroadcast=1
  ```

## Installation

Téléchargez `FlowXav-Setup-<version>.exe` depuis les
[versions publiées](https://github.com/xalacaga/FlowXav/releases) et lancez-le.
L'installateur :

- installe FlowXav pour votre compte, sans droits administrateur, dans
  `%LOCALAPPDATA%\Programs\FlowXav` ;
- installe Microsoft WebView2 s'il manque ;
- n'installe ni ne remplace jamais le SimConnect du système : FlowXav emporte
  sa propre copie de la bibliothèque, qui dialogue avec MSFS.

Une archive portable (`FlowXav-<version>-windows-x64-portable.zip`) est aussi
proposée.

## Utilisation

1. Lancez MSFS et chargez votre avion, en Cold & Dark de préférence.
2. Lancez FlowXav : il se connecte tout seul et reconnaît l'avion.
3. Choisissez une procédure, puis **Démarrer le guidage**.
4. Manœuvrez la commande entourée par l'anneau. Pendant le guidage, la vue
   reste fixée sur la commande : c'est voulu, les clics passent.
5. **Fait** valide une étape à la main, par exemple pour un bouton-poussoir
   dont l'état n'est pas lisible. **Arrêter** rend la caméra.

Vos réglages et le journal sont dans `%LOCALAPPDATA%\FlowXav`.

## Confidentialité

FlowXav fonctionne entièrement sur votre ordinateur. Son service local
n'écoute que `127.0.0.1` et rien n'est envoyé sur Internet.

## Pour les contributeurs

Voir [CONTRIBUTING.md](CONTRIBUTING.md) : architecture, ajout d'un avion,
cartographie d'un cockpit et construction de l'installateur.

## Licence

PolyForm Noncommercial 1.0.0 : voir [LICENSE](LICENSE) et [NOTICE](NOTICE).
Microsoft Flight Simulator, PMDG, Boeing et Airbus sont des marques de leurs
propriétaires respectifs. FlowXav n'est affilié à aucun d'eux.
