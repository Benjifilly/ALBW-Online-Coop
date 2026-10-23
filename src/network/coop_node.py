"""Small host-authoritative UDP lobby for up to four ALBW companions.

Handshake, session epochs, ordered snapshots and acknowledged health events keep
late packets and retries from applying an old state to a new session.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import math
import secrets
import socket
import threading
import time
from src.memory.albw_state import PlayerSnapshot
from src.network.world_journal import HostWorldJournal,ClientWorldJournal,room_key,integer,MAX_EVENTS
from src.network.enemy_frames import EnemyFrames,context_for_wire
from src.network.enemy_hub import EnemyHub
from src.network.shared_loot import HostLootJournal,ClientLootJournal
from src.network.shared_progress import HostProgressJournal,ClientProgressJournal
from src.version import VERSION,mismatch
from src.network.quality import LinkQuality
from src.network.datagrams import Datagrams,encode as datagrams

DEFAULT_COOP_UDP_PORT = 45990
PROTOCOL_VERSION = 20
MAX_PLAYERS = 4
PEER_TIMEOUT = 6.0
MAX_DATAGRAM = 16384
PROBE_PAD = 96
COLORS = {"green", "blue", "red", "purple"}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_player(data):
    if not isinstance(data, dict):
        return None
    pid, name, color = data.get("player_id"), data.get("name"), data.get("tunic_color")
    if not isinstance(pid, str) or not 1 <= len(pid) <= 64:
        return None
    if not isinstance(name, str) or not name.strip() or len(name) > 24 or any(ord(c) < 32 for c in name):
        return None
    if not isinstance(color, str) or color not in COLORS or type(data.get("in_game")) is not bool:
        return None
    if 'pose_verified' in data and type(data['pose_verified']) is not bool:
        return None
    render=None
    if data.get('render_state'):
        from src.memory.render_state import validate_render_state
        render=validate_render_state(data['render_state'])
        if render is None:return None
        wall=render.get('wall')
        if isinstance(wall,dict) and wall.get('surface',{}).get('triangle_count',0)>12:
            # A peer cannot expand every roster past the datagram budget.
            wall.pop('surface')
    elif 'render_state' in data and data['render_state']!={}:return None
    for key in ('animation_id','upper_animation_id'):
        if key in data and not (_integer(data[key],0,175) or type(data[key]) is int and data[key]==65535):
            return None
    if data.get('life','alive') not in ('alive','dead','ghost'):
        return None
    if data.get('life_party'):
        from src.network.party_life import status
        if status(data['life_party']) is None:return None
    elif data.get('life_party',{})!={}:return None
    ident=data.get('life_id','')
    if not isinstance(ident,str) or (ident and (len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident))):return None
    for key, high in (('entrance', 0xFFFF), ('revive_serial', 2**31), ('release_serial', 2**31)):
        if key in data and not _integer(data[key], 0, high):
            return None
    for key, low, high in (("health", 0, 80), ("max_health", 12, 80), ("course", 0, 23),
                            ("stage", 0, 65535), ("rupees", 0, 9999), ("maiamai", 0, 100)):
        if not _integer(data.get(key), low, high):
            return None
    if data["max_health"] % 4 or data["health"] > data["max_health"]:
        return None
    for key in ("x", "y", "z", "yaw"):
        value = data.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e7:
            return None
    inventory, flags, cflags = data.get("inventory"), data.get("event_flags_hex", ""), data.get("course_flags_hex", "")
    if not isinstance(inventory, list) or len(inventory) != 50 or not all(_integer(v, 0, 0xFFFFFFFF) for v in inventory):
        return None
    if not isinstance(flags, str) or len(flags) not in (0, 256):
        return None
    try:
        bytes.fromhex(flags)
    except ValueError:
        return None
    if not isinstance(cflags, str) or len(cflags) not in (0, 64, 96):
        return None
    try:
        bytes.fromhex(cflags)
    except ValueError:
        return None
    fields = PlayerSnapshot.__dataclass_fields__
    clean = {key: deepcopy(data[key]) for key in fields if key in data and key != "unlocked_milestones"}
    clean["name"] = name.strip()
    if render is not None:clean['render_state']=render
    return clean


class CoopNetworkNode:
    def __init__(self, player_id, player_name, tunic_color="green", on_event_log=None,enemy_authority=False,*,version=VERSION):
        self.player_id, self.player_name, self.tunic_color = player_id, player_name, tunic_color
        self.on_event_log = on_event_log
        self.version=version;self.connection_detail='';self.quality=LinkQuality()
        self._lock = threading.RLock()
        from src.network.save_transfer import SaveTransfer
        self.save_transfer=SaveTransfer(self)
        self._sock = self._thread = None
        self._datagrams=Datagrams()
        self._running = False
        self.mode = "offline"
        self.bind_port = DEFAULT_COOP_UDP_PORT
        self.host_target = None
        self.connection_state = "offline"
        self.session_id = ""
        self._join_nonce = ""
        self.peers = {}
        self._members = {}
        self._peer_series = {}
        self._own_state = asdict(PlayerSnapshot(player_id=player_id, name=player_name, tunic_color=tunic_color))
        self._sequence = 0
        self._last_roster_seq = -1
        self._last_host_seen = 0
        self._party_world_reset=''
        self._last_hello = self._last_ping = 0
        self._pings = {}
        self.latency_ms = None
        self._soul_link = False
        self.health_epoch = ""
        self.shared_health = None
        self.health_revision = 0
        self._health_seq = 0
        self._health_acked = {}
        self._pending_health = []
        self._room_authorities = {}
        self._world_host=HostWorldJournal()
        self._world_client=ClientWorldJournal()
        self._world_rooms={}
        self._world_session=''
        self._next_world_send=0
        self._loot_host=HostLootJournal();self._loot_client=ClientLootJournal();self._next_loot_send=0
        self._progress_host=HostProgressJournal();self._progress_client=ClientProgressJournal();self._next_progress_send=0
        self.enemy_authority=enemy_authority is True
        self.enemy_channel=EnemyFrames();self.enemy_hub=EnemyHub();self._enemy_report=None;self._next_enemy_send=0

    @property
    def soul_link(self):
        return self._soul_link

    @soul_link.setter
    def soul_link(self, enabled):
        self.set_soul_link(enabled)

    def _log(self, message):
        if self.on_event_log:
            self.on_event_log(message)

    def _start(self, port, mode):
        if not _integer(port, 0, 65535):
            return False
        self.stop()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", port))
            sock.settimeout(.05)
        except OSError as exc:
            if "sock" in locals():
                sock.close()
            self._log(f"Impossible d’ouvrir le salon : {exc}")
            return False
        self._sock, self.mode, self._running = sock, mode, True
        self.bind_port = sock.getsockname()[1]
        return True

    def _start_worker(self):
        # Capture the socket; a previous worker can never read a replacement socket.
        self._thread = threading.Thread(target=self._recv_loop, args=(self._sock,), daemon=True)
        self._thread.start()

    def start_host(self, port=DEFAULT_COOP_UDP_PORT):
        if not self._start(port, "host"):
            return False
        self.session_id = secrets.token_hex(16)
        self.connection_state = "hosting"
        self._start_worker()
        self._log(f"Salon ouvert sur le port {self.bind_port}. Jusqu’à 4 joueurs.")
        return True

    def join_host(self, host_ip, host_port=DEFAULT_COOP_UDP_PORT, local_port=0):
        if not isinstance(host_ip, str) or not _integer(host_port, 1, 65535):
            return False
        try:
            socket.inet_pton(socket.AF_INET, host_ip)
        except OSError:
            return False
        if not self._start(local_port, "client"):
            return False
        self.host_target = (host_ip, host_port)
        self._join_nonce = secrets.token_hex(16)
        self.connection_state = "connecting"
        self._start_worker()
        self._log(f"Connexion à {host_ip}:{host_port}…")
        return True

    def stop(self):
        sock, worker = self._sock, self._thread
        if sock:
            with self._lock:
                endpoints = list(self._members) if self.mode == "host" else ([self.host_target] if self.host_target else [])
                for endpoint in endpoints:
                    self._send_packet({"type": "BYE", "session": self.session_id, "player_id": self.player_id}, endpoint)
        self._running = False
        self._sock = None
        if sock:
            sock.close()
        if worker and worker is not threading.current_thread():
            worker.join(.4)
        with self._lock:
            self._thread = None
            self.mode = self.connection_state = "offline"
            self.host_target = None
            self.session_id = ""
            self.peers.clear()
            self._members.clear()
            self._peer_series.clear()
            self._datagrams=Datagrams()
            self._room_authorities.clear()
            self._pings.clear()
            self._pending_health.clear()
            self._health_acked.clear()
            self._world_host=HostWorldJournal()
            self._world_client.reset();self._world_rooms={};self._world_session='';self._next_world_send=0
            self._loot_host=HostLootJournal();self._loot_client.reset();self._next_loot_send=0
            self._progress_host=HostProgressJournal();self._progress_client.reset();self._next_progress_send=0
            self.enemy_channel.select(None);self.enemy_hub=EnemyHub();self._enemy_report=None
            self._sequence, self._last_roster_seq = 0, -1
            self._last_hello = self._last_ping = self._last_host_seen = 0
            self.latency_ms = self.shared_health = None
            self.quality=LinkQuality();self.connection_detail=''
            self.save_transfer.clear()
            self._soul_link = False
            self.health_epoch = ""
            self._health_seq = self.health_revision = 0

    def _send_packet(self, payload, endpoint):
        sock = self._sock
        if not sock or not self._running or not endpoint:
            return
        try:
            from src.memory.fresco_wire import player_for_wire
            payload=dict(payload)
            if 'player' in payload:payload['player']=player_for_wire(payload['player'])
            if 'players' in payload:payload['players']=[player_for_wire(p) for p in payload['players']]
            raw = json.dumps(dict(payload, v=PROTOCOL_VERSION), separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(raw)>MAX_DATAGRAM:
                # Optional visual data must never suppress the whole roster.
                # Work on a copy; retain flying beams before short sword trails.
                payload=deepcopy(payload)
                players=payload.get('players',[])+([payload['player']] if 'player' in payload else [])
                while len(raw)>MAX_DATAGRAM:
                    candidates=[(p,e) for p in players for e in p.get('render_state',{}).get('effects',[])]
                    if not candidates:break
                    player,effect=next(((p,e) for p,e in candidates if e['bank']==0),candidates[-1])
                    player['render_state']['effects'].remove(effect)
                    raw=json.dumps(dict(payload,v=PROTOCOL_VERSION),separators=(',',':'),allow_nan=False).encode('utf-8')
                for player in players:
                    if len(raw)<=MAX_DATAGRAM:break
                    player.get('render_state',{}).get('wall',{}).pop('surface',None)
                    raw=json.dumps(dict(payload,v=PROTOCOL_VERSION),separators=(',',':'),allow_nan=False).encode('utf-8')
                # Dense cosmetic snapshots must not suppress the room/player
                # state. These lists are optional and refreshed in the next
                # roster; commands and shared damage use their own journals.
                for key in ('items','bombs'):
                    while len(raw)>MAX_DATAGRAM:
                        rows=next((p['render_state'][key] for p in players
                            if p.get('render_state',{}).get(key)),None)
                        if rows is None:break
                        rows.pop()
                        raw=json.dumps(dict(payload,v=PROTOCOL_VERSION),separators=(',',':'),allow_nan=False).encode('utf-8')
            if len(raw) <= MAX_DATAGRAM:
                for part in datagrams(raw):sock.sendto(part, endpoint)
        except (OSError, ValueError, TypeError):
            pass

    def broadcast_state(self, snapshot):
        clean = validate_player(asdict(snapshot))
        if clean is None:
            return
        with self._lock:
            self._own_state = clean
            if self.mode == "host":
                if self._soul_link and not clean["in_game"]:
                    self.set_soul_link(False)
                    self._log("Cœurs liés désactivés : la partie de l’hôte n’est plus disponible.")
                self._publish_roster()
            elif self.mode == "client" and self.connection_state == "connected":
                self._sequence += 1
                self._send_packet({"type": "STATE", "session": self.session_id, "seq": self._sequence,
                    "nonce":self._join_nonce,
                    "player": clean, "health_epoch": self.health_epoch,
                    "health_events": list(self._pending_health)}, self.host_target)

    def _publish_roster(self):
        self._sequence += 1
        players = [self._own_state] + list(self.peers.values())
        from src.network.party_life import completed_return
        returned,reset=completed_return(players)
        if returned and returned!=self._party_world_reset:
            self._party_world_reset=returned
            if reset:
                self._world_host.rooms.clear();self.enemy_hub=EnemyHub()
                self._enemy_report=None;self.enemy_channel.select(None)
                if self._soul_link:
                    self.health_epoch=secrets.token_hex(8);self.health_revision=0
                    self.shared_health=min(p['health'] for p in players if p.get('in_game'))
                    self._pending_health.clear();self._health_acked.clear();self._health_seq=0
        if self._soul_link and self.shared_health > self._health_cap():
            self.shared_health = self._health_cap()
            self.health_revision += 1
        rooms = {}
        for player in players:
            if player["in_game"]:
                room = f"{player['course']}:{player['stage']}"
                rooms.setdefault(room, []).append(player["player_id"])
        self._room_authorities = {room: self._room_authorities.get(room) if self._room_authorities.get(room) in ids else ids[0]
                                  for room, ids in rooms.items()}
        self._world_host.update_rooms(players)
        self._world_rooms=self._world_host.contexts()
        self._select_world_context()
        payload = {"type": "ROSTER", "session": self.session_id, "seq": self._sequence,
                   "host_id": self.player_id, "players": players, "authorities": self._room_authorities,
                   "soul_link": self._soul_link, "health_epoch": self.health_epoch,
                   "shared_health": self.shared_health, "health_revision": self.health_revision,
                   "health_acked": self._health_acked,"world_rooms":self._world_rooms}
        for endpoint,member in self._members.items():
            self._send_packet(dict(payload,nonce=member['nonce']), endpoint)

    def set_soul_link(self, enabled):
        with self._lock:
            if self.mode != "host" or (enabled and not self._own_state.get("in_game")):
                return False
            enabled = bool(enabled)
            if enabled != self._soul_link:
                self._soul_link = enabled
                self.health_epoch = secrets.token_hex(8)
                self.shared_health = min(self._own_state["health"], self._health_cap()) if enabled else None
                self.health_revision = 0
                self._health_acked.clear()
                self._pending_health.clear()
                self._health_seq = 0
                self._publish_roster()
            return True

    def send_health_delta(self, delta, epoch=None):
        with self._lock:
            if (not self._soul_link or not _integer(delta, -80, 80) or delta == 0
                    or (epoch is not None and epoch != self.health_epoch)):
                return
            if self.mode != "host" and (self.connection_state != "connected" or len(self._pending_health) >= 64):
                return
            self._health_seq += 1
            event = {"id": self._health_seq, "delta": delta}
            if self.mode == "host":
                self._accept_health_events(self.player_id, [event], self.health_epoch)
            elif self.connection_state == "connected" and len(self._pending_health) < 64:
                self._pending_health.append(event)

    def _accept_health_events(self, pid, events, epoch):
        if not self._soul_link or epoch != self.health_epoch or not isinstance(events, list) or len(events) > 64:
            return
        for event in events:
            if not isinstance(event, dict) or not _integer(event.get("id"), 1, 2**53) or not _integer(event.get("delta"), -80, 80):
                return
            last = self._health_acked.get(pid, 0)
            if event["id"] <= last:
                continue
            if event["id"] != last + 1:
                break
            self.shared_health = max(0, min(self._health_cap(), self.shared_health + event["delta"]))
            self._health_acked[pid] = event["id"]
            self.health_revision += 1

    def _health_cap(self):
        # A common pool cannot exceed any active player's own heart capacity.
        return min([self._own_state["max_health"]] + [p["max_health"] for p in self.peers.values() if p["in_game"]])

    def get_health_state(self):
        with self._lock:
            return self._soul_link, self.health_epoch, self.health_revision, self.shared_health

    def get_projected_health(self):
        with self._lock:
            if not self._soul_link or self.shared_health is None:
                return None
            return self.shared_health + sum(event["delta"] for event in self._pending_health)

    def get_room_authority(self, course, stage):
        with self._lock:
            return self._room_authorities.get(f"{course}:{stage}", self.player_id)

    def _select_world_context(self):
        player=self._own_state
        room=(player['course'],player['stage'])
        context=self._world_rooms.get(room_key(room)) if player['in_game'] else None
        self._world_client.select(room,context['epoch'] if context else None,active_contexts=self._world_rooms)

    def get_world_context(self):
        with self._lock:return deepcopy(self._world_client.context)

    def get_world_room_context(self,room):
        with self._lock:return deepcopy(self._world_rooms.get(room_key(room)))

    def queue_world_observation(self,event,context=None):
        """Queue into the journal of `context` only.

        The network thread may switch the selected room at any time; an
        observation of the previous room must never land in the new one.
        """
        with self._lock:
            if context is not None and self._world_client.context!=tuple(context):return False
            return self._world_client.observe(event)

    def get_mass_manifest(self):
        with self._lock:return self._world_client.mass_manifest()

    def queue_loot_observation(self,event):
        with self._lock:return self._loot_client.observe(event)

    def get_loot_events(self,after=0):
        with self._lock:return deepcopy(self._loot_client.events[after:])

    def _loot_tick(self,now):
        if now<self._next_loot_send:return
        self._next_loot_send=now+.05
        outgoing=self._loot_client.outgoing()
        if self.mode=='host':
            self._loot_host.accept(self.player_id,outgoing['events'],self._world_rooms)
            reply=self._loot_host.delta(self.player_id,outgoing['cursor'])
            if reply:self._loot_client.receive(reply)
        elif self.mode=='client' and self.connection_state=='connected':
            self._send_packet(dict(outgoing,type='LOOT_INPUT',session=self.session_id,nonce=self._join_nonce),self.host_target)

    def queue_progress_observation(self,event):
        with self._lock:return self._progress_client.observe(event)

    def get_progress_events(self,after=0):
        with self._lock:return deepcopy(self._progress_client.events[after:])

    def _progress_tick(self,now):
        if now<self._next_progress_send:return
        self._next_progress_send=now+.05
        outgoing=self._progress_client.outgoing()
        if self.mode=='host':
            self._progress_host.accept(self.player_id,outgoing['events'])
            reply=self._progress_host.delta(self.player_id,outgoing['cursor'])
            if reply:self._progress_client.receive(reply)
        elif self.mode=='client' and self.connection_state=='connected':
            self._send_packet(dict(outgoing,type='PROGRESS_INPUT',session=self.session_id,nonce=self._join_nonce),self.host_target)

    def next_world_event(self,context=None):
        with self._lock:
            if context is not None and self._world_client.context!=tuple(context):return None
            return self._world_client.next_event()

    def acknowledge_world_event(self,revision,context=None):
        with self._lock:
            if context is not None and self._world_client.context!=tuple(context):return False
            return self._world_client.acknowledge(revision)

    def rewind_world_scene(self):
        with self._lock:
            # A new native scene needs the ordered history again, even when
            # another participant kept that logical room alive on the host.
            self._world_client.cursor=0;self._world_client.delivery=[]

    def _world_tick(self,now):
        if now<self._next_world_send:return
        self._next_world_send=now+.05
        self._select_world_context()
        payload=self._world_client.outgoing()
        if payload is None:return
        if self.mode=='host':
            self._world_host.accept(self.player_id,payload['room'],payload['epoch'],payload['events'])
            delta=self._world_host.delta(self.player_id,payload['room'],payload['epoch'],payload['cursor'])
            if delta:self._world_client.receive(delta)
        elif self.mode=='client' and self.connection_state=='connected':
            self._send_packet(dict(payload,type='WORLD_INPUT',session=self.session_id,nonce=self._join_nonce),self.host_target)

    def get_active_peers(self):
        with self._lock:
            return deepcopy(list(self.peers.values()))

    def get_life_peers(self):
        """Un HELLO/WELCOME seul ne prouve pas que le groupe est tombé."""
        with self._lock:
            ready=bool(self.session_id) and (self.mode=='host' and self.connection_state=='hosting'
                or self.mode=='client' and self.connection_state=='connected' and self._last_roster_seq>=0
                and time.monotonic()-self._last_host_seen<=PEER_TIMEOUT)
            return deepcopy(list(self.peers.values())),ready

    def _room_context(self,player):
        """(room, world epoch) of an in-game player, or None."""
        if not player or not player.get('in_game'):return None
        room=(player['course'],player['stage']);world=self._world_rooms.get(room_key(room))
        return (room,world['epoch']) if world else None

    def get_enemy_context(self):
        """This player's room for the shared enemies, whoever else is there."""
        with self._lock:
            ready=(self.enemy_authority and (bool(self.session_id) or bool(self._world_session))
                   and (self.mode=='host' or self.connection_state in ('connected','connecting')))
            return self.enemy_channel.select(self._room_context(self._own_state) if ready else None)

    def get_authoritative_enemies(self):
        """Latest shared states of this room (owner, ledger HP, owner's pose)."""
        with self._lock:
            if self.mode=='client' and self.connection_state!='connected':return None
            if not self.get_enemy_context() or time.monotonic()-self.enemy_channel.updated>.5:return None
            return deepcopy(self.enemy_channel.states)

    def queue_enemy_damage(self,attacks):
        with self._lock:
            return bool(self.get_enemy_context() and self.enemy_channel.queue(attacks))

    def pending_enemy_damage_keys(self):
        with self._lock:
            if not self.get_enemy_context():return set()
            return {a['key'] for a in self.enemy_channel.pending}

    def publish_enemy_report(self,report):
        """This game's view of its room, sent with the next enemy tick."""
        with self._lock:
            if not self.get_enemy_context():return False
            self._enemy_report=deepcopy(report);return True

    def _enemy_positions(self,context):
        players=[self._own_state]+list(self.peers.values())
        # A spectator's game never simulates the room's enemies.
        return {p['player_id']:(p['x'],p['z']) for p in players
                if self._room_context(p)==context and p.get('life','alive')!='ghost'}

    def _enemy_tick(self,now):
        if now<self._next_enemy_send:return
        self._next_enemy_send=now+1/30
        context=self.get_enemy_context()
        report=self.enemy_channel.report(self._enemy_report) if context and self._enemy_report else None
        if self.mode=='host':
            contexts={(tuple(int(v) for v in room.split(':')),value['epoch']) for room,value in self._world_rooms.items()}
            self.enemy_hub.retain(contexts)
            if report:self.enemy_hub.accept(self.player_id,context,report,now)
            if not self.enemy_authority:return
            # Every room with someone in it, the host's own room or not.
            own=self._room_context(self._own_state)
            if own:
                payload=self.enemy_hub.frame(self.player_id,own,self._enemy_positions(own),now)
                if payload:self.enemy_channel.receive(payload,self.player_id)
            for endpoint,member in self._members.items():
                player=self.peers.get(member['pid']);room=self._room_context(player)
                payload=room and self.enemy_hub.frame(member['pid'],room,self._enemy_positions(room),now)
                if payload:
                    self._send_packet(dict(payload,type='ENEMY_STATE',session=self.session_id,nonce=member['nonce']),endpoint)
        elif report and self.connection_state=='connected':
            self._send_packet(dict(type='ENEMY_REPORT',session=self.session_id,nonce=self._join_nonce,
                                  context=context,report=report),self.host_target)

    def get_connection_info(self):
        with self._lock:
            return {"connection_state": self.connection_state, "latency_ms": self.latency_ms,
                    "connected_players": len(self.peers) + 1, "session_id": self.session_id,
                    "version":self.version,"connection_detail":self.connection_detail,**self.quality.summary()}

    def _tick(self):
        with self._lock:
            now = time.monotonic()
            if self.connection_state in ('hosting','connected'):self.save_transfer.tick(now)
            self._world_tick(now)
            self._loot_tick(now)
            self._progress_tick(now)
            self._enemy_tick(now)
            if self.mode == "host":
                for endpoint, member in list(self._members.items()):
                    if now - member["last_seen"] > PEER_TIMEOUT:
                        self._remove_member(endpoint)
            elif self.mode == "client":
                if self.connection_state == "connected" and now - self._last_host_seen > PEER_TIMEOUT:
                    self.peers.clear()
                    self.session_id = ""
                    self._soul_link = False
                    self.shared_health = None
                    self.connection_state = "connecting"
                    self._log("Liaison perdue. Reconnexion automatique…")
                if self.connection_state in ('connecting','duplicate') and now - self._last_hello >= 1:
                    self._last_hello = now
                    self._send_packet({"type": "HELLO", "nonce": self._join_nonce,
                        "build":self.version,
                        "enemy_authority":self.enemy_authority,
                        "player": dict(self._own_state,name=self.player_name,tunic_color=self.tunic_color)}, self.host_target)
            if now - self._last_ping >= 1:
                self._last_ping = now
                endpoints = list(self._members) if self.mode == "host" else ([self.host_target] if self.connection_state == "connected" else [])
                for endpoint in endpoints:
                    ident = secrets.token_hex(8)
                    self._pings[(endpoint, ident)] = now
                    self._send_packet({"type": "PING", "session": self.session_id, "id": ident}, endpoint)
                for key,stamp in list(self._pings.items()):
                    if now-stamp>=2.5:
                        self.quality.record(key[0],False);self._pings.pop(key,None)

    def _remove_member(self, endpoint):
        member = self._members.pop(endpoint, None)
        if member:
            self.quality.forget(endpoint)
            self._pings={key:stamp for key,stamp in self._pings.items() if key[0]!=endpoint}
            self.save_transfer.jobs.pop(endpoint,None);self.save_transfer.outgoing.pop(endpoint,None)
            player = self.peers.pop(member["pid"], {})
            # Une coupure conserve les acquittements ; seul un nouveau nonce les remet à zéro.
            self._log(f"{player.get('name', 'Un compagnon')} a quitté le salon.")
            self._publish_roster()

    def _handle_packet(self, msg, addr):
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            return
        kind = msg["type"]
        with self._lock:
            # Title-screen reachability probe: answered, never registered. Its
            # padding keeps the reply smaller than the request (no amplifier).
            if self.mode == "host" and kind == "PROBE":
                ident, pad = msg.get("id"), msg.get("pad")
                if isinstance(ident, str) and len(ident) == 16 and isinstance(pad, str) and len(pad) >= PROBE_PAD:
                    self._send_packet({"type": "PROBE_REPLY", "id": ident,
                                       "build":self.version,"count": 1 + len(self._members), "max": MAX_PLAYERS}, addr)
                return
            # Un refus d'une autre version doit rester lisible avant le filtre.
            if (self.mode=='client' and addr==self.host_target and kind=='REJECT'
                    and msg.get('nonce')==self._join_nonce and msg.get('reason')=='incompatible'):
                if self.connection_state!='incompatible':
                    self.connection_detail=mismatch(msg.get('build'),self.version);self._log(self.connection_detail)
                self.connection_state='incompatible';return
            if msg.get("v") != PROTOCOL_VERSION:
                if self.mode == "host" and kind == "HELLO":
                    self._send_packet({"type": "REJECT", "reason": "incompatible", "build":self.version,"nonce": msg.get("nonce")}, addr)
                return
            if self.mode == "host" and kind == "HELLO":
                if msg.get('build')!=self.version:
                    self._send_packet(dict(type='REJECT',reason='incompatible',build=self.version,nonce=msg.get('nonce')),addr);return
                player = validate_player(msg.get("player"))
                nonce = msg.get("nonce")
                if player is None or not isinstance(nonce, str) or len(nonce) != 32 or player["player_id"] == self.player_id:
                    return
                enemies=msg.get('enemy_authority',False)
                if type(enemies) is not bool or enemies!=self.enemy_authority:
                    self._send_packet({'type':'REJECT','reason':'incompatible','build':self.version,'nonce':nonce},addr)
                    return
                if addr not in self._members and len(self._members) >= MAX_PLAYERS - 1:
                    self._send_packet({"type": "REJECT", "reason": "full", "nonce": nonce}, addr)
                    return
                pid = player["player_id"]
                if any(m["pid"] == pid and ep != addr for ep, m in self._members.items()):
                    self._send_packet({"type": "REJECT", "reason": "duplicate", "nonce": nonce}, addr)
                    return
                fresh = addr not in self._members
                if not fresh and self._members[addr]["pid"] != pid:
                    self._remove_member(addr)
                    fresh = True
                if fresh or self._members[addr]["nonce"] != nonce:
                    new_series=self._peer_series.get(pid)!=nonce
                    self._peer_series[pid]=nonce
                    self._members[addr] = {"pid": pid, "nonce": nonce, "seq": -1, "last_seen": time.monotonic()}
                    self.peers[pid] = player
                    if new_series:
                        self.enemy_hub.forget(pid)
                        self._health_acked.pop(pid, None)
                        for state in self._world_host.rooms.values():state['acked'].pop(pid,None)
                        self._loot_host.acked.pop(pid,None)
                        self._progress_host.acked.pop(pid,None)
                self._send_packet({"type": "WELCOME", "nonce": nonce, "session": self.session_id,
                                   "build":self.version,"enemy_authority":self.enemy_authority}, addr)
                if fresh:
                    self._log(f"{player['name']} a rejoint le salon.")
                self._publish_roster()
                return
            if self.mode == "client":
                if addr != self.host_target:
                    return
                if kind == "REJECT" and msg.get("nonce") == self._join_nonce:
                    self.connection_state = msg.get("reason") if msg.get("reason") in ("full", "incompatible", "duplicate") else "rejected"
                    self._log({"full": "Le salon est complet.", "incompatible": "Les compagnons utilisent des versions incompatibles.",
                               "duplicate": "Ce profil est déjà connecté."}.get(self.connection_state, "Connexion refusée."))
                    return
                if kind == "WELCOME" and msg.get("nonce") == self._join_nonce:
                    if msg.get('build',self.version)!=self.version:
                        self.connection_state='incompatible';self.connection_detail=mismatch(msg.get('build'),self.version);return
                    if msg.get('enemy_authority',False) is not self.enemy_authority:return
                    session = msg.get("session")
                    if not isinstance(session, str) or len(session) != 32:
                        return
                    if session != self._world_session:
                        self.save_transfer.clear()
                        self._last_roster_seq = -1
                        self.peers.clear()
                        self._pending_health.clear()
                        self._health_seq = 0
                    if session!=self._world_session:
                        self._world_client.reset();self._world_rooms={};self._world_session=session
                        self._loot_client.reset();self._progress_client.reset()
                        self.enemy_channel.select(None)
                    self.session_id, self.connection_state = session, "connected"
                    self._last_host_seen = time.monotonic()
                    return
            if not self.session_id or msg.get("session") != self.session_id:
                return
            if self.mode == "host" and addr not in self._members:
                return
            if kind.startswith('SAVE_'):
                self.save_transfer.handle(msg,addr);return
            if self.mode=='host' and kind=='ENEMY_REPORT':
                member=self._members[addr];context=self._room_context(self.peers.get(member['pid']))
                if (self.enemy_authority and context and msg.get('nonce')==member['nonce']
                        and context_for_wire(msg.get('context'))==context):
                    self.enemy_hub.accept(member['pid'],context,msg.get('report'),time.monotonic())
                return
            if self.mode=='client' and kind=='ENEMY_STATE':
                if self.get_enemy_context() and msg.get('nonce')==self._join_nonce:
                    self.enemy_channel.receive(msg,self.player_id)
                return
            if self.mode=='host' and kind=='WORLD_INPUT':
                member=self._members[addr];player=self.peers.get(member['pid'])
                room=msg.get('room');epoch=msg.get('epoch')
                if (msg.get('nonce')!=member['nonce'] or not player or not player['in_game']
                        or room!=[player['course'],player['stage']]):return
                self._world_host.accept(member['pid'],room,epoch,msg.get('events'))
                delta=self._world_host.delta(member['pid'],room,epoch,msg.get('cursor'))
                if delta:self._send_packet(dict(delta,type='WORLD_DELTA',session=self.session_id,nonce=member['nonce']),addr)
                return
            if self.mode=='host' and kind=='LOOT_INPUT':
                member=self._members[addr]
                if msg.get('nonce')!=member['nonce']:return
                self._loot_host.accept(member['pid'],msg.get('events'),self._world_rooms)
                reply=self._loot_host.delta(member['pid'],msg.get('cursor'))
                if reply:self._send_packet(dict(reply,type='LOOT_DELTA',session=self.session_id,nonce=member['nonce']),addr)
                return
            if self.mode=='client' and kind=='LOOT_DELTA':
                if msg.get('nonce')==self._join_nonce:self._loot_client.receive(msg)
                return
            if self.mode=='host' and kind=='PROGRESS_INPUT':
                member=self._members[addr]
                if msg.get('nonce')!=member['nonce']:return
                self._progress_host.accept(member['pid'],msg.get('events'))
                reply=self._progress_host.delta(member['pid'],msg.get('cursor'))
                if reply:self._send_packet(dict(reply,type='PROGRESS_DELTA',session=self.session_id,nonce=member['nonce']),addr)
                return
            if self.mode=='client' and kind=='PROGRESS_DELTA':
                if msg.get('nonce')==self._join_nonce:self._progress_client.receive(msg)
                return
            if self.mode=='client' and kind=='WORLD_DELTA':
                if msg.get('nonce')!=self._join_nonce:return
                self._world_client.receive({key:value for key,value in msg.items() if key not in ('v','type','session','nonce')})
                return
            if kind in ("PING", "PONG"):
                ident = msg.get("id")
                if not isinstance(ident, str) or len(ident) != 16:
                    return
                if kind == "PING":
                    self._send_packet({"type": "PONG", "session": self.session_id, "id": ident}, addr)
                elif (stamp := self._pings.pop((addr, ident), None)) is not None:
                    self.latency_ms = round((time.monotonic() - stamp) * 1000)
                    self.quality.record(addr,True,self.latency_ms)
                if self.mode == "host":
                    self._members[addr]["last_seen"] = time.monotonic()
                else:
                    self._last_host_seen = time.monotonic()
                return
            if kind == "BYE":
                if self.mode == "host":
                    self._remove_member(addr)
                else:
                    self.peers.clear()
                    self._soul_link = False
                    self.shared_health = None
                    self.connection_state = "connecting"
                    self.session_id = ""
                    self._log("L’hôte a fermé le salon. En attente de son retour…")
                return
            if self.mode == "host" and kind == "STATE":
                member = self._members[addr]
                if msg.get('nonce')!=member['nonce']:return
                seq = msg.get("seq")
                player = validate_player(msg.get("player"))
                if not _integer(seq, 0, 2**53) or seq <= member["seq"] or player is None or player["player_id"] != member["pid"]:
                    return
                member.update(seq=seq, last_seen=time.monotonic())
                self.peers[member["pid"]] = player
                if player["in_game"]:
                    self._accept_health_events(member["pid"], msg.get("health_events", []), msg.get("health_epoch"))
                self._publish_roster()
                return
            if self.mode == "client" and kind == "ROSTER":
                if msg.get('nonce')!=self._join_nonce:return
                seq, raw = msg.get("seq"), msg.get("players")
                if not _integer(seq, 0, 2**53) or seq <= self._last_roster_seq or not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PLAYERS:
                    return
                players = [validate_player(p) for p in raw]
                if any(p is None for p in players) or len({p["player_id"] for p in players}) != len(players):
                    return
                ids = {p["player_id"] for p in players}
                if self.player_id not in ids or msg.get("host_id") not in ids:
                    return
                enabled, health, epoch = msg.get("soul_link"), msg.get("shared_health"), msg.get("health_epoch")
                if type(enabled) is not bool or not isinstance(epoch, str) or (enabled and (len(epoch) != 16 or not _integer(health, 0, 80))):
                    return
                acked, authorities = msg.get("health_acked"), msg.get("authorities")
                world_rooms=msg.get('world_rooms',{})
                if not isinstance(world_rooms,dict) or len(world_rooms)>MAX_PLAYERS:return
                for room,value in world_rooms.items():
                    try:valid=room_key([int(v) for v in room.split(':')])==room
                    except (ValueError,AttributeError):return
                    if (not valid or not isinstance(value,dict) or set(value)!={'epoch','revision','error'}
                            or not isinstance(value['epoch'],str) or len(value['epoch'])!=16
                            or not integer(value['revision'],0,MAX_EVENTS) or value['error'] not in ('','journal_full')):return
                revision = msg.get("health_revision")
                if not isinstance(acked, dict) or not isinstance(authorities, dict) or not _integer(revision, 0, 2**53):
                    return
                if not all(isinstance(k, str) and _integer(v, 0, 2**53) for k, v in acked.items()):
                    return
                if not all(isinstance(k, str) and isinstance(v, str) and v in ids for k, v in authorities.items()):
                    return
                self._last_roster_seq = seq
                self._last_host_seen = time.monotonic()
                self.peers = {p["player_id"]: p for p in players if p["player_id"] != self.player_id}
                self._room_authorities = authorities
                self._world_rooms=deepcopy(world_rooms);self._select_world_context()
                if epoch != self.health_epoch:
                    self._pending_health.clear()
                    self._health_seq = 0
                self._soul_link, self.health_epoch = enabled, epoch
                self.shared_health, self.health_revision = health, revision
                self._pending_health = [e for e in self._pending_health if e["id"] > acked.get(self.player_id, 0)]

    def _recv_loop(self, sock):
        while self._running and self._sock is sock:
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM + 1)
                if len(data) <= MAX_DATAGRAM:
                    data=self._datagrams.receive(data,addr,time.monotonic())
                    if data is not None:self._handle_packet(json.loads(data.decode("utf-8")), addr)
            except (UnicodeError, ValueError, RecursionError, TypeError):
                pass
            except OSError:
                if self._sock is not sock:
                    break
            self._tick()
