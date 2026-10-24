# Architecture

Compagnon Python/PyQt6 à côté de chaque Azahar. Il lit la mémoire du jeu par
RPC, échange l'état avec les autres joueurs en UDP et installe dans le jeu de
petits hooks ARM réversibles qui appellent les routines natives. Profil
vérifié : ALBW EUR `00040000000EC400`. Aucun binaire Nintendo n'est modifié
sur disque.

## Modules

| Domaine | Fichiers |
| --- | --- |
| Lancement | `src/launcher.py` (accueil), `src/main.py` (orchestrateur), `src/local_session.py` (essai à deux), `src/updater.py` |
| Liaison jeu | `src/rpc/` (RPC Azahar, GDB, pont hybride) |
| Lecture d'état | `src/memory/albw_state.py`, `gameplay_state.py`, `world_actors.py`, `enemy_sync.py` |
| Réseau | `src/network/coop_node.py` (salon, protocole 20), `datagrams.py`, `world_journal.py` (props), `shared_loot.py`, `shared_progress.py`, `enemy_frames.py`, `enemy_hub.py` |
| Connexion | `src/network/probe.py` (test sans rejoindre), `rendezvous.py` (codes de salon, STUN, perçage), `reachability.py` (adresses et UPnP), `quality.py` (latence et pertes de sondes) |
| Import de progression | `src/network/save_transfer.py`, `src/online_progress.py`, `src/online_saves.py`, `src/save_import.py` |
| Rendu du compagnon | `remote_model`, `appearance`, `equipment_model`, `wall_model`, `fresco_*`, `effect_*`, `render_state` |
| Monde partagé | `world_sync.py` (orchestration), `world_native.py` (réserve et anneau natif), `mass_state.py` (lecteur), `foliage_motion.py`, `capture_hooks.py`, `grass_restore.py`, `combat_log.py` |
| Ennemis | `enemy_authority.py`, `enemy_health.py`, `enemy_pose.py`, `enemy_impact_replay.py`, `combat_evidence.py`, `src/dynamic_enemies.py` |
| Butins | `src/memory/shared_loot.py` + `src/network/shared_loot.py` |
| Interface | `src/overlay/` |

## Liaison avec Azahar

Le rendu, le monde partagé, les butins et la progression exigent le pont
hybride (`src/rpc/hybrid_bridge.py`) : mémoire par RPC, débogueur GDB
seulement pour retirer les crochets à un point d'arrêt sûr. Le lanceur active
dans l'Azahar du joueur le serveur RPC et le débogueur (Azahar fermé, après
accord), et retire la pause en arrière-plan, puis passe au compagnon
`--rpc-port`, `--gdb-port`, `--title-id` et `--process-name`. Le débogueur ne
met pas le jeu en pause : il écoute, et le compagnon s'y connecte le temps
d'un retrait. Sans ce pont, seuls les cœurs et la liste des joueurs
fonctionnent. L'essai à deux prépare des copies portables avec un port RPC
privé.

Azahar répond au RPC depuis sa boucle d'émulation : un chargement ou une
compilation de shaders la bloque. Le pont tolère environ une seconde sans
réponse avant de se détacher ; le rendu interrompu redémarre ensuite seul,
trois fois au plus par session, cinq secondes après la coupure. Le journal
garde sous chaque problème ses détails (origine dans le code, dernière requête
restée sans réponse), affichés par « Problèmes » et inclus par « Copier ».

## Code natif

Sources `src/memory/*.arm`, assemblées par `scripts/build_remote_model.py`
(keystone) en `*.bin` versionnés. Deux zones :

- **Zone de patch fixe** `7AFD00..7B0000` et **réserve monde** (2 Mio) :
  tout emplacement est déclaré dans `src/memory/world_layout.py`
  (`PATCH_SLOTS`, `WORLD_RESERVE`). `free_patch_ranges()` donne la place
  libre ; une seconde zone de trampolines `7AFA48..7AFB00`
  (`TRAMPOLINE_SLOTS`) complète la première.
- Un hook = un saut dans le code du jeu → trampoline 8 octets dans la zone de
  patch → code dans la réserve. Installation : empreinte vérifiée, code,
  trampoline, puis saut ; retrait dans l'ordre inverse après une image native.

## Familles d'objets « Mass » (herbes, buissons…)

`src/mass_families.py` est la **seule source** : une ligne `MassFamily` par
couple type/vtable vérifié (nombre, records, taille, état final). Le
builder en génère la table de `world_mass.arm`, routine native unique
(`world+0x2000`) qui valide acteur, famille, bornes, placement et rend
l'adresse du record. Tous les hooks Mass l'appellent.

Ajouter une famille :
1. relever en lecture seule factory/vtable/update/coupe dans le jeu EUR ;
2. ajouter la ligne au catalogue et reconstruire ;
3. donner sa capture (hook à la transition native) et son rejeu (branche
   dans `world_dispatch.arm`) ; réutiliser l'anneau d'événements et le
   journal `mass_cut` ;
4. reconstruire le code natif puis relancer les compagnons.

## Objets-acteurs à machine d'états (panneaux…)

Catalogue `ACTOR_PROPS` (même fichier) : type, vtable, position de la
machine, changeState natif, point de décision, états autorisés. Table native
générée pour `world_prop.arm` (`world+0x2400`). La capture remplace le `bl
changeState` du point de décision ; le rejeu appelle ce même changeState
quand l'objet local est dans le même état de départ. Toutes les captures sont
déclarées dans `src/memory/capture_hooks.py` (`CAPTURES`) et installées
ensemble.

## Flux d'une coupe

Jeu A coupe → hook de capture → anneau natif (`world+0x100`) →
`WorldSynchronizer._publish_local_cuts` → journal de salle de l'hôte
(dédoublonné par identité) → jeu B `_apply_remote_cut` → dispatch natif
(`world_dispatch.arm`) sur le thread du jeu → résultat → accusé. Les
identités réseau sont `type, placement, xyz du lot, index, xyz` : jamais de
pointeur.

## Connexion et transport

`VERSION` contient le numéro commun au lanceur et au salon. Le contrôle de
version précède l'inscription d'un joueur ; un refus d'un ancien protocole
reste lisible. Chaque session et chaque tentative de connexion ont leur
identifiant aléatoire. États et listes de joueurs portent aussi le nonce de
connexion : un ancien paquet ne peut pas remplacer la nouvelle instance.

La connexion retente automatiquement après une coupure. L'hôte conserve
les acquittements pour le même joueur et le même nonce ; un nouveau compagnon
ouvre une nouvelle série. Les journaux monde, butin et progression renvoient
les événements manquants, dans l'ordre et sans les appliquer deux fois. Les
dégâts ennemis restent en attente d'acquittement pendant la reconnexion.

Les messages logiques sont limités à 16 Kio. Les petits JSON restent directs ;
les plus grands sont comprimés puis découpés si nécessaire en datagrammes
d'au plus 1 200 octets. L'assemblage est séparé par adresse et identifiant,
limité à 32 messages incomplets et expire après 1,5 seconde. La décompression
est bornée. Les journaux assurent leurs renvois ; les poses sont rafraîchies
par le prochain instantané.

La découverte d'adresses et UPnP tournent en arrière-plan, sans dépendance
supplémentaire. Une redirection existante n'est pas remplacée. Seule la
redirection créée par cette instance est retirée à la fermeture du salon.
L'adresse publique annoncée par la box n'est pas une preuve d'accessibilité
depuis Internet ; le test de l'ami reste nécessaire. La qualité affichée est
une estimation sur les dernières sondes acquittées, pas une mesure de tous
les paquets du jeu.

### Se rejoindre sans serveur (`src/network/rendezvous.py`)

Le salon utilise un seul socket double pile (IPv6 avec IPv4 associé) ; les
adresses restent des couples `(ip, port)`, l'IPv4 associée est ramenée à sa
forme habituelle. Des requêtes STUN publiques (Google, Cloudflare) partent de
ce socket même, toutes les 20 s, pour connaître l'adresse que voit Internet
et garder ouverte la traduction de la box. Deux serveurs IPv4 qui voient deux
ports différents signalent une box « symétrique ».

Le code de salon (`ALBW-…`, base32 avec somme de contrôle) liste jusqu'à six
chemins : publics d'abord (IPv6, STUN, UPnP), réseau local ensuite. Le client
envoie son HELLO à tous les chemins toutes les demi-secondes ; ces envois
ouvrent aussi sa propre box. La première réponse portant le nonce secret de
la tentative fixe l'hôte, quel que soit le chemin (adresse source IPv6
différente, autre port). Le même compagnon arrivé par un second chemin n'est
pas un doublon. Si la box de l'hôte filtre, le client affiche un code de
réponse ; l'hôte le colle et envoie des paquets `PUNCH` vers ces chemins
pendant 45 s. Sans relais, deux box symétriques en IPv4 restent injoignables ;
l'IPv6, sans traduction d'adresse, évite ce cas sur la plupart des réseaux
mobiles. Protocole 21.

## Import de la progression enregistrée

L'hôte lit uniquement le fichier actuellement chargé (`*(0x711DE8)+0x9BC`).
Les trois emplacements EUR sont `Save_0.bin` à `Save_2.bin`, chacun de
`0x1604` octets. Les CRC des blocs sont vérifiés aux offsets `0xDFC`,
`0x13FC` et `0x15FC`. `SaveCommon.bin` est distinct (512 octets) et n'est
jamais remplacé par celui de l'hôte.

Le dossier est trouvé dans les données Azahar de l'exécutable associé au
joueur (portable ou profil Windows, stockage personnalisé inclus), sous
`sdmc/Nintendo 3DS/<id0>/<id1>/title/00040000/000ec400/data/00000001`.
Une archive ambiguë est refusée. Les chemins locaux ne viennent jamais du réseau.

Le salon transporte une enveloppe EUR de moins de 16 Kio, avec empreinte
SHA-256. Blocs de 768 octets, fenêtre de quatre, acquittements, renvoi après
450 ms et abandon après 45 secondes. La réception complète est vérifiée
avant l'ouverture de l'assistant indépendant d'import.

L'assistant crée une copie datée sous `backups/`, propose les fichiers 1 à 3,
puis demande confirmation. Il ferme uniquement l'Azahar associé par sa
fermeture normale ; aucune terminaison forcée. Après fermeture, une nouvelle
copie intégrale et une vérification d'absence d'autre Azahar utilisant cette
archive précèdent le remplacement atomique du seul fichier choisi. En cas
d'échec d'écriture, l'ancien fichier est restauré. Le jeu et le compagnon
sont rouverts avec les mêmes paramètres ; le joueur charge le fichier choisi.

## Mise à jour

Au démarrage, `src/updater.py` consulte l'API publique GitHub du dépôt
`Benjifilly/ALBW-Online-Coop`. Lecture de `VERSION` et ZIP sont liés au même
commit de `main`. La recherche est bornée et facultative ; une panne réseau
n'empêche pas de jouer. Les copies de développement ou les copies Git
modifiées sont ignorées ; une copie Git propre se fie à son HEAD (celle qui
publie ne se propose ni sa propre révision ni une plus ancienne). Au premier
lancement d'un ZIP sans numéro de commit, les empreintes GitHub des fichiers
publics permettent de reconnaître une révision différente même si `VERSION`
n'a pas changé. Une seule requête donne la révision et les titres des commits
affichés comme nouveautés.

Après accord, seuls les fichiers d'exécution et de présentation sont
installés. `.local/`, `.venv/`, `backups/` et Git sont conservés. L'archive
est validée (chemins, liens, doublons et tailles) ; les fichiers remplacés
restent en mémoire pour le retour arrière, sans copie sur disque. Un verrou
empêche deux installations simultanées. Un marqueur `.patch-owner` dont
l'Azahar ou le compagnon vit encore interdit le remplacement ; celui d'un jeu
fermé est ignoré. Aucun marqueur n'est supprimé par la mise à jour.

Les dépendances ne sont réinstallées que si `requirements.txt` change. En
cas d'échec, les fichiers précédents sont restaurés et la réinstallation
des anciennes dépendances est tentée. Le lanceur relit sa nouvelle version
après réussite. `.local/updater.json` mémorise le commit installé et les
empreintes pour distinguer les mises à jour des modifications personnelles.
