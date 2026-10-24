"""Build the closed EUR sword-impact image without accessing a process.

No game bridge, RPC, process access, allocator, hook publication or automatic
retry exists here. The caller must provide a verified local context and manage
publication/retirement separately. This deliberately attributes the synthetic
impact to the registered LOCAL Player; it is not remote combat attribution.
"""
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path
import struct
import math
from src.combat_profiles import SWORD_TARGETS,ATTACK_PROFILES,sword_target
from src.dynamic_enemies import KINDS

HOOK = 0x4F7330
CONTINUE = HOOK + 4
ORIGINAL = bytes.fromhex('000056e3')
CONTROL_OFFSET = 0x1000
CONTROL_SIZE = 0x120
MAGIC = 0x31504D49
SYNTHETIC = 0x544E5953
SWORD_DESC = bytes.fromhex('090500000000000000000000000000000800ffff0000803f')
# One real HitInfo, excluding its process-specific author and descriptor pointer.
SYNTHETIC_HIT = bytes.fromhex(
    '800f6f007a1132bf000000004dec373f46835e416666263f5c85d3416d4f133f'
    '0000a84108000000000000000000000000000004000000000000803fbb306541'
    '0a9c1d3e8712d041')
FINGERPRINTS = {
    HOOK: bytes.fromhex('000056e33600000a3c1494e5000091e5000050e3'),
    0x4F822C: bytes.fromhex('70402de90040a0e12c0091e5016b84e20150a0e1'),
    0x34C8CC: bytes.fromhex('30002de92c3092e5000053e34400000a000051e3'),
    0x4F73F8: bytes.fromhex('b810d5e1000041e07000bfe6400070e3'),
    0x4F7E64: bytes.fromhex('f0472de90050a0e10140a0e1012b80e23c1495e5'),
    0x52FB78: bytes.fromhex('000051e30a00000a042090e5210ba0e1'),
    0x52ED2C: bytes.fromhex('d8009fe510402de90140a0e1000090e5'),
}


class TrialState(IntEnum):
    ARMED = 1
    CLAIMED = 2
    RETURNED = 3
    REJECTED = 4


class Refusal(IntEnum):
    NONE = 0
    CONTEXT = 1
    TRANSITION = 2
    TARGET = 3
    AUTHOR = 4
    LOCAL_RECORD_BUSY = 5
    PROFILE = 6
    NATIVE_GATE_OR_ABI = 7
    EXPIRED = 8
    HP_CHANGED = 9


def _heap(value):
    return type(value) is int and value % 4 == 0 and 0x08000000 <= value < 0x1F000000


def _profile(value):
    return type(value) is int and value % 4 == 0 and 0x100000 <= value < 0x7AFF00


def _sensitivity(value):
    # EUR soldier tables live in the mapped static data, not necessarily heap.
    return _heap(value) or (type(value) is int and value % 4 == 0
                           and 0x5E0000 <= value < 0x7AFB00)


@dataclass(frozen=True)
class TrialContext:
    owner: int
    game: int
    table: int
    end: int
    course: int
    stage: int
    scene: int
    player: int
    player_handle: int
    player_body: int
    target: int
    target_handle: int
    kind: int
    placement: int
    target_profile: int
    sensitivity_table: int
    attack_record: int
    expected_hp: int
    max_hp: int
    player_profile: int
    player_action: int
    # Sensitivity halfword of the profile's reaction type (sword: table+0x18).
    attack_sensitivity: int
    dynamic_binding: bool = False
    # Index in combat_profiles.ATTACK_PROFILES; 0 is the standard sword.
    profile: int = 0

    def validate(self):
        for field in ('owner','game','table','end','scene','player','player_body',
                      'target','attack_record','player_action'):
            if not _heap(getattr(self,field)):
                raise ValueError('Invalid local pointer: '+field)
        if not _sensitivity(self.sensitivity_table):
            raise ValueError('Invalid local sensitivity table')
        if not all(_profile(v) for v in (self.target_profile,self.player_profile)):
            raise ValueError('Invalid verified native profile pointer')
        profile=sword_target(self.kind)
        if self.target == self.player or profile is None:
            raise ValueError('Only a distinct verified combat target is supported')
        if not 0 < self.end-self.table <= 0x1000:
            raise ValueError('Invalid actor table extent')
        for handle in (self.player_handle,self.target_handle):
            if type(handle) is not int or not 0 < handle <= 0xFFFFFFFF:
                raise ValueError('Invalid actor handle')
            if self.table+(handle >> 22)*4 >= self.end:
                raise ValueError('Actor handle index outside verified table')
        expected_max = profile.max_hp
        if self.max_hp != expected_max or not 0 < self.expected_hp <= self.max_hp:
            raise ValueError('Unsupported soldier health epoch')
        dynamic=(self.placement==-1 and self.dynamic_binding is True and self.kind in KINDS)
        if not (type(self.placement) is int and (0 <= self.placement < 0x8000 or dynamic)
                and type(self.course) is int and 0 <= self.course <= 18
                and type(self.stage) is int and 0 <= self.stage <= 0xFFFF
                and type(self.attack_sensitivity) is int and 0 <= self.attack_sensitivity <= 0xFFFF):
            raise ValueError('Invalid room, placement or native sensitivity')
        if type(self.profile) is not int or not 0 <= self.profile < len(ATTACK_PROFILES):
            raise ValueError('Unknown attack profile')


def payload_source():
    """Expand local closed guards; this never consumes network-supplied code."""
    source = Path(__file__).with_suffix('.arm').read_text(encoding='utf-8')
    source=source.replace('__TARGET_PROFILE_COUNT__',str(len(SWORD_TARGETS)))
    table='\n'.join(f'.word {kind},0x{p.vtable:08x},{p.max_hp}' for kind,p in SWORD_TARGETS.items())
    source=source.replace('__TARGET_PROFILE_TABLE__',table)
    source=source.replace('__ATTACK_PROFILE_COUNT__',str(len(ATTACK_PROFILES)))
    rows=['.word '+','.join(f'0x{w:08x}' for w in (*struct.unpack('<6I',p.desc),p.flags,0))
          for p in ATTACK_PROFILES]
    return source.replace('__ATTACK_PROFILE_TABLE__','\n'.join(rows))


def compile_payload():
    """Development only (keystone): the versioned enemy_impact_trial.bin comes from here."""
    from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM
    machine, _ = Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(payload_source(),addr=0)
    return bytes(machine)


@lru_cache(maxsize=1)
def assembled_payload():
    """Assembled at zero; all internal references remain position independent.
    Players have no assembler (24/10: every frame stopped on the missing keystone)."""
    result = Path(__file__).with_suffix('.bin').read_bytes()
    if len(result) > CONTROL_OFFSET:
        raise ValueError('Trial payload overlaps its private control')
    if result.count(SWORD_DESC) != 1:
        raise ValueError('Compiled closed sword profile differs from capture')
    return result


def verify_code(code, base):
    """Verify supplied local bytes, without reading a process or file."""
    return all(code[at-base:at-base+len(expected)] == expected
               for at,expected in FINGERPRINTS.items())


def build_image(context, *, nonce, synthetic_geometry_acknowledged=False, lease=120,vectors=None,target_hp=None):
    """Return private image bytes; this function never publishes or executes them.

    The acknowledgement is a required API flag, not a user-confirmation flow.
    Default geometry is the historical room (0,17) synthetic prototype.
    Explicit vectors reconstruct only fields consumed by the closed reaction5
    path; neither mode establishes equivalent spatial collision between worlds.
    """
    if synthetic_geometry_acknowledged is not True:
        raise ValueError('Explicitly acknowledge unvalidated synthetic geometry')
    context.validate()
    if type(nonce) is not int or not 0 < nonce <= 0xFFFFFFFF:
        raise ValueError('A nonzero single-use command nonce is required')
    if type(lease) is not int or not 1 <= lease <= 120:
        raise ValueError('The target-visit lease must be in 1..120')
    data = bytearray(CONTROL_OFFSET+CONTROL_SIZE)
    payload = assembled_payload()
    data[:len(payload)] = payload
    initial = bytearray(CONTROL_SIZE)
    struct.pack_into('<6I',initial,0,MAGIC,1,TrialState.ARMED,0,nonce,1)
    struct.pack_into('<19I',initial,0x18,
        context.owner,context.game,context.table,context.end,context.course,context.stage,
        context.scene,context.player,context.player_handle,context.player_body,
        context.target,context.target_handle,context.kind,context.placement&0xFFFFFFFF,
        context.target_profile,context.sensitivity_table,context.attack_record,
        context.expected_hp,context.max_hp)
    struct.pack_into('<I',initial,0x64,lease)
    struct.pack_into('<6I',initial,0xE0,SYNTHETIC,0,1,context.player_profile,
                     context.player_action,context.attack_sensitivity)
    profile=ATTACK_PROFILES[context.profile]
    hit=bytearray(SYNTHETIC_HIT)
    if vectors is None and context.profile:
        raise ValueError('Only the sword has a captured default geometry')
    if vectors is not None:
        if (not isinstance(vectors,(list,tuple)) or len(vectors)!=3
                or any(not isinstance(v,(list,tuple)) or len(v)!=3 for v in vectors)
                or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1e6
                       for v in vectors for x in v)):
            raise ValueError('Invalid closed sword vectors')
        # Only fields read by the closed reaction5 path are reconstructed.
        # No opaque HitInfo word, address or foreign native author is accepted.
        hit=bytearray(72)
        struct.pack_into('<I',hit,0,0x6F0F80)
        # Category, kind and flags of the closed captured profile only.
        struct.pack_into('<2I',hit,0x20,profile.category,profile.kind)
        struct.pack_into('<I',hit,0x30,profile.flags)
        for at,values in zip((4,0x10,0x3C),vectors):struct.pack_into('<3f',hit,at,*values)
    initial[0x80:0xC8]=hit
    initial[0xC8:0xE0]=profile.desc
    struct.pack_into('<I',initial,0x118,context.profile)
    if target_hp is not None:
        if type(target_hp) is not int or not 0<=target_hp<context.expected_hp:
            raise ValueError('A native reconciliation may only reduce living HP')
        struct.pack_into('<2I',initial,0x100,1,target_hp)
    data[CONTROL_OFFSET:] = initial
    return bytes(data)


def read_result(control):
    """A retained record is NOT proof of HP loss; observe native damage separately."""
    if len(control) != CONTROL_SIZE:
        raise ValueError('Expected the complete private control block')
    word = lambda at: struct.unpack_from('<I',control,at)[0]
    signed = lambda at: struct.unpack_from('<i',control,at)[0]
    if word(0) != MAGIC or word(4) != 1:
        raise ValueError('Unknown private trial control version')
    state = TrialState(word(8))
    retained = (state == TrialState.RETURNED and word(0x68) == 1
                and word(0x6C) == 0x20 and word(0x70) == 8 and word(0xFC) == 5)
    return dict(state=state.name,refusal=Refusal(word(12)).name,native_calls=word(0x7C),
                commit_calls=word(0xF8),
                native_return=word(0x68),record_mask=word(0x6C),amount=word(0x70),
                record_retained=retained,hp_before=signed(0x74),
                hp_after_deposit=signed(0x78),native_hp_loss_confirmed=False,
                reconciliation=bool(word(0x100)),target_hp=word(0x104),
                applied_nonce=word(0x108),applied_hp=word(0x10C),
                original_damage=word(0x110),applied_damage=word(0x114))
