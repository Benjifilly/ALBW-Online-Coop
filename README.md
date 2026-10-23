<p align="center">
  <img src="docs/images/banner.png" alt="ALBW Online Co-op" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/plateforme-Windows%2010%20%7C%2011-2b5797?style=for-the-badge&logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/émulateur-Azahar-c99a3b?style=for-the-badge" alt="Azahar">
  <img src="https://img.shields.io/badge/joueurs-jusqu'à%204-8b1e1e?style=for-the-badge" alt="Jusqu'à 4 joueurs">
  <img src="https://img.shields.io/badge/python-3.13-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13">
</p>

<p align="center">
  <b>Jouez à <i>The Legend of Zelda: A Link Between Worlds</i> à plusieurs, en ligne.</b><br>
  Chacun joue sa partie dans son Azahar ; vous vous voyez, vous vous aidez,<br>
  et ce que l'un fait dans le monde, les autres le voient aussi.
</p>

---

<p align="center">
  <img src="docs/images/deux-jeux.png" alt="Deux jeux, un même monde" width="95%">
  <br><sub>Deux jeux, un même monde : chacun voit l'autre Link, à la même place, avec sa couleur.</sub>
</p>

## ✨ Ce qui est partagé

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/compagnon.png" alt="Les compagnons" width="100%">
      <h3>🧝 Les compagnons</h3>
      Les autres Links apparaissent dans votre jeu, en 3D : déplacements,
      animations, tunique <b>verte, bleue, rouge ou violette</b>, épée,
      bouclier, arc, grappin, bombes… et même la fusion en peinture sur les murs.
      Ils se bousculent doucement au lieu de se traverser.
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/cristal.png" alt="Le monde partagé" width="100%">
      <h3>🌍 Le monde</h3>
      Herbes, buissons, pots, rochers et panneaux coupés ou cassés chez tout le
      monde ; objets portés et lancés ; boules de cristal, boutons au sol et
      torches qui réagissent chez les deux.
    </td>
  </tr>
</table>

| | |
| --- | --- |
| ❤️ **Cœurs liés** | Une vie commune (option), et un HUD avec le pseudo et les cœurs de chacun. |
| ⚔️ **Ennemis** | Mêmes positions, mêmes points de vie, morts communes ; ils attaquent le joueur le plus proche. |
| 💎 **Butin** | Ce qui tombe est identique pour tous, et chacun reçoit sa part. |
| 🗝️ **Progression** | Coffres, objets reçus, portes, histoire : partagés (sauf les petites clés). |
| 💰 **Bourse commune** | Un achat chez Ravio coûte à tout le groupe. |
| 👻 **Mort partagée** | Un Link tombé suit ses compagnons en spectateur ; une girouette activée le ranime. Si tout le groupe tombe, retour ensemble à la dernière girouette (−15 % de rubis). |

## 📦 Ce qu'il faut

- **Windows 10 ou 11**
- **[Azahar](https://azahar-emu.org/)** (testé avec la version 2126.2)
- **Votre copie** de *A Link Between Worlds*, **version européenne (EUR)** : la
  version américaine est reconnue, mais les fonctions avancées ne sont
  vérifiées que sur l'EUR
- **Python** : rien à faire, le lanceur l'installe tout seul s'il manque

## 🚀 Installation

1. **Téléchargez** le projet : bouton vert **Code → Download ZIP**, puis
   décompressez-le où vous voulez.
2. **Dans Azahar**, ouvrez les réglages, onglet **Débogage**, et cochez
   **Enable RPC Server**.
3. **Double-cliquez sur `run_coop.bat`.**

Au premier lancement, le lanceur installe ce qui manque (Python, PyQt6,
pygame), puis un écran d'accueil vérifie tout pour vous : Azahar, le jeu, le
serveur RPC, la manette et le réseau.

## 🎮 Jouer en ligne

<table>
  <tr>
    <th width="50%">👑 L'hôte (un seul joueur)</th>
    <th width="50%">🤝 Les autres</th>
  </tr>
  <tr>
    <td valign="top">
      1. Lancez <code>run_coop.bat</code> → <b>Héberger un salon</b><br>
      2. Donnez l'adresse affichée à vos amis<br>
      3. Chargez votre sauvegarde
    </td>
    <td valign="top">
      1. Lancez <code>run_coop.bat</code> → <b>Rejoindre un ami</b><br>
      2. Entrez l'adresse de l'hôte (elle est testée avant d'entrer)<br>
      3. Chargez votre sauvegarde
    </td>
  </tr>
</table>

> [!TIP]
> **Pas sur le même réseau ?** Le salon utilise le port **UDP 45990**. Le plus
> simple : installez tous **[Tailscale](https://tailscale.com/)** (ou Radmin
> VPN) et utilisez l'adresse Tailscale de l'hôte. Sinon, l'hôte doit ouvrir ce
> port sur sa box.

> [!IMPORTANT]
> Chacun joue **sa propre sauvegarde**, et ce qui change pendant la session est
> partagé. Pour que tout corresponde, commencez au même point de l'histoire
> (par exemple une nouvelle partie ensemble). Tous les joueurs doivent avoir la
> **même version** du projet.

## 🧪 Essayer à deux sur un seul PC

Double-cliquez sur **`test_local_coop.bat`**, puis **Lancer Link 1** et
**Lancer Link 2** : les deux fenêtres se placent côte à côte et se connectent
toutes seules. Chaque copie a ses propres sauvegardes de test ; votre
installation d'Azahar n'est pas modifiée.

## ⌨️ Pendant la partie

| Touche | Action |
| :---: | --- |
| <kbd>F8</kbd> | Ouvrir ou fermer le menu coop |
| <kbd>Échap</kbd> | Fermer le menu et revenir au jeu |
| <kbd>L3</kbd> + <kbd>R3</kbd> | Ouvrir le menu à la manette (modifiable) |

Le menu regroupe votre pseudo et votre couleur de tunique, le salon, la liste
des compagnons, le journal de la session et les réglages du HUD.

<p align="center">
  <img src="docs/images/equipement.png" alt="En jeu" width="70%">
</p>

## 🛠️ En cas de souci

<details>
<summary><b>Azahar est détecté, mais pas de cœurs</b></summary>

Chargez une sauvegarde : le compagnon attend une vraie partie, pas l'écran
titre.
</details>

<details>
<summary><b>Rien ne se connecte</b></summary>

Vérifiez que <i>Enable RPC Server</i> est coché dans Azahar, que l'adresse de
l'hôte est la bonne et que le port UDP 45990 est joignable chez lui (ou que
vous êtes tous sur Tailscale).
</details>

<details>
<summary><b>Un ami ne voit pas les mêmes choses</b></summary>

Vérifiez que vous avez tous la même version du projet, la version EUR du jeu,
et des sauvegardes au même point de l'histoire.
</details>

Le **journal** du menu coop explique ce qui se passe et peut être copié pour
signaler un problème.

## 🔧 Pour les développeurs

[ARCHITECTURE.md](ARCHITECTURE.md) explique comment tout fonctionne. Le code
injecté dans le jeu est écrit en assembleur ARM (`src/memory/*.arm`) ; après
une modification, reconstruisez-le :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m scripts.build_remote_model
```

## ⚖️ Mentions

*The Legend of Zelda* et *A Link Between Worlds* sont des marques de Nintendo.
Ce projet de fan n'est ni affilié à Nintendo ni approuvé par Nintendo. Il ne
contient ni le jeu ni son code ; seules quelques images de l'interface
(`assets/`) viennent des textures du jeu et restent la propriété de Nintendo.
