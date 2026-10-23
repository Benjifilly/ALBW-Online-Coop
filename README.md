# ALBW Online Co-op

Jouez à **The Legend of Zelda: A Link Between Worlds** à plusieurs, en ligne,
sur PC avec l'émulateur **Azahar**. Chaque joueur a sa propre partie ; le
compagnon (un petit programme Python à côté de l'émulateur) relie les jeux :
vous voyez les autres Links dans votre monde, et ce que l'un fait, les autres
le voient aussi.

> Projet de fan, expérimental, sans lien avec Nintendo. Le jeu n'est pas
> fourni : il vous faut votre propre copie d'ALBW.

## Ce qui est partagé

- **Les autres Links**, en 3D dans votre jeu : position, animations, couleur
  de tunique (vert, bleu, rouge, violet), épée, bouclier, arc, grappin,
  bombes, et même la fusion en peinture sur les murs.
- **Les cœurs** (option « Cœurs liés ») et un HUD avec les pseudos et la vie de
  chacun.
- **Le monde** : herbes, buissons, pots, rochers, panneaux coupés ou cassés
  chez tout le monde ; les objets portés et lancés.
- **Les ennemis** : mêmes positions, mêmes points de vie, morts communes.
- **Le butin** : ce qui tombe est le même pour tous, et chacun reçoit sa part.
- **La progression** : coffres, objets reçus, portes, drapeaux de l'histoire
  (sauf les petites clés, qui restent à celui qui les trouve).
- **La bourse** : une bourse commune ; un achat chez Ravio coûte à tout le
  monde.
- **Les interrupteurs** : boules de cristal, boutons au sol, torches.
- **La mort** : un Link tombé devient spectateur et suit ses compagnons ; un
  compagnon le ranime en activant une girouette. Si tout le groupe tombe, tout
  le monde repart de la dernière girouette (−15 % de rubis).
- **Les Links se bousculent** doucement au lieu de se traverser.

## Ce qu'il faut

- Windows 10 ou 11.
- [Azahar](https://azahar-emu.org/) (testé avec la version 2126.2).
- Votre copie de **The Legend of Zelda: A Link Between Worlds, version
  européenne (EUR)**. La version américaine est reconnue, mais les fonctions
  avancées ne sont vérifiées que sur l'EUR.
- Python 3.13 : le lanceur l'installe tout seul s'il manque (via winget, pour
  votre compte uniquement).

## Installation

1. Téléchargez le projet : bouton **Code → Download ZIP** sur GitHub, puis
   décompressez-le où vous voulez (ou `git clone`).
2. Dans les réglages d'Azahar, onglet **Débogage**, cochez
   **Enable RPC Server**.
3. Double-cliquez sur **`run_coop.bat`**. Au premier lancement, il installe ce
   qui manque (Python, PyQt6, pygame), puis affiche un écran d'accueil qui
   vérifie tout : Azahar, le jeu, le serveur RPC, la manette et le réseau.

## Jouer en ligne

**L'hôte** (un seul joueur) :

1. Lancez `run_coop.bat` et choisissez **Héberger un salon**.
2. Donnez à vos amis l'adresse affichée (par exemple `192.168.1.24:45990`).
3. Chargez votre sauvegarde.

**Les autres joueurs** :

1. Lancez `run_coop.bat` et choisissez **Rejoindre un ami**.
2. Entrez l'adresse de l'hôte : le lanceur vérifie qu'elle répond avant
   d'entrer.
3. Chargez votre sauvegarde.

Jusqu'à 4 joueurs. Le salon utilise le port **UDP 45990** : sur le même réseau
local, rien à faire ; à distance, il faut soit ouvrir ce port chez l'hôte,
soit utiliser un réseau privé virtuel (Tailscale, ZeroTier, Radmin VPN…).
Tous les joueurs doivent avoir la même version du projet.

## Essayer à deux sur un seul PC

Double-cliquez sur **`test_local_coop.bat`** (ou choisissez **Essayer à deux
sur ce PC** dans l'écran d'accueil). Cliquez **Lancer Link 1**, chargez une
sauvegarde, puis **Lancer Link 2** : les deux fenêtres se placent côte à côte
et se connectent toutes seules. Chaque copie a ses propres sauvegardes de test,
rangées dans `.local/coop-lab/` ; votre installation d'Azahar n'est pas
modifiée.

## Pendant la partie

| Touche | Action |
| --- | --- |
| **F8** | Ouvrir ou fermer le menu coop |
| **Échap** | Fermer le menu et revenir au jeu |
| **L3 + R3** | Ouvrir le menu à la manette (modifiable dans Réglages) |

Dans le menu : votre pseudo et votre couleur de tunique, le salon, la liste
des compagnons, le journal de la session et les réglages du HUD (taille,
opacité, position).

## En cas de souci

- **« ALBW reconnu » mais pas de cœurs** : chargez une sauvegarde ; le
  compagnon attend une vraie partie (pas l'écran titre).
- **Rien ne se connecte** : vérifiez que *Enable RPC Server* est coché dans
  Azahar, et que le port UDP 45990 est joignable chez l'hôte.
- **Un ami ne voit pas les mêmes choses** : vérifiez que vous avez tous la même
  version du projet et la version EUR du jeu.
- Le journal (menu coop → **Journal**) explique ce qui se passe et peut être
  copié pour signaler un problème.

## Pour les développeurs

[ARCHITECTURE.md](ARCHITECTURE.md) explique comment tout fonctionne. Le code
injecté dans le jeu est écrit en assembleur ARM (`src/memory/*.arm`) ; après
une modification, reconstruisez-le :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m scripts.build_remote_model
```

## Mentions

The Legend of Zelda et A Link Between Worlds sont des marques de Nintendo. Ce
projet n'est ni affilié à Nintendo ni approuvé par Nintendo. Il ne contient ni
le jeu ni son code ; seules quelques images de l'interface (`assets/`) viennent
des textures du jeu et restent la propriété de Nintendo.
