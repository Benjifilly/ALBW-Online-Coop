# Architecture

Compagnon Python/PyQt6 à côté de chaque Azahar. Il lit la mémoire du jeu par
RPC, échange l'état avec les autres joueurs en UDP et installe dans le jeu de
petits hooks ARM réversibles qui appellent les routines natives. Profil
vérifié : ALBW EUR `00040000000EC400`. Aucun binaire Nintendo n'est modifié
sur disque.

## Modules

| Domaine | Fichiers |
| --- | --- |
| Lancement | `src/main.py` (orchestrateur), `src/local_session.py` (essai à deux) |
| Liaison jeu | `src/rpc/` (RPC Azahar, GDB, pont hybride) |
| Lecture d'état | `src/memory/albw_state.py`, `gameplay_state.py`, `world_actors.py`, `enemy_sync.py` |
| Réseau | `src/network/coop_node.py` (salon, protocole 15), `world_journal.py` (props), `shared_loot.py`, `enemy_frames.py`, `enemy_hub.py` |
| Rendu du compagnon | `remote_model`, `appearance`, `equipment_model`, `wall_model`, `fresco_*`, `effect_*`, `render_state` |
| Monde partagé | `world_sync.py` (orchestration), `world_native.py` (réserve et anneau natif), `mass_state.py` (lecteur), `foliage_motion.py`, `capture_hooks.py`, `grass_restore.py`, `combat_log.py` |
| Ennemis | `enemy_authority.py`, `enemy_health.py`, `enemy_pose.py`, `enemy_impact_replay.py`, `combat_evidence.py`, `src/dynamic_enemies.py` |
| Butins | `src/memory/shared_loot.py` + `src/network/shared_loot.py` |
| Interface | `src/overlay/` |

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
