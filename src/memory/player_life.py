"""This Link's spectator mode and warps, through player_life.arm's control.

The hook itself is one of the world captures (capture_hooks 'player_life'),
installed and removed with them. This only writes its control block: the
spectator flag under a frame lease (a stopped companion lets Link reappear),
and warp requests run once by the game thread through the game's own scene
change 4EF3F0 (the routine doors use).
"""
import struct
from pathlib import Path
from types import SimpleNamespace
from src.memory.world_layout import WORLD_POINTER,WORLD_MAGIC,WORLD_ABI,reserve
from src.rpc.azahar_bridge import TITLE_ID_EUR

CONTROL=0x400
MAGIC=0x4546494C
SCENE_CHANGE=0x4EF3F0
# Transition 2: the game's own restart (364864). 4 is an event-only fade that
# stays black without its script (23/10, Link 2 had to be restarted).
WARP_TYPE=2
LEASE=120


def scene(course,stage,entrance):
    """A scene descriptor as the game stores it (game +0x18 current, +0x48 request)."""
    return struct.pack('<B3xIHBB',course,stage,entrance,1,0)


class NativePlayerLife:
    def __init__(self,world):
        self.world=world;self.bridge=world.bridge;self.serial=0

    @classmethod
    def recover(cls,bridge):
        """Réarme seulement la vie après un plantage, sans reprendre le rendu."""
        from src.memory.capture_hooks import CAPTURES
        b=bridge;a=b.read_u32(WORLD_POINTER)
        if (b.attached_title_id!=TITLE_ID_EUR or type(a) is not int or a%0x1000
                or not 0x14000000<=a<=0x1C000000-0x200000
                or b.read_u32(a)!=WORLD_MAGIC or b.read_u32(a+0x90)!=WORLD_ABI):return None
        for c in (c for c in CAPTURES if c.name in ('player_life','input_lock','life_hide')):
            code=Path(__file__).with_name(c.binary).read_bytes()
            if c.name=='player_life':code=code[:CONTROL]
            if (b.read_memory(c.hook,4)!=c.jump
                    or b.read_memory(c.entry,8)!=struct.pack('<2I',0xE51FF004,a+c.offset)
                    or b.read_memory(a+c.offset,len(code))!=code):return None
        from src.memory.party_native import CONTROL as party_control,MAGIC as party_magic
        code=Path(__file__).with_name('party_native.bin').read_bytes()[:party_control]
        at=a+reserve('party_life')
        if b.read_memory(at,len(code))!=code or b.read_u32(at+party_control)!=party_magic:return None
        if b.read_u32(WORLD_POINTER)!=a:return None
        life=cls(SimpleNamespace(bridge=b,allocation=a,closed=False))
        return life if life._ready() else None

    @property
    def control(self):return self.world.allocation+reserve('player_life')+CONTROL

    def _ready(self):
        b=self.bridge;w=self.world
        return (not w.closed and w.allocation and b.read_u32(WORLD_POINTER)==w.allocation
                and b.read_u32(self.control)==MAGIC)

    def spectate(self,enabled):
        """Keep (renew) or end the spectator mode. False if not installed."""
        if not self._ready():return False
        b=self.bridge;c=self.control
        if not enabled:return b.write_u32(c+4,0)
        return b.write_u32(c+8,LEASE) and b.write_u32(c+4,1)

    def spectating(self):
        return self._ready() and self.bridge.read_u32(self.control+4) in (1,2)

    def hold(self):
        """Défaite collective : caché et figé, sans suivre l'ancien compagnon."""
        if not self._ready():return False
        return self.bridge.write_u32(self.control+8,LEASE) and self.bridge.write_u32(self.control+4,2)

    def warp(self,course,stage,entrance):
        """Ask the game thread for a scene change; returns its serial or None."""
        if not self._ready():return None
        b=self.bridge;c=self.control;self.serial=max(self.serial,b.read_u32(c+0x10) or 0)+1
        if not (b.write_memory(c+0x18,scene(course,stage,entrance)) and b.write_u32(c+0x24,WARP_TYPE)
                and b.write_u32(c+0xC,self.serial)):return None
        return self.serial

    def warp_result(self,serial):
        """None while pending, else the scene change's return (1 accepted)."""
        if not self._ready():return None
        b=self.bridge
        if b.read_u32(self.control+0x10)!=serial:return None
        return b.read_u32(self.control+0x14)
