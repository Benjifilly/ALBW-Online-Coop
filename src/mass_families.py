"""Closed catalogues of EUR props shared between the two games.

Two kinds: Mass families (many instances in one batch actor: grass, shrubs)
and ActorProp (one actor with a native state machine: signboards).

One row per (type, vtable) pair verified on the local EUR executable. It is the
single source of truth: the native identity table (world_mass.arm) is generated
from it by scripts/build_remote_model.py, and the reader, journal and restore
services consult it. Public Ghidra ObjInfo names only identify a family;
factory, vtable, layout and update/cut code are checked on the EUR game.
MassGrassHole and independently liftable grass are not layout-compatible.

To add a family: verify its layout in the game, add a row, rebuild, and give
its capture/replay route (grass: 237288; shrub: 23979C; pot/rock: capture_hooks.MASS_SITES; each with a dispatch branch).
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class MassFamily:
    name:str
    kind:int
    vtable:int
    count_offset:int
    records_offset:int
    record_size:int
    terminal_state:int
    loader_restore:bool  # NativeGrassRestore can apply terminal state before draw


_GRASS=dict(count_offset=0x630,records_offset=0x634,record_size=0x104,terminal_state=5,loader_restore=True)
_SHRUB=dict(count_offset=0x62C,records_offset=0x630,record_size=0xD8,terminal_state=8,loader_restore=False)
_POT=dict(count_offset=0x620,records_offset=0x624,record_size=0x104,terminal_state=4,loader_restore=False)
_ROCK=dict(count_offset=0x620,records_offset=0x624,record_size=0x118,terminal_state=4,loader_restore=False)

FAMILIES=MappingProxyType({f.kind:f for f in (
    MassFamily('grass_light',316,0x6EE60C,**_GRASS),
    MassFamily('grass_dark',317,0x6EE60C,**_GRASS),
    MassFamily('lawn_light',319,0x6EC2D8,**_GRASS),
    MassFamily('lawn_dark',320,0x6EC2D8,**_GRASS),
    # MassShrubLight: own base 1E56CC, update 2395CC, contact 238DD4.
    MassFamily('shrub_light',321,0x6D7C50,**_SHRUB),
    # MassPotLight: update 4BA694; states 0 intact, 3 wobble, 1 breaking, 2 hide, 4 gone.
    MassFamily('pot_light',327,0x6EB390,**_POT),
    MassFamily('pot_skull',328,0x6EB390,**_POT),
    # MassRock/RockL/RockSDark/RockLDark: factory 60AA18, update 4D5FAC; states
    # 0 intact, 1 wobble, 2 breaking, 3 removal (4D57E8), 4 gone.
    MassFamily('rock',323,0x6EC3B4,**_ROCK),
    MassFamily('rock_large',324,0x6EC3B4,**_ROCK),
    MassFamily('rock_dark',325,0x6EC3B4,**_ROCK),
    MassFamily('rock_large_dark',326,0x6EC3B4,**_ROCK),
)})


GRASS_PROFILES=MappingProxyType({k:f.vtable for k,f in FAMILIES.items() if f.loader_restore})


def family(kind):
    return FAMILIES.get(kind) if type(kind) is int else None

def mass_profile(kind):
    """Vtable of any shared Mass family."""
    f=family(kind);return f.vtable if f else None

def grass_profile(kind):
    """Vtable of a family restored by the grass loader hooks (grass and lawn)."""
    f=family(kind);return f.vtable if f and f.loader_restore else None

def shrub_profile(kind):
    f=family(kind);return f.vtable if f and f.kind==321 else None

def native_table():
    """ARM source rows for world_mass.arm, terminated by a zero type."""
    rows=[f'.short {f.kind}, {f.record_size:#x}\n.word {f.vtable:#x}\n.short {f.count_offset:#x}, {f.records_offset:#x}'
          for f in FAMILIES.values()]
    return '\n'.join(rows+['.short 0, 0\n.word 0\n.short 0, 0'])


@dataclass(frozen=True)
class ActorProp:
    """A prop that is a whole actor driven by its own native state machine.

    The hit decision site calls changeState(actor+machine_offset, new). The
    capture hook there records (from, to); the replay calls the same native
    changeState when the other copy is still in `from`. Identity on the wire
    is the actor placement key [type, placement, x, y, z] (x10000 ints).
    """
    name:str
    kind:int
    vtable:int
    machine_offset:int    # state machine; current state is the signed byte at +4
    change_state:int      # native changeState(machine, state)
    decision_site:int     # bl change_state reached after a hit
    states:frozenset      # target states that may be replicated
    # A reaction prop has no machine: change_state is its own reaction routine,
    # replayed as routine(actor, 1); its state byte is at machine_offset+4.
    reaction:bool=False
    # Observed by its own decision hook, not by the common machine observer.
    own_hook:bool=False

    @property
    def mask(self):return sum(1<<s for s in self.states)


# MojSignboard: ctor 1C3D30, state table 69D838, sounds SE_Signboard_CUT/BURN/
# BREAK. The active state reads the hit code at 1C2C80 and decides at 1C2D50:
# codes 5/10 -> 1, 1/13 -> 2, 2/8/15 -> 3. 1C3C18 (->4) and 1C42E0 (->0) are
# internal progressions that the replaying copy runs by itself.
ACTOR_PROPS=MappingProxyType({p.kind:p for p in (
    ActorProp('signboard',277,0x6CFA74,0x788,0x65BB50,0x1C2D50,frozenset({1,2,3}),own_hook=True),
    # MojSwitchHit (crystal switch): its hit reaction 1C4538 toggles +40C,
    # plays the sound and sets its room flag (opens the passage, 23/10).
    ActorProp('crystal_switch',222,0x6CFB70,0x408,0x1C4538,0x1C4538,frozenset({0,1}),reaction=True),
    # MojSwitchStep (floor switch): only its press is replayed, setState(switch,
    # 1). Its dungeon flag (what it opens) is already shared progress (23/10).
    ActorProp('step_switch',229,0x6D4D24,0x428,0x3AAAE4,0x3AAAE4,frozenset({1}),reaction=True),
    # MojSwitchCandle (torch): machine +40C, 0 out / 1 lit. Machines are
    # observed in common (machine_capture.arm, 10BEB4) and replayed with the
    # generic changeState 65BB50, identical to every class copy.
    ActorProp('torch',230,0x6DC288,0x40C,0x65BB50,0x10BEB4,frozenset({0,1})),
)})


def actor_prop(kind):
    return ACTOR_PROPS.get(kind) if type(kind) is int else None

def prop_effect(source,target):
    """Wire effect of a state change: target in the low nibble, source above."""
    return (source&0xF)<<4|(target&0xF)

def prop_native_table():
    """ARM rows for world_prop.arm: type u16, machine offset u16, vtable, state mask
    (bit 31: reaction routine, bit 30: own hook), changeState or reaction routine."""
    rows=[f'.short {p.kind}, {p.machine_offset:#x}\n.word {p.vtable:#x}, {p.mask|p.reaction<<31|p.own_hook<<30:#x}, {p.change_state:#x}'
          for p in ACTOR_PROPS.values()]
    return '\n'.join(rows+['.short 0, 0\n.word 0, 0, 0'])
