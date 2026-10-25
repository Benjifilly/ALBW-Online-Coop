"""Read the local player's real EUR projectiles; never create gameplay actors.

One table row per family: bombs, arrows (stuck ones included) and the flying
hookshot head. The records travel under the render state's 'bombs' key.
Native actor handles, G3D resources and particle-bank pointers are local-only.
Published IDs are a separate counter. A seen native Explose phase is repeated
briefly after the actor disappears, so normal network jitter cannot lose its
visual event. An actor born and retired entirely between polls is not inferred
from a missing pointer: only the native phase is proof of an explosion.
"""
from copy import deepcopy
from dataclasses import dataclass
import struct
import time

from src.memory.render_state import _heap, _integer, _number
from src.memory.world_actors import ActorSurvey
from src.rpc.azahar_bridge import TITLE_ID_EUR

BOMB_KIND = 110
ARROW_KIND = 35
HOOK_KIND = 39
BOMB_VTABLE = 0x6ED19C
MAX_BOMBS = 8
EXPLOSION_RETAIN = 1.5
PROFILE_TABLE = 0x727C28
# An arrow or hook is this Link's when it starts at most this far from him;
# archers' arrows start at the archer.
SHOT_REACH = 2.0
# The flying hook draws its chain as 9 G3Ds named Chain, array at actor+52C.
CHAIN_LINKS = 9
CHAIN_ARRAY = 0x52C


@dataclass(frozen=True)
class Projectile:
    vtable: int
    model: int          # actor offset of its G3D root
    root_vtable: int
    names: dict         # BCH model name -> wire variant


# EUR projectile probe (22/10). The variant comes from the loaded model name:
# a normal bomb has +6E8 = 2 (its sound level) yet draws BombS, and the
# profile's group 2 is BombEnemy, which no row accepts.
PROJECTILES = {
    BOMB_KIND: Projectile(BOMB_VTABLE, 0x4F4, 0x6DBB44, {'BombS': 0, 'BombR': 1, 'BombM': 3}),
    ARROW_KIND: Projectile(0x6ED014, 0x454, 0x6DBB44, {'Arrow': 0}),
    HOOK_KIND: Projectile(0x6CD7E4, 0x528, 0x6EBEF0, {'Hook': 0}),
}
BOMB_FINGERPRINTS = {
    0x189944: bytes.fromhex('f0472de90040a0e108d04de2c0029fe5'),
    0x188DF0: bytes.fromhex('10402de90040a0e108d04de20100a0e3d505c4e5'),
    0x188BB8: bytes.fromhex('10402de90040a0e13d0e80e2f72f84e20010a0e1'),
    0x19B04C: bytes.fromhex('0c109fe50a0d50e300118130000091e5'),
    0x3526B4: bytes.fromhex('a14405eb200084e5304080e5'),
}


def _valid_blink(blink):
    """The fuse's red material animation: [index, frame, previous, rate, length]."""
    return (isinstance(blink, list) and len(blink) == 5 and _integer(blink[0], 0, 15)
            and all(_number(v, -1, 10000) for v in (blink[1], blink[2], blink[4]))
            and _number(blink[3], -32, 32))


def read_blink(bridge, animation):
    """Phase of the bomb's own animation object (+4F8, built by 18A3E4).

    Same controller as Link's: one active entry at +14, whose rate (+10) the
    fuse routine 189944 raises to 1.0 for the last 60 frames.
    """
    header = bridge.read_memory(animation, 0x18) if _heap(animation) else None
    if not header or len(header) != 0x18:
        return None
    count, active = struct.unpack_from('<I', header, 0xC)[0], struct.unpack_from('<I', header, 0x14)[0]
    entry = bridge.read_memory(active, 24) if count == 1 and _heap(active) else None
    if not entry or len(entry) != 24:
        return None
    index, _, frame, previous, rate, length = struct.unpack('<2I4f', entry)
    blink = [index, frame, previous, rate, length]
    return blink if _valid_blink(blink) and max(frame, previous) <= max(1, length) + 1 else None


def validate_bombs(value):
    """Closed cosmetic records. Unknown fields (especially pointers) are removed."""
    if not isinstance(value, list) or len(value) > MAX_BOMBS:
        return None
    result = []; ids = set()
    for item in value:
        if not isinstance(item, dict):
            return None
        serial = item.get('id'); variant = item.get('variant'); kind = item.get('kind')
        if (not _integer(serial, 1, 0xFFFFFFFF) or serial in ids
                or type(kind) is not int or kind not in PROJECTILES
                or type(variant) is not int or variant not in PROJECTILES[kind].names.values()
                or item.get('phase') not in ('fuse', 'explosion')
                or not _integer(item.get('frame'), 0, 0xFFFF)
                or not _number(item.get('fuse'), 0, 1)
                or type(item.get('visible')) is not bool):
            return None
        position, matrix, scale = item.get('pos'), item.get('matrix'), item.get('scale')
        if (not isinstance(position, list) or len(position) != 3
                or any(not _number(v, -1e6, 1e6) for v in position)
                or not _valid_transform(matrix, scale)):
            return None
        # An explosion can never revive the old 3D bomb mesh.
        if item['phase'] == 'explosion' and item['visible']:
            return None
        blink = item.get('blink')
        if blink is not None and (kind != BOMB_KIND or not _valid_blink(blink)):
            return None
        lean = item.get('lean')
        if lean is not None and not _number(lean, 0, 2):
            return None
        chain = item.get('chain')
        if chain is not None and (kind != HOOK_KIND or not isinstance(chain, list) or len(chain) > CHAIN_LINKS
                                  or not all(isinstance(link, list) and len(link) == 15
                                             and _valid_transform(link[:12], link[12:]) for link in chain)):
            return None
        ids.add(serial)
        clean = dict(id=serial, kind=kind, variant=variant,
            pos=list(position), matrix=list(matrix), scale=list(scale),
            fuse=float(item['fuse']), phase=item['phase'], frame=item['frame'],
            visible=item['visible'])
        if blink is not None:
            clean['blink'] = list(blink)
        if chain is not None:
            clean['chain'] = [list(link) for link in chain]
        if lean is not None:
            clean['lean'] = float(lean)
        result.append(clean)
    return result


def _valid_transform(matrix, scale):
    return (isinstance(matrix, list) and len(matrix) == 12
            and all(_number(v, -1e6 if i % 4 == 3 else -64, 1e6 if i % 4 == 3 else 64)
                    for i, v in enumerate(matrix))
            and isinstance(scale, list) and len(scale) == 3 and all(_number(v, 0, 16) for v in scale))


def read_chain(bridge, links):
    """Visible links of a flying hook: 12 matrix + 3 scale values each."""
    result = []
    for link in links:
        if bridge.read_u8(link + 0x30) != 1:
            continue
        transform = bridge.read_memory(link + 0x74, 60)
        if not transform or len(transform) != 60:
            return None
        scale, matrix = struct.unpack_from('<3f', transform), struct.unpack_from('<12f', transform, 12)
        result.append(list(matrix) + list(scale))
    return result


def _model_name(bridge, resource):
    raw = bridge.read_u32(resource + 8) if _heap(resource) else None
    name = bridge.read_u32(raw + 0x84) if _heap(raw) else None
    # BCH strings need not be word-aligned.
    text = bridge.read_memory(name, 16) if type(name) is int and 0x08000000 <= name < 0x1F000000 - 16 else None
    return text.split(b'\0', 1)[0].decode('ascii', 'replace') if text else None


def _profile_resource(b, kind, variant):
    """Bombs: the profile's model group of this variant, live bomb or not."""
    profile = b.read_u32(PROFILE_TABLE + kind * 4)
    if not (type(profile) is int and 0x100000 <= profile < 0x7B0000 and profile % 4 == 0):
        return None
    if b.read_u32(profile + 8) != kind:
        return None
    group = b.read_u32(profile + 0x1C8 + variant * 4)
    if not _heap(group):
        return None
    count, table = b.read_u32(group + 0xC8), b.read_u32(group + 0xD0)
    if count != 1 or not _heap(table):
        return None
    resource = b.read_u32(table)
    if (b.read_u32(PROFILE_TABLE + kind * 4) != profile
            or b.read_u32(profile + 0x1C8 + variant * 4) != group):
        return None
    return resource, group


def _group_animations(b, group, resource):
    """Animation wrappers of a profile group (+D4 count, +DC table), each one
    (raw animation, bound model resource) bound to this very resource."""
    count, table = b.read_u32(group + 0xD4), b.read_u32(group + 0xDC)
    if type(count) is not int or not 1 <= count <= 4 or not _heap(table):
        return []
    wrappers = []
    for index in range(count):
        wrapper = b.read_u32(table + index * 4)
        pair = b.read_memory(wrapper, 8) if _heap(wrapper) else None
        if not pair or len(pair) != 8:
            return []
        raw, bound = struct.unpack('<2I', pair)
        if bound != resource or not _heap(raw):
            return []
        wrappers.append(wrapper)
    return wrappers


def _held_resource(b, kind, model='Hook'):
    """Arrows: the nocked arrow's BCH. Hooks: the 'Hook' (or 'Chain') model
    listed next to 'Shooter' in the hookshot archive's model table."""
    from src.memory.item_visuals import PROFILE_BY_KIND, _source
    singleton = b.read_u32(0x70FB60)
    player = b.read_u32(singleton + 0x10) if _heap(singleton) else None
    profile = PROFILE_BY_KIND['nocked_arrow' if kind == ARROW_KIND else 'hookshot']
    source = _source(b, player, profile)
    if source is None:
        return None
    held = b.read_u32(source[1] + 0x50)
    if not _heap(held):
        return None
    if kind == ARROW_KIND:
        return held
    window = b.read_memory(held - 0x40, 0x40)
    if not window or len(window) != 0x40:
        return None
    words = struct.unpack('<16I', window)
    if held not in words:
        return None
    for resource in words[words.index(held) + 1:words.index(held) + 4]:
        if _heap(resource) and _model_name(b, resource) == model:
            return resource
    return None


def _describe(b, resource, name):
    raw = b.read_u32(resource + 8)
    sizes = b.read_memory(resource + 0x4C, 16)
    if (_model_name(b, resource) != name
            or not sizes or len(sizes) != 16 or any(n > 0x10000 for n in struct.unpack('<4I', sizes))
            or b.read_u8(raw) not in (0, 2)):
        return None
    return dict(resource=resource, name=name, sizes=struct.unpack('<4I', sizes))


def chain_model_source(bridge):
    """This game's own 'Chain' model for a flying hook's links."""
    resource = _held_resource(bridge, HOOK_KIND, 'Chain')
    return _describe(bridge, resource, 'Chain') if _heap(resource) else None


def bomb_model_source(bridge, kind=BOMB_KIND, variant=0):
    """Resolve this game's G3D resource for a projectile, even with none live.

    The return value is local rendering input, never a network record. Mesh
    counts belong to the newly constructed private model (+5C), not to these
    pointers. Refuse unknown resource names and malformed descriptors.
    """
    row = PROJECTILES.get(kind) if type(kind) is int else None
    name = next((n for n, v in row.names.items() if v == variant), None) if row and type(variant) is int else None
    if name is None:
        return None
    b = bridge; animations = []
    if kind == BOMB_KIND:
        found = _profile_resource(b, kind, variant)
        resource = found[0] if found else None
        if found:
            # The fuse's red blink: an optional extra, never a reason to hide the bomb.
            animations = _group_animations(b, found[1], resource) if _heap(resource) else []
    else:
        resource = _held_resource(b, kind)
    source = _describe(b, resource, name) if _heap(resource) else None
    if source and animations:
        source['animations'] = animations
    return source


class BombStateReader:
    """One source game, bounded actor survey, no write or debugger methods."""
    def __init__(self, bridge, *, clock=time.monotonic):
        self.bridge = bridge; self.clock = clock; self.identity = None
        self.supported = False; self.room = None; self.next_survey = 0
        self.candidates = []; self.ids = {}; self.next_id = 0
        self.explosions = {}; self.effect_banks = []; self.error = ''
        self.player = None; self.shooters = {}; self.chains = {}
        self.survey = ActorSurvey(bridge)

    def _executable(self):
        b = self.bridge
        return b.attached_title_id == TITLE_ID_EUR and all(
            b.read_memory(at, len(data)) == data for at, data in BOMB_FINGERPRINTS.items())

    def _serial(self, key):
        if key not in self.ids:
            self.next_id = (self.next_id + 1) & 0xFFFFFFFF or 1
            self.ids[key] = self.next_id
        return self.ids[key]

    def _context(self, player, room):
        b = self.bridge; owner = b.read_u32(0x706E20); game = b.read_u32(0x709DF8)
        singleton = b.read_u32(0x70FB60)
        if (not _heap(owner) or not _heap(game) or not _heap(singleton)
                or b.read_u32(singleton + 0x10) != player
                or (b.read_u8(game + 0x18), b.read_u32(game + 0x1C)) != room):
            return None
        table, end = b.read_u32(owner + 0x3DBC0), b.read_u32(owner + 0x3DBC8)
        if not _heap(table) or not _heap(end) or not 0 <= end - table <= 4096 * 4:
            return None
        return owner, game, table, end

    def _mine(self, kind, actor, raw):
        # 6B4=0 is Link's bomb. Shadow Link=1 and enemy thrown bombs=2 stay local.
        if kind == BOMB_KIND:
            return raw[0x6B4] == 0
        # Arrows and hooks: decided once per actor, from where they started.
        key = actor, struct.unpack_from('<I', raw, 0x2CC)[0]
        if key not in self.shooters:
            here = self.bridge.read_memory(self.player + 0x3D0, 12)
            if not here or len(here) != 12:
                return False
            (x, _, z), (px, _, pz) = struct.unpack_from('<3f', raw, 8), struct.unpack('<3f', here)
            if len(self.shooters) >= 64:
                self.shooters.clear()
            self.shooters[key] = (x - px) ** 2 + (z - pz) ** 2 <= SHOT_REACH ** 2
        return self.shooters[key]

    def _chain_links(self, actor, raw):
        """The hook's chain G3Ds, names verified once per actor and array."""
        b = self.bridge
        key = actor, struct.unpack_from('<I', raw, 0x2CC)[0], struct.unpack_from('<I', raw, CHAIN_ARRAY)[0]
        if key not in self.chains:
            words = b.read_memory(key[2], CHAIN_LINKS * 4) if _heap(key[2]) else None
            links = struct.unpack(f'<{CHAIN_LINKS}I', words) if words and len(words) == CHAIN_LINKS * 4 else ()
            from src.memory.equipment_model import resource_name
            if not all(_heap(link) and b.read_u32(link) in (0x6DBB44, 0x6EBEF0)
                       and resource_name(b, link) == 'Chain' for link in links):
                links = ()
            if len(self.chains) >= 16:
                self.chains.clear()
            self.chains[key] = links
        return self.chains[key]

    def _actor(self, handle, kind, context):
        b = self.bridge; table, end = context[2:]; slot = handle >> 22; row = PROJECTILES[kind]
        if slot >= (end - table) // 4:
            return None
        actor = b.read_u32(table + slot * 4)
        if not _heap(actor):
            return None
        raw = b.read_memory(actor, 0x704)
        if not raw or len(raw) != 0x704:
            return None
        word = lambda off: struct.unpack_from('<I', raw, off)[0]
        profile = word(0x2D0)
        if (word(0) != row.vtable or word(0x2CC) != handle or raw[0x2D7]
                or not (type(profile) is int and 0x100000 <= profile < 0x7B0000)
                or b.read_u32(profile + 8) != kind or not self._mine(kind, actor, raw)):
            return None
        root = word(row.model)
        if not _heap(root) or b.read_u32(root) != row.root_vtable:
            return None
        from src.memory.equipment_model import resource_name
        variant = row.names.get(resource_name(b, root))
        transform = b.read_memory(root + 0x74, 60)
        mode = b.read_u8(root + 0x30)
        if variant is None or not transform or len(transform) != 60 or mode not in (0, 1):
            return None
        if kind == BOMB_KIND:
            phase, frame = struct.unpack_from('<b', raw, 0x500)[0], word(0x4FC)
            elapsed, limit = word(0x698), word(0x69C)
            # States 7,8,10,11 are non-explosion erasure/fall states; never synthesize a boom.
            if phase not in range(0, 7) or frame > 0xFFFF or not 1 <= limit <= 3600 or elapsed > limit:
                return None
            state = dict(fuse=max(0., 1. - elapsed / limit), phase='explosion' if phase == 6 else 'fuse',
                         frame=frame, visible=bool(mode) and phase != 6)
            blink = read_blink(b, word(0x4F8))
            if blink is not None:
                state['blink'] = blink
        else:
            state = dict(fuse=1., phase='fuse', frame=0, visible=bool(mode))
            links = self._chain_links(actor, raw) if kind == HOOK_KIND else ()
            chain = read_chain(b, links) if links else None
            if chain:
                state['chain'] = chain
        item = dict(kind=kind, variant=variant, pos=list(struct.unpack_from('<3f', raw, 0x3D0)),
            scale=list(struct.unpack_from('<3f', transform)), matrix=list(struct.unpack_from('<12f', transform, 12)),
            **state)
        # The renderer's camera lean (G3D+134): 1.0 tilts a bomb toward the view.
        lean = b.read_memory(root + 0x134, 4)
        lean = struct.unpack('<f', lean)[0] if lean and len(lean) == 4 else None
        if _number(lean, 0, 2):
            item['lean'] = lean
        # Recheck the full identity after variable reads. A recycled slot must not
        # inherit the previous projectile's serial, model or particle bank.
        if (b.read_u32(table + slot * 4) != actor or b.read_u32(actor) != row.vtable
                or b.read_u32(actor + 0x2CC) != handle or b.read_u8(actor + 0x2D7) != 0
                or b.read_u32(actor + 0x2D0) != profile or b.read_u32(actor + row.model) != root):
            return None
        bank = word(0x344)
        if not (_heap(bank) and b.read_u32(bank + 0x30) == actor + 0x324
                and b.read_u32(actor + 0x344) == bank):
            bank = None
        return actor, item, bank

    def read(self, player, room):
        b = self.bridge; room = tuple(room); now = self.clock()
        identity = b.attached_pid, b.attached_title_id, player
        if identity != self.identity:
            self.identity = identity; self.next_survey = 0; self.candidates = []
            self.ids.clear(); self.explosions.clear(); self.next_id = 0; self.error = ''
            self.supported = False
        # Once per game process; a failed read is checked again next time.
        if not self.supported:
            self.supported = self._executable()
        self.effect_banks = []
        if room != self.room:
            self.room = room; self.next_survey = 0; self.candidates = []
            self.explosions.clear(); self.shooters.clear(); self.chains.clear()
        if not self.supported or not _heap(player):
            return []
        self.player = player
        context = self._context(player, room)
        if context is None:
            return []
        if now >= self.next_survey:
            try:
                survey = self.survey.read(room)
                self.candidates = [(a['handle'], a['type']) for a in survey['actors']
                    if a['type'] in PROJECTILES and a['vtable'] == PROJECTILES[a['type']].vtable][:32]
            except ValueError:
                self.candidates = []
            self.next_survey = now + .05
        result = []; live = set()
        for handle, kind in self.candidates:
            found = self._actor(handle, kind, context)
            if found is None:
                continue
            actor, item, bank = found; key = actor, handle
            item['id'] = self._serial(key)
            if validate_bombs([item]) is None:
                continue
            live.add(key)
            if bank is not None:
                self.effect_banks.append(bank)
            if item['phase'] == 'explosion':
                self.explosions.setdefault(item['id'], (now + EXPLOSION_RETAIN, deepcopy(item)))
            result.append(item)
        # A single incomplete survey or an RPC read failure is not a new birth.
        # Native handle generations distinguish actors; keep recent identities
        # across these gaps, with a strict bound for a very long session.
        if len(self.ids) > 1024:
            for key in list(self.ids):
                if len(self.ids) <= 1024:
                    break
                if key not in live and self.ids[key] not in self.explosions:
                    del self.ids[key]
        active = {item['id'] for item in result}
        self.explosions = {serial: value for serial, value in self.explosions.items() if value[0] > now}
        result += [deepcopy(item) for serial, (_, item) in self.explosions.items() if serial not in active]
        if self._context(player, room) != context:
            self.effect_banks = []
            return []
        # Explosions first, then the newest: a wall of stuck arrows never hides a new shot.
        result.sort(key=lambda item: (item['phase'] != 'explosion', -item['id']))
        return validate_bombs(result[:MAX_BOMBS]) or []
