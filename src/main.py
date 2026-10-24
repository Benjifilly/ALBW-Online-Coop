# -*- coding: utf-8 -*-
import argparse
import ipaddress
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

if __package__ is None or __package__ == "":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.memory.albw_state import COURSE_NAMES, ALBWMemoryController
from src.memory.shared_health import SharedHealthSynchronizer
from src.memory.appearance import InGameAppearance,appearance_cache
from src.memory.world_sync import WorldSynchronizer
from src.memory.enemy_authority import EnemyAuthority
from src.memory.life_sync import LifeSynchronizer,life_notice
from src.memory.life_checkpoint import LifeCheckpoint
from src.network.coop_node import CoopNetworkNode
from src.overlay.azahar_overlay import AzaharNativeOverlay
from src.rpc.azahar_bridge import AzaharBridge
from src.console import print_banner, print_event, where

TUNIC_NAMES = {0: "green", 1: "blue", 2: "red", 3: "purple"}
TUNIC_INDICES = {"green": 0, "blue": 1, "red": 2, "purple": 3}


class CoopOrchestrator(QObject):
    snapshot_changed = pyqtSignal(dict)
    logs_changed = pyqtSignal(list)

    def __init__(self, bridge=None, profile=None, emulator_pid=0, *, enemy_authority=False) -> None:
        super().__init__()
        self.player_id = uuid.uuid4().hex[:8]
        self.player_name = "Link"
        self.tunic_idx = 0
        self.logs: List[str] = []
        self._log_lock = threading.Lock()
        self._memory_lock = threading.RLock()
        self._detection_status = None
        self._last_error = ""
        self.status_path = None
        self._next_status_write = 0
        self._next_network_send = 0

        self.bridge = bridge or AzaharBridge()
        self.memory = ALBWMemoryController(self.bridge)
        self.shared_health = SharedHealthSynchronizer()
        self.shared_world=WorldSynchronizer(self.bridge,self.add_log)
        # A collective return debits every wallet itself: its debit is not a spend.
        self.shared_world.progress.suppress_spend=lambda:self.life.party.active
        self.enemies=EnemyAuthority(self.bridge,self.memory) if enemy_authority else None
        self.life=LifeSynchronizer(self.bridge,self.add_log,
            checkpoint=LifeCheckpoint(self.bridge,emulator_pid))
        self.appearance=InGameAppearance(self.bridge,appearance_cache(profile),emulator_pid,self.add_log,
            marker_path=Path(__file__).resolve().parents[1]/'.local/profiles'/((profile or 'default')+'.patch-owner'))
        self.overlay: AzaharNativeOverlay | None = None

        self.net = CoopNetworkNode(
            player_id=self.player_id,
            player_name=self.player_name,
            tunic_color=TUNIC_NAMES[self.tunic_idx],
            on_event_log=self.add_log,
            enemy_authority=enemy_authority,
        )
        from src.network.reachability import HostingAccess
        self.hosting_access=HostingAccess(self.add_log)
        from src.online_progress import OnlineProgress
        self.online_progress=OnlineProgress(self,profile,emulator_pid)

        self._announced_room = self._announced_reply = ""
        self._joining_since = self._hosting_since = time.monotonic()
        self._running = True
        self._latest_ui_snapshot: Dict = {
            "rpc_connected": False,
            "net_mode": "offline",
            "soul_link": False,
            "in_game": False,
            "rupees": None,
            "players": [],
        }

    def add_log(self, msg: str, detail: str = "") -> None:
        stamp = time.strftime("%H:%M:%S")
        # Details follow the message on their own lines: the « Problèmes »
        # view and « Copier » show them, the plain journal stays short.
        lines = [line.strip() for line in str(detail).splitlines() if line.strip()]
        with self._log_lock:
            self.logs.append("\n".join([f"{stamp}  {msg}", *lines]))
            self.logs = self.logs[-200:]
            logs = list(self.logs)
        print_event(stamp, msg, lines)
        self.logs_changed.emit(logs)

    def get_ui_logs(self) -> List[str]:
        with self._log_lock:
            return list(self.logs)

    def sync_loop(self) -> None:
        while self._running:
            frame_started=time.monotonic()
            # Hors des instantanés du jeu : sur l'écran titre, rien d'autre ne change.
            try:progress=self.online_progress.poll()
            except Exception:progress={}
            try:
                if self.net.mode != "client" or self.net.connection_state == "connected":
                    self._joining_since = time.monotonic()
                self._announce_codes()
            except Exception:pass
            try:
                tunic_str = TUNIC_NAMES.get(self.tunic_idx, "green")
                with self._memory_lock:
                    snap = self.memory.read_snapshot(self.player_id, self.player_name, tunic_str)
                    if not self.life.party.active and not (self.life.wiped and self.life.party.group):
                        self.shared_health.step(snap, self.net, self.memory)
                detection = "playing" if snap.in_game else self.bridge.detection_status
                if detection != self._detection_status:
                    self._detection_status = detection
                    self.add_log({
                        "playing": "Partie ALBW prête. Les cœurs sont disponibles.",
                        "albw_detected": "ALBW détecté. En attente des données de la partie.",
                        "waiting_game": "Azahar répond. En attente du lancement d’ALBW.",
                        "other_game": "Le jeu ouvert n’est pas une version EUR / USA reconnue d’ALBW.",
                        "rpc_incompatible": "Le serveur RPC ne fournit pas la liste des jeux attendue.",
                        "rpc_unavailable": "Communication avec le jeu indisponible. Nouvelle tentative automatique.",
                    }[detection], getattr(self.bridge, "last_failure", "") if detection == "rpc_unavailable" else "")
                all_peers,life_ready = self.net.get_life_peers()
                world=(self.shared_world.native if not self.shared_world.error
                       and self.shared_world.state not in ('retiring','changing_room','waiting_game','waiting_session') else None)
                with self._memory_lock:
                    # Death, spectating and weathervane revival (life_sync).
                    for key,value in self.life.step(snap,all_peers,world,self.memory.player_ptr,roster_ready=life_ready).items():
                        setattr(snap,key,value)
                if frame_started>=self._next_network_send:
                    self._next_network_send=frame_started+1/30
                    # Pot/bush/rock carried by this Link (shown by the others).
                    carried=self.shared_world.carried
                    if carried and snap.render_state:snap.render_state['carried']=carried
                    self.net.broadcast_state(snap)
                # A spectator is invisible: never drawn, nothing reacts to him.
                peers = [p for p in all_peers if p.get('life','alive')!='ghost']
                with self._memory_lock:
                    if self._running:
                        self.appearance.step(self.memory,snap,peers)
                        self.shared_world.step(self.memory,snap,self.net,self.appearance,peers)
                        receipts=self.shared_world.drain_combat_receipts()
                        if self.enemies:
                            self.enemies.step(snap,self.net,receipts,world)
                authority = self.net.get_room_authority(snap.course, snap.stage)

                world_str = COURSE_NAMES.get(snap.course, "Hyrule")
                players_list = [
                    {
                        "player_id": self.player_id,
                        "name": self.player_name,
                        "tunic_color": self.tunic_idx,
                        "current_hp": snap.health,
                        "max_hp": snap.max_health,
                        "in_game": snap.in_game,
                        "world": world_str,
                        "is_self": True,
                        "is_room_master": authority == self.player_id,
                        "is_wall_merged": bool(snap.render_state.get('merged',False)),
                        "life": snap.life,
                    }
                ]
                for p in all_peers:
                    p_tunic = TUNIC_INDICES.get(str(p.get("tunic_color", "blue")).lower(), 1)
                    p_world = COURSE_NAMES.get(p.get("course", 0), "Hyrule")
                    players_list.append(
                        {
                            "player_id": p.get("player_id", ""),
                            "name": p.get("name", "Allié"),
                            "tunic_color": p_tunic,
                            "current_hp": int(p.get("health", 12)),
                            "max_hp": int(p.get("max_health", 12)),
                            "in_game": bool(p.get("in_game", False)),
                            "world": p_world,
                            "is_self": False,
                            "is_room_master": authority == p.get("player_id"),
                            "is_wall_merged": bool(p.get('render_state',{}).get('merged',False)),
                            "life": p.get('life','alive'),
                        }
                    )

                ui_snapshot = {
                    **progress,
                    **self.appearance.status(),
                    **self.shared_world.status(),
                    **(self.enemies.status() if self.enemies else {'enemy_sync_status':'off'}),
                    **self.net.get_connection_info(),
                    "rpc_connected": self.bridge.rpc_connected,
                    "detection_status": detection,
                    "region": self.bridge.region,
                    "in_game": snap.in_game,
                    "net_mode": self.net.mode,
                    "soul_link": self.net.soul_link,
                    "rupees": snap.rupees if snap.in_game else None,
                    "players": players_list,
                    "life_notice": self.life.party.notice() or life_notice(snap.life,all_peers),
                    "position_verified": self.memory.profile.actor_position_verified,
                    "can_teleport": self.memory.profile.actor_position_verified and snap.in_game and snap.life=='alive' and any(
                        p.get("in_game") and p.get("course") == snap.course and p.get("stage") == snap.stage
                        for p in peers
                    ),
                }
                if ui_snapshot != self._latest_ui_snapshot:
                    self._latest_ui_snapshot = ui_snapshot
                    self.snapshot_changed.emit(self.get_ui_snapshot())
                self._last_error = ""
            except Exception as exc:
                with self._memory_lock:
                    self.memory.last_snapshot.in_game = False
                    self.memory.player_ptr = 0
                self._latest_ui_snapshot = dict(self._latest_ui_snapshot, **progress, in_game=False,
                    detection_status="read_error", rupees=None, can_teleport=False,
                    players=[dict(p, in_game=False) if p.get("is_self") else p
                             for p in self._latest_ui_snapshot.get("players", [])])
                self.snapshot_changed.emit(self.get_ui_snapshot())
                message = f"Lecture du jeu interrompue : {type(exc).__name__}: {exc}"
                if message != self._last_error:
                    self.add_log(message, where(exc))
                    self._last_error = message
            self._write_status()
            time.sleep(max(.001,1/60-(time.monotonic()-frame_started)))

    def present_loop(self) -> None:
        """Companion pose for each new game frame, beside the slower sync loop.

        The sync loop's world and enemy steps can take 50-60 ms; the game
        draws every 16.7 ms. This thread only rewrites already prepared
        outputs (InGameAppearance.present_fast); the bridge serialises each
        request, and native jobs stay on the sync thread.
        """
        while self._running:
            self._present()
            time.sleep(.002)

    def _present(self):
        """Companion pose for the newest game frame."""
        try:self.appearance.present_fast(self.net.get_active_peers())
        except Exception as exc:
            message=f'Présentation du compagnon interrompue : {type(exc).__name__}: {exc}'
            if message!=self._last_error:self.add_log(message);self._last_error=message

    def _write_status(self):
        if self.status_path is None or time.monotonic() < self._next_status_write:
            return
        self._next_status_write = time.monotonic() + 1
        try:
            payload = dict(self.get_ui_snapshot(), updated_at=time.time(),
                pose={key: getattr(self.memory.last_snapshot, key) for key in (
                    'pose_verified', 'in_game', 'course', 'stage', 'x', 'y', 'z', 'yaw', 'tunic_color',
                    'animation_id','upper_animation_id')},
                memory_pointers={key: hex(getattr(self.memory, key)) for key in ("player_ptr", "game_ptr", "save_ptr")})
            temporary = self.status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.status_path)
        except OSError:
            pass

    def get_ui_snapshot(self) -> Dict:
        result = dict(self._latest_ui_snapshot)
        result.update(net_mode=self.net.mode, soul_link=self.net.soul_link,
                      port=self.net.bind_port, host_target=self.net.host_target)
        result.update(self.net.get_connection_info())
        result.update(self.hosting_access.snapshot())
        # Accessory: a code that cannot be built never costs the menu its state.
        try:room, reply, note = self.room_codes()
        except Exception:room = reply = note = ""
        result.update(room_code=room, reply_code=reply, nat_note=note)
        return result

    def set_identity(self, name: str, tunic_idx: int) -> None:
        self.player_name = name
        self.tunic_idx = tunic_idx % 4
        self.net.player_name = name
        self.net.tunic_color = TUNIC_NAMES.get(self.tunic_idx, "green")

    def start_hosting(self, port: int = 45990) -> bool:
        ok=self.net.start_host(port)
        if ok:self.hosting_access.start(self.net.bind_port);self._hosting_since=time.monotonic()
        return ok

    def join_host(self, host_ip: str, port: int = 45990) -> bool:
        self.hosting_access.close()
        return self.net.join_host(host_ip, port)

    def join_target(self, text: str, port: int = 45990) -> bool:
        """An address or a room code: every path of the code is tried at once."""
        from src.network.rendezvous import parse_target, PREFIX
        candidates = parse_target(text, port)
        if not candidates:
            return False
        self.hosting_access.close()
        code = text.strip().upper().startswith(PREFIX)
        return self.net.join_candidates(candidates, label="l’hôte (code de salon)" if code else None)

    def open_for_friend(self, code: str):
        """Host: the friend's reply code opens this box toward it for a while."""
        from src.network.rendezvous import decode, REPLY
        try:candidates = decode(code, REPLY)
        except ValueError as exc:return str(exc)
        if not self.net.punch(candidates):return "Ouvrez d’abord un salon."
        self.add_log("Passage ouvert vers votre ami pendant 45 s : il peut se connecter maintenant.")
        return ""

    def room_codes(self):
        """(room code of the host, reply code of a waiting friend, note) for the menu."""
        from src.network import rendezvous
        if self.net.mode == "host":
            rows = self.hosting_access.snapshot().get("hosting_addresses", [])
            found = self.net.public_candidates()
            for row in sorted(rows, key=lambda r: "UPnP" not in r["label"]):
                found += rendezvous.parse_target(row["address"], self.net.bind_port) or []
            # A public path first; the local network last, for friends at home.
            found.sort(key=lambda value: (not rendezvous.shareable(value[0]) or ipaddress.ip_address(value[0]).is_private,
                                          ":" not in value[0]))
            return rendezvous.encode(rendezvous.HOST, found), "", self.net.nat_note()
        if self.net.mode == "client" and self.net.connection_state != "connected":
            return "", self.net.reply_code(), self.net.nat_note()
        return "", "", ""

    def _announce_codes(self):
        room, reply, note = self.room_codes()
        # Once the local, UPnP and STUN paths are known: one line, not three.
        settled = (self.hosting_access.snapshot().get("hosting_addresses")
                   and (self.net.public_candidates() or time.monotonic() - self._hosting_since > 4))
        if room and room != self._announced_room and settled:
            self._announced_room = room
            self.add_log("Code de salon à envoyer à vos amis : " + room, note)
        if reply and reply != self._announced_reply and time.monotonic() - self._joining_since > 3:
            self._announced_reply = reply
            self.add_log("Si l’hôte ne vous voit pas, envoyez-lui ce code de réponse : " + reply, note)

    def leave_session(self) -> None:
        self.hosting_access.close()
        self.net.stop()
        self._latest_ui_snapshot = dict(self._latest_ui_snapshot,
            players=[p for p in self._latest_ui_snapshot.get("players", []) if p.get("is_self")],
            can_teleport=False)
        self.add_log("Vous avez quitté le salon.")

    def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        self.hosting_access.close()
        self.net.stop()
        with self._memory_lock:
            if not self.appearance.close():
                self.add_log('Restauration en attente : reprenez le jeu et désactivez le rendu avant de fermer, ou relancez cette copie Azahar.')
            self.bridge.close()

    def set_appearance_enabled(self,enabled):
        with self._memory_lock:
            self.appearance.set_enabled(enabled)
        self.add_log('Couleurs, compagnon et peinture activés.' if enabled else 'Restauration du rendu normal…')

    def set_soul_link(self, enabled: bool) -> None:
        with self._memory_lock:
            changed = self.net.set_soul_link(enabled)
        if changed:
            self.add_log("Cœurs liés activés : dégâts et soins partagés." if enabled else "Cœurs liés désactivés.")
        else:
            self.add_log("Ouvrez une sauvegarde chez l’hôte avant d’activer les cœurs liés.")

    def teleport_to_friend(self) -> bool:
        with self._memory_lock:
            # Refresh before writing: a door transition may have happened since
            # the menu last displayed this action.
            snap = self.memory.read_snapshot(self.player_id, self.player_name, TUNIC_NAMES[self.tunic_idx])
            target = next((p for p in self.net.get_active_peers() if p.get("in_game") and p.get("course") == snap.course
                           and p.get("stage") == snap.stage), None)
            if target is None or not snap.in_game:
                return False
            return self.memory.teleport_to_coordinates(
                float(target.get("x", 0.0)), float(target.get("y", 0.0)), float(target.get("z", 0.0)))


def main() -> None:
    parser = argparse.ArgumentParser(description="ALBW Online Co-op")
    parser.add_argument("--preview", action="store_true", help="Ouvrir l’interface sans connexion au jeu")
    parser.add_argument("--demo-party", action="store_true", help="Afficher quatre joueurs fictifs en aperçu")
    parser.add_argument("--profile", help="Profil indépendant pour les essais locaux (nom simple)")
    parser.add_argument("--emulator-pid", type=int, default=0, help="Fenêtre Azahar réservée à ce compagnon")
    parser.add_argument("--gdb-port", type=int, help="Port de l’instance locale préparée par le lanceur")
    parser.add_argument('--rpc-port',type=int,help='Liaison mémoire privée préparée pour cette copie')
    parser.add_argument("--title-id", help="Identifiant du jeu validé par le lanceur local")
    parser.add_argument("--process-name", help="Nom du programme lu dans le jeu local")
    parser.add_argument('--enemy-authority',action=argparse.BooleanOptionalAction,default=True,
                        help='Partager les ennemis entre les joueurs de chaque salle (vie, positions, attaques)')
    parser.add_argument('--host-progress',action='store_true',help='Proposer l’import de la progression enregistrée de l’hôte')
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--host-port", type=int)
    network.add_argument("--join", help="Adresse (IPv4:port, [IPv6]:port) ou code de salon de l’hôte")
    args = parser.parse_args()
    if args.demo_party and not args.preview:
        parser.error("--demo-party nécessite --preview")
    if args.host_port is not None and not 1 <= args.host_port <= 65535:
        parser.error("Le port du salon doit être compris entre 1 et 65535")
    if args.profile:
        if not args.profile.replace("-", "").replace("_", "").isalnum() or len(args.profile) > 40:
            parser.error("Nom de profil invalide")
        import src.overlay.azahar_overlay as overlay_module
        overlay_module.PREFERENCES_PATH = Path(__file__).resolve().parents[1] / ".local" / "profiles" / (args.profile + ".json")
    bridge = None
    if args.gdb_port and not args.preview:
        from src.rpc.gdb_bridge import GDBBridge
        try:
            if not 1024 <= args.gdb_port <= 65535:
                raise ValueError("Port local invalide")
            if args.rpc_port:
                if not 1024<=args.rpc_port<=65535:
                    raise ValueError('Port privé invalide')
                from src.rpc.hybrid_bridge import HybridBridge
                bridge=HybridBridge(args.rpc_port,args.gdb_port,int(args.title_id or '',16),args.process_name)
            else:
                bridge = GDBBridge(args.gdb_port, int(args.title_id or "", 16), args.process_name)
        except (ValueError, TypeError) as exc:
            parser.error(str(exc))
    app = QApplication(sys.argv)
    app.setApplicationName("ALBW Online Co-op")
    control_server = None
    if not args.preview:
        from src.companion_control import listen
        control_server = listen(app,args.profile,lambda: overlay.toggle_subscreen()
                                if not overlay.subscreen_open else overlay.activateWindow())
        if control_server is None:
            if bridge:
                bridge.close()
            return
    print_banner(args.preview)
    orchestrator = None if args.preview else CoopOrchestrator(bridge,args.profile,args.emulator_pid,
                                                           enemy_authority=args.enemy_authority)
    if orchestrator:
        orchestrator.online_progress.auto=args.host_progress
        saved_args=[]
        for key in ('profile','gdb_port','rpc_port','title_id','process_name'):
            value=getattr(args,key)
            if value is not None:saved_args.extend(['--'+key.replace('_','-'),str(value)])
        saved_args.append('--enemy-authority' if args.enemy_authority else '--no-enemy-authority')
        orchestrator.online_progress.launch_args=saved_args
    if orchestrator and args.profile:
        status_dir = Path(__file__).resolve().parents[1] / ".local/profiles"
        status_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.status_path = status_dir / (args.profile + ".status.json")
    overlay = AzaharNativeOverlay(orchestrator=orchestrator, preview_only=args.preview,
                                  target_emulator_pid=args.emulator_pid)
    if args.demo_party:
        overlay.preview_players = True
        overlay.state_cache.update(in_game=True, rpc_connected=True)
        overlay.refresh_state()
        overlay.sanctuary.select_page(2)
    overlay.show()
    if args.emulator_pid and not args.preview:
        # The managed companion exits with its own emulator, even if no game
        # window was created (e.g. a missing runtime dependency).
        from src.overlay.window_tracker import _process_path
        lifetime = QTimer(app)
        lifetime.timeout.connect(lambda: app.quit() if not _process_path(args.emulator_pid).endswith(
            ("\\azahar.exe", "\\azahar-qt.exe")) else None)
        lifetime.start(1500)
    if orchestrator:
        orchestrator.overlay = overlay
        app.aboutToQuit.connect(orchestrator.shutdown)
        orchestrator.add_log("Menu prêt. En attente d’ALBW dans Azahar.")
        sync_thread = threading.Thread(target=orchestrator.sync_loop, daemon=True)
        sync_thread.start()
        threading.Thread(target=orchestrator.present_loop, daemon=True).start()
        if args.host_port:
            orchestrator.start_hosting(args.host_port)
        elif args.join:
            if not orchestrator.join_target(args.join):
                orchestrator.add_log("Adresse ou code de salon invalide.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
