"""Reads and HP reconciliation of the closed EUR enemy catalogue in one game.

step_client detects this game's native losses and reconciles down to the
shared ledger's HP (enemy_hub); report() is this game's view for the ledger
and owners. step_master (applying others' losses natively) is the former
host-authority path, kept for the identity-race tests. This does not
replicate enemy AI animations; each write revalidates the scene, actor
handle, placement and exact vtable.
"""
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.rpc.azahar_bridge import AzaharBridge, TITLE_ID_EUR
from src.memory.gameplay_state import GameplayStateReader
from src.memory.render_state import _heap
from src.memory.world_actors import ActorSurvey, placement_key
from src.combat_profiles import sword_target

ENEMY_TYPES = {526, 527}
WORLD_SINGLETON = 0x706E20
GAME_SINGLETON = 0x709DF8


def format_placement_key(room: Tuple[int, int], actor: dict) -> Optional[str]:
    k = placement_key(actor, room)
    return None if k is None else ":".join(str(v) for v in k)


@dataclass(frozen=True)
class _RoomIdentity:
    pid: int
    owner: int
    game: int
    table: int
    # Where the actor table ends when read: a bound, not an identity. It moves
    # whenever an actor takes or frees the last slot (a lit torch's ground
    # glow restarted the world every second, 23/10).
    end: int = field(compare=False)
    room: Tuple[int, int] = ()


class EnemySynchronizer:
    def __init__(self, bridge: AzaharBridge, cache_scans=False):
        self.bridge = bridge
        self.survey = ActorSurvey(bridge)
        self.scene = GameplayStateReader(bridge)
        self.current_room: Optional[Tuple[int, int]] = None
        self.local_enemies: Dict[str, dict] = {}
        self.previous_hps: Dict[str, int] = {}
        self._previous_identities = {}
        self._context = None
        self._attack_counter = 0
        self.defeated_enemies: Dict[Tuple[int, int], set] = {}
        self.completed_attacks=[]
        self.mutation_guard=lambda:True
        self.cache_scans=cache_scans;self._candidates=None;self._survey_after=0
        self.health_writer=None
        self._dynamic_generation=None
        # Attack profile (combat_profiles index) of the latest loss per enemy.
        self.last_profile={}

    def is_enemy_actor(self, actor: dict) -> bool:
        return (type(actor.get("placement_id")) is int
                and (actor["placement_id"] > 0 or self._dynamic_binding(actor) is not None)
                and type(actor.get("type")) is int
                and (profile:=sword_target(actor["type"])) is not None
                and actor.get("vtable")==profile.vtable)

    def _dynamic_binding(self,actor,room=None):
        from src.dynamic_enemies import binding_for
        if actor.get('placement_id')!=-1:return None
        return binding_for(self.bridge,actor.get('handle'),room or self.current_room,
                           actor.get('type'),actor['placement_id'],actor.get('initial_position'))

    def _actor_key(self,room,actor):
        binding=self._dynamic_binding(actor,room)
        return binding['key'] if binding else format_placement_key(room,actor)

    def _clear(self):
        self.local_enemies.clear()
        self.previous_hps.clear()
        self._previous_identities.clear()
        self._context = None
        self._candidates=None;self._survey_after=0

    def _read_context(self, room):
        b = self.bridge
        if (not isinstance(room, tuple) or len(room) != 2
                or any(type(v) is not int for v in room)
                or not 0 <= room[0] <= 23 or not 0 <= room[1] <= 65535
                or b.attached_title_id != TITLE_ID_EUR
                or type(b.attached_pid) is not int or b.attached_pid <= 0
                or not b.rpc_connected or self.scene.playable() is not True or not self.mutation_guard()):
            return None
        pid = b.attached_pid
        owner, game = b.read_u32(WORLD_SINGLETON), b.read_u32(GAME_SINGLETON)
        if not _heap(owner) or not _heap(game):
            return None
        table, end = b.read_u32(owner + 0x3DBC0), b.read_u32(owner + 0x3DBC8)
        if not _heap(table) or not _heap(end) or not 0 <= end - table <= 4096 * 4:
            return None
        if (b.read_u8(game + 0x18), b.read_u32(game + 0x1C)) != room:
            return None
        if (b.read_u32(WORLD_SINGLETON) != owner or b.read_u32(GAME_SINGLETON) != game
                or b.read_u32(owner + 0x3DBC0) != table
                or b.read_u32(owner + 0x3DBC8) != end
                or (b.read_u8(game + 0x18), b.read_u32(game + 0x1C)) != room
                or self.scene.playable() is not True or not self.mutation_guard()
                or b.attached_pid != pid or b.attached_title_id != TITLE_ID_EUR):
            return None
        return _RoomIdentity(pid, owner, game, table, end, room)

    def _resolve_actor_address(self, handle: int, context=None) -> Optional[int]:
        context = context or self._read_context(self.current_room)
        if context is None or type(handle) is not int or not 0 < handle <= 0xFFFFFFFF:
            return None
        slot = handle >> 22
        if not context.table <= context.table + slot * 4 < context.end:
            return None
        addr = self.bridge.read_u32(context.table + slot * 4)
        if (not _heap(addr) or self.bridge.read_u32(addr + 0x2CC) != handle
                or self.bridge.read_u8(addr + 0x2D7) != 0):
            return None
        return addr

    def _read_actor(self, context, handle,validate_context=True):
        """Read identity and HP again; survey entries are never write leases."""
        b = self.bridge
        if type(handle) is not int or not 0<handle<=0xFFFFFFFF:return None
        slot=context.table+(handle>>22)*4
        if not context.table<=slot<context.end:return None
        addr=b.read_u32(slot)
        if not _heap(addr):
            return None
        header = b.read_memory(addr, 0x2D8)
        if not header or len(header) != 0x2D8 or header[0x2D7]:
            return None
        current_handle, profile = struct.unpack_from('<2I', header, 0x2CC)
        if current_handle != handle or not (_heap(profile)
                or type(profile) is int and 0x100000 <= profile < 0x7B0000 and profile % 4 == 0):
            return None
        actor = dict(handle=handle, address=addr, profile=profile,
                     type=b.read_u32(profile + 8),
                     vtable=struct.unpack_from('<I', header)[0],
                     placement_id=struct.unpack_from('<h', header, 0x58)[0],
                     initial_position=list(struct.unpack_from('<3f', header, 8)))
        self.current_room=context.room
        if (not self.is_enemy_actor(actor)
                or not all(math.isfinite(v) and abs(v) <= 1e6 for v in actor['initial_position'])):
            return None
        # From +3CC: +3CD is set while the game skips this actor's update
        # (farther than its calc range, ~20 units, from this game's Link).
        raw = b.read_memory(addr + 0x3CC, 0x144)
        if not raw or len(raw) != 0x144:
            return None
        hp, max_hp = struct.unpack_from('<2h',raw,0x13C)
        if not 1 <= max_hp <= 250 or not -64 <= hp <= max_hp:
            return None
        # Native overkill can leave -4 HP while the actor's death is processed.
        # Treat it as zero on the wire; never overwrite the game's signed value.
        dynamic=self._dynamic_binding(actor,context.room)
        actor.update(key=self._actor_key(context.room, actor), hp=max(0,hp),
                     max_hp=max_hp, _context=context,pos=list(struct.unpack_from('<3f',raw,4)),
                     yaw=struct.unpack_from('<I',raw,0x1C)[0],active=raw[1]==0)
        if dynamic is not None:actor['dynamic_key']=dynamic['key']
        if not all(math.isfinite(v) and abs(v)<=1e6 for v in actor['pos']):return None
        if (b.read_u32(addr + 0x2CC) != handle or b.read_u8(addr + 0x2D7) != 0
                or b.read_u32(slot)!=addr
                or validate_context and self._read_context(context.room) != context):
            return None
        return actor

    @staticmethod
    def _identity(info):
        return (info['_context'], info['address'], info['handle'],
                info['key'], tuple(info['initial_position']), info['profile'],
                info['type'], info['vtable'], info['max_hp'],info['placement_id'])

    def scan_room(self, room: Tuple[int, int]) -> Dict[str, dict]:
        room = tuple(room)
        self.current_room = room
        try:
            context = self._read_context(room)
            if context is None:
                self._clear()
                return {}
            if context != self._context:
                self._clear()
                self._context = context
            now=time.monotonic()
            generation=getattr(self.bridge,'shared_dynamic_generation',0)
            if generation!=self._dynamic_generation:
                self._dynamic_generation=generation;self._survey_after=0
            if not self.cache_scans or self._candidates is None or now>=self._survey_after:
                self._candidates=self.survey.read(room);self._survey_after=now+.5
            survey=self._candidates
            if survey.get('room') != list(room):
                self._clear()
                return {}
            candidates = {}
            duplicates = set()
            for actor in survey.get('actors', []):
                if not self.is_enemy_actor(actor):
                    continue
                key = self._actor_key(room, actor)
                if key in candidates:
                    duplicates.add(key)
                candidates[key] = actor
            scanned = {}
            for key, actor in candidates.items():
                if key in duplicates:
                    continue
                info = self._read_actor(context, actor.get('handle'),validate_context=False)
                if info is None or info['key'] != key or info['type'] != actor['type']:
                    self._survey_after=0
                    continue
                scanned[key] = info
            if self._read_context(room) != context:
                self._clear()
                return {}
        except (ValueError, OSError, TypeError, KeyError, struct.error):
            self._clear()
            return {}
        self.local_enemies = scanned
        return dict(scanned)

    def _apply_health(self, room, info, *, damage=None, health=None, only_reduce=False,command=None,profile=0):
        try:
            fresh = self._read_actor(info['_context'], info['handle'])
            if (fresh is None or info['_context'].room != tuple(room)
                    or self._identity(fresh) != self._identity(info)):
                return False
            target = max(0, fresh['hp'] - damage) if damage is not None else health
            if type(target) is not int or not 0 <= target <= fresh['max_hp']:
                return False
            if only_reduce:target=min(target,max(0,fresh['hp']))
            if self.health_writer is not None:
                extra={'profile':profile} if profile else {}
                result=self.health_writer(fresh,target,command,**extra)
                if type(result) is not int:return False
                info['hp']=result
                return True
            # No position or retirement-flag writes: those paths are unverified.
            if target != fresh['hp'] and not self.bridge.write_memory(
                    fresh['address'] + 0x508, struct.pack('<h', target)):
                return False
            info['hp'] = target
            return True
        except (ValueError, OSError, TypeError, KeyError, struct.error):
            return False

    def _remember(self, current):
        self.previous_hps = {key: info['hp'] for key, info in current.items()}
        self._previous_identities = {key: self._identity(info) for key, info in current.items()}

    @staticmethod
    def _receipt_key(room,receipt):
        if receipt.get('room')!=list(room):return None
        from src.dynamic_enemies import parse_key
        dynamic=receipt.get('dynamic_key')
        if parse_key(dynamic,room) is not None:return dynamic
        target=receipt.get('target')
        if not isinstance(target,list) or len(target)!=5:return None
        if not all(type(v) is int for v in target):return None
        return ':'.join(map(str,(*room,*target)))

    def _note_profiles(self,room,receipts):
        from src.combat_profiles import ATTACK_PROFILES
        names={p.name:i for i,p in enumerate(ATTACK_PROFILES)};names['standard_sword']=0
        for receipt in receipts:
            key=self._receipt_key(room,receipt);profile=names.get(receipt.get('profile'))
            if key and profile is not None:self.last_profile[key]=profile

    def _note_dynamic_deaths(self,room,current,receipts):
        from src.dynamic_enemies import parse_key
        confirmed=getattr(self.bridge,'shared_dynamic_death_receipts',set())
        for key,info in current.items():
            if info['hp']<=0 and parse_key(key,room) is not None:confirmed.add(key)
        for receipt in receipts:
            key=self._receipt_key(room,receipt)
            before,damage=receipt.get('hp_before'),receipt.get('native_damage')
            if (parse_key(key,room) is not None and type(before) is int and type(damage) is int
                    and 0<before<=damage):confirmed.add(key)
        self.bridge.shared_dynamic_death_receipts=confirmed

    def _shared_defeats(self,room):
        from src.dynamic_enemies import parse_key
        return {key for key in getattr(self.bridge,'shared_dynamic_defeated_keys',set()) if parse_key(key,room) is not None}

    def step_master(self, room: Tuple[int, int], incoming_attacks: List[dict], receipts=()) -> Dict[str, dict]:
        room = tuple(room)
        current = self.scan_room(room)
        self.completed_attacks=[];blocked=set()
        if self._context is None:return {}
        self._note_dynamic_deaths(room,current,receipts)
        self._note_profiles(room,receipts)
        defeated = self.defeated_enemies.setdefault(room, set())
        shared=self._shared_defeats(room)
        for key in shared:
            if key in current and current[key]['hp']>0:
                self._apply_health(room,current[key],health=0,only_reduce=True,command=('shared_death',key,0))
        defeated.update(shared)
        for receipt in receipts:
            key=self._receipt_key(room,receipt)
            if (key and type(receipt.get('hp_before')) is int and type(receipt.get('native_damage')) is int
                    and 0<receipt['hp_before']<=receipt['native_damage'] and key not in current):
                defeated.add(key)
        for attack in incoming_attacks:
            if not isinstance(attack, dict):
                continue
            key, damage = attack.get('key'), attack.get('damage')
            source=attack.get('source_player')
            if source in blocked:continue
            if key in defeated:
                self.completed_attacks.append(attack);continue
            profile=attack.get('profile',0)
            if (isinstance(key, str) and key in current and type(damage) is int
                    and 0 < damage <= 250):
                if self._apply_health(room, current[key], damage=damage,command=('host',source,attack.get('id')),
                                      profile=profile):
                    if profile:self.last_profile[key]=profile
                    self.completed_attacks.append(attack);continue
            blocked.add(source)
        for key, info in current.items():
            if info['hp'] <= 0:
                defeated.add(key)
        result = {key: dict(hp=info['hp'], max_hp=info['max_hp'],
                            pos=info['pos'], yaw=info.get('yaw', 0), dead=info['hp'] <= 0)
                  for key, info in current.items()}
        # Which attack profile the others replay a loss with (sword when absent).
        for key,state in result.items():
            if self.last_profile.get(key):state['profile']=self.last_profile[key]
        for key in defeated:
            if key in shared or key not in result:
                prior=result.get(key,{})
                result[key] = dict(hp=0, max_hp=prior.get('max_hp',0),pos=prior.get('pos',[0.0,0.0,0.0]),yaw=prior.get('yaw',0),dead=True)
        self._remember(current)
        return result

    def step_client(self, room: Tuple[int, int], authoritative_states: Dict[str, dict], receipts=(),
                    *,pending_keys=(),allow_healing=False,hold_keys=(),loss_credits=None) -> List[dict]:
        room = tuple(room)
        old=dict(self.local_enemies)
        current = self.scan_room(room)
        self._note_dynamic_deaths(room,current,receipts)
        self._note_profiles(room,receipts)
        authoritative_states=dict(authoritative_states or {})
        for key in self._shared_defeats(room):
            authoritative_states[key]=dict(hp=0,max_hp=0,dead=True,pos=[0.,0.,0.],yaw=0)
        held=set(hold_keys)
        losses={}
        for key, info in current.items():
            if key in held:continue
            previous = self.previous_hps.get(key)
            first=False;binding=self._dynamic_binding(info,room)
            if previous is None and binding is not None:
                previous=binding.get('last_hp') if binding.get('baseline_seen') else binding['birth_hp']
                binding['baseline_seen']=True;first=previous is not None
            if ((first or self._previous_identities.get(key) == self._identity(info))
                    and previous is not None and 0 <= info['hp'] < previous):
                losses[key]=previous-info['hp']
        # A lethal native hit can retire its actor before the next RPC scan.
        # Merge receipts into the observed HP interval, never add both blindly.
        for receipt in receipts:
            key=self._receipt_key(room,receipt);info=current.get(key) or old.get(key)
            if key in held:continue
            before,damage=receipt.get('hp_before'),receipt.get('native_damage')
            if info is None and receipt.get('dynamic_key'):
                entries=getattr(self.bridge,'shared_dynamic_enemies',{}).values()
                binding=next((v for v in entries if v['key']==key),None)
                baseline=(binding.get('last_hp') if binding.get('baseline_seen') else binding['birth_hp']) if binding else None
                if (type(baseline) is int and type(before) is int and type(damage) is int
                        and 0<before<=damage and before<=baseline):
                    binding['baseline_seen']=True;binding['last_hp']=0;losses[key]=baseline
                continue
            if (info is None or info['_context']!=self._context
                    or self._previous_identities.get(key)!=self._identity(info)
                    or type(before) is not int or type(damage) is not int or damage<=0):continue
            seen=current[key]['hp'] if key in current else self.previous_hps[key]
            after=max(0,before-damage)
            if after<seen:
                losses[key]=losses.get(key,0)+seen-after
                if key in current:current[key]['hp']=after
        attacks=[]
        for key,credit in (loss_credits or {}).items():
            if key in losses:losses[key]=max(0,losses[key]-credit)
        for key,damage in losses.items():
            if damage<=0:continue
            self._attack_counter+=1
            attack=dict(id=self._attack_counter,key=key,damage=damage)
            if self.last_profile.get(key):attack['profile']=self.last_profile[key]
            attacks.append(attack)
        protected=set(pending_keys)|losses.keys()
        if isinstance(authoritative_states, dict):
            for key, auth in authoritative_states.items():
                if key not in current or not isinstance(auth, dict):
                    continue
                if key in held:continue
                info = current[key]
                # The native replay runs in the enemy's own update, which this
                # game skips while it is out of range: reconcile once active.
                if not info.get('active',True):continue
                hp, maximum, dead = auth.get('hp'), auth.get('max_hp'), auth.get('dead')
                if (type(hp) is int and 0 <= hp <= info['max_hp']
                        and (maximum is None or maximum == info['max_hp'])
                        and (type(dead) is bool and dead == (hp == 0))):
                    # An older host frame must not undo a local hit awaiting ACK.
                    may_heal=allow_healing and key not in protected and info['hp']>0
                    target=hp if may_heal else min(hp,info['hp'])
                    if target!=info['hp']:
                        self._apply_health(room,info,health=target,only_reduce=not may_heal,
                                           command=('client',key,target),profile=auth.get('profile',0))
                elif dead is True and hp == 0:
                    self._apply_health(room, info, health=0,command=('client',key,0),profile=auth.get('profile',0))
        # Preserve the pre-command interval until its native result is known.
        # Concurrent genuine local hits are then counted after replay credit.
        previous=dict(self.previous_hps);identities=dict(self._previous_identities)
        self._remember(current)
        for key in held:
            if key in previous:
                self.previous_hps[key]=previous[key]
                self._previous_identities[key]=identities[key]
            if key not in self.local_enemies and key in old:
                self.local_enemies[key]=old[key]
        for binding in getattr(self.bridge,'shared_dynamic_enemies',{}).values():
            if binding['key'] in self.previous_hps:binding['last_hp']=self.previous_hps[binding['key']]
        return attacks

    def report(self):
        """This game's view of the room for the shared ledger and owners."""
        return {key:dict(hp=info['hp'],max_hp=info['max_hp'],pos=list(info['pos']),
                         yaw=info.get('yaw',0),active=info.get('active',True))
                for key,info in self.local_enemies.items()}
