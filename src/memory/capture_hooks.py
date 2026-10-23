"""Native observers of local prop transitions, writing the shared world event ring.

Each capture replaces one verified instruction of the game by a jump to its
trampoline slot, which reaches code copied into the world reservation. All
sites are checked before anything is written; all are removed together.
"""
from dataclasses import dataclass
from pathlib import Path
import struct
import time
from src.memory.remote_model import branch,MAILBOX
from src.memory.world_layout import WORLD_POINTER,WORLD_RESERVE,TRAMPOLINE_AREA,slot,reserve


@dataclass(frozen=True)
class Capture:
    name:str
    hook:int
    fingerprint:bytes
    slot:str
    reserve:str
    binary:str

    @property
    def entry(self):return slot(self.slot)
    @property
    def offset(self):return reserve(self.reserve)
    @property
    def original(self):return self.fingerprint[:4]
    @property
    def jump(self):return branch(self.hook,self.entry)


CAPTURES=(
    # 2395CC right after bl 238DD4: any shrub leaving its intact state.
    Capture('shrub',0x23979C,bytes.fromhex('840184e0800080e0800188e00010d0e5'),
            'shrub_cut_trampoline','shrub_capture','shrub_capture.bin'),
    # MojSignboard hit decision: bl 65BB50 (changeState) at 1C2D50.
    Capture('signboard',0x1C2D50,bytes.fromhex('7e6312eb0400a0e193780debd015d5e1'),
            'sign_trampoline','sign_capture','sign_capture.bin'),
)


@dataclass(frozen=True)
class MassSite:
    """A native "strb r1,[r0]" storing a Mass instance's new state.

    mass_capture.arm is assembled once per site: it records (handle, index,
    effect=value) when the instance leaves one of its idle states, then does
    the displaced store and returns to hook+4. batch/index are offsets of the
    native registers in the observer's saved frame (r0 +4 ... r11 +0x30; the
    native stack starts at +0x3C).
    """
    name:str
    hook:int
    fingerprint:bytes
    vtable:int
    value:int
    idle:tuple
    batch:int
    index:int

    def substitutions(self):
        return dict(VTABLE=hex(self.vtable),VALUE=str(self.value),IDLE_A=str(self.idle[0]),
                    IDLE_B=str(self.idle[1]),BATCH=hex(self.batch),INDEX=hex(self.index),RETURN=hex(self.hook+4))

    def capture(self):
        return Capture(self.name,self.hook,self.fingerprint,self.name+'_trampoline',self.name,self.name+'.bin')


MASS_SITES=(
    # MassPot (327/328) update 4BA694: breaking state 1 (index r4, batch on
    # the native stack +0x48); idle 0, wobble 3.
    MassSite('pot_break',0x4BA838,bytes.fromhex('0010c0e5040384e0000185e00010d0e5'),0x6EB390,1,(0,3),0x84,0x14),
    # Pot vtable slot 20 4B9AF0, conversion to a carried actor: state 2 (batch r4, index r6).
    MassSite('pot_lift',0x4B9B24,bytes.fromhex('0010c0e5200694e5060050e124569495'),0x6EB390,2,(0,3),0x14,0x1C),
    # MassRock (323-326) update 4D5FAC: breaking state 2 (batch fp, index r5); idle 0, wobble 1.
    MassSite('rock_break',0x4D6248,bytes.fromhex('0010c0e5850165e0000180e0800186e0'),0x6EC3B4,2,(0,1),0x30,0x18),
    # Rock vtable slot 20 4D473C, lifted: removal state 3 (batch r4, index r5).
    MassSite('rock_lift',0x4D4778,bytes.fromhex('0010c0e5780494e5050050e180149485'),0x6EC3B4,3,(0,1),0x14,0x18),
)
CAPTURES+=tuple(site.capture() for site in MASS_SITES)


@dataclass(frozen=True)
class CarrySite:
    """The "movs DEST, r0" right after a lift spawns its carried actor (carry_bind.arm)."""
    name:str
    hook:int
    fingerprint:bytes
    dest:str
    index:int
    lift:int
    vtables:tuple

    def substitutions(self):
        return dict(DEST=self.dest,INDEX=hex(self.index),LIFT=str(self.lift),VTABLE_A=hex(self.vtables[0]),
                    VTABLE_B=hex(self.vtables[-1]),RETURN=hex(self.hook+4))

    def capture(self):
        return Capture(self.name,self.hook,self.fingerprint,self.name+'_trampoline',self.name,self.name+'.bin')


CARRY_SITES=(
    # Pot slot 20 4B9AF0: index r6, lift effect 2 (pot_lift).
    CarrySite('pot_carry',0x4B9CD0,bytes.fromhex('0040b0e1000057131e00000acc0294e5'),'r4',0x1C,2,(0x6EB390,)),
    # Grass/lawn slot 20 505DD8 (ObjGrassLift): index r5, lift = 237288 kind 4.
    CarrySite('grass_carry',0x505F8C,bytes.fromhex('0040b0e1000056131e00000acc0294e5'),'r4',0x18,4,(0x6EE60C,0x6EC2D8)),
    # Rock slot 20 4D473C: index r5, lift effect 3 (rock_lift).
    CarrySite('rock_carry',0x4D4934,bytes.fromhex('0070b0e1000056134300000acc0297e5'),'r7',0x18,3,(0x6EC3B4,)),
)
CAPTURES+=tuple(site.capture() for site in CARRY_SITES)
# 520F98, the player position pointer: enemies of the combat catalogue get the
# nearest of Link and the companion (nearest_player.arm). Not a capture, but the
# same verified install/restore of one replaced instruction.
CAPTURES+=(Capture('nearest_player',0x520F98,bytes.fromhex('08009fe5000090e51c0090e51eff2fe1'),
                   'nearest_trampoline','nearest_player','nearest_player.bin'),)
# 1DE420, the Player calc's first instruction (player_life.arm): soft collision
# with the companion, and the spectator mode of a Link waiting for a weathervane.
CAPTURES+=(Capture('player_life',0x1DE420,bytes.fromhex('f0472de90040a0e1068c80e2017b80e2'),
                   'life_trampoline','player_life','player_life.bin'),
            # 19AE0C, the input manager's freeze decision: a spectator's gameplay
            # wrappers are frozen as during events (input_lock.arm).
            Capture('input_lock',0x19AE0C,bytes.fromhex('000050e32200001a010054e30500000a'),
                    'input_lock_trampoline','input_lock','input_lock.bin'),
            # 1DCC34, Player::updateModel's call of 1DD530: a spectator is hidden
            # right after it with the game's own sequence (life_hide.arm).
            Capture('life_hide',0x1DCC34,bytes.fromhex('3d0200ebb00197e5fe0550e30b00001a'),
                    'life_hide_trampoline','life_hide','life_hide.bin'),)


@dataclass(frozen=True)
class ReactionSite:
    """An actor prop's own reaction routine, observed at its first instruction
    (reaction_capture.arm): r0 = the actor, its state byte at +state before
    and after it."""
    name:str
    hook:int
    fingerprint:bytes
    state:int
    displaced:str

    def substitutions(self):
        return dict(STATE=hex(self.state),RETURN=hex(self.hook+4),DISPLACED=self.displaced)

    def capture(self):
        return Capture(self.name,self.hook,self.fingerprint,self.name+'_trampoline',self.name,self.name+'.bin')


REACTION_SITES=(
    # MojSwitchHit (222) hit reaction 1C4538(switch, sound): toggles +40C
    # (1C4388), plays its sound and sets the room flag of its event (+344).
    ReactionSite('switch_hit',0x1C4538,bytes.fromhex('70402de90040a0e1410e80e20160a0e1'),0x40C,'push {r4, r5, r6, lr}'),
    # MojSwitchStep (229) setState 3AAAE4(switch, state): 1 pressed (model
    # down, SE_Switch_ON), 3 released; the state is stored at +42C.
    ReactionSite('step_switch',0x3AAAE4,bytes.fromhex('f0402de90040a0e10170b0e1028b2ded'),0x42C,'push {r4, r5, r6, r7, lr}'),
)
CAPTURES+=tuple(site.capture() for site in REACTION_SITES)
# 10BEB4, the animation notification every changeState copy makes right after
# storing a machine's new state (machine still in r4): one observer for the
# machine of every catalogued prop (machine_capture.arm).
CAPTURES+=(Capture('machine',0x10BEB4,bytes.fromhex('0120a0e3000a9fed3e0700ea00000000'),
                   'machine_trampoline','machine_capture','machine_capture.bin'),)
# Shared by every CarrySite: counter, then 8 x (actor handle, batch handle, index, serial).
CARRIED_TABLE=reserve('pot_carry')+0xF00


class NativeCaptureHooks:
    def __init__(self,world,captures=CAPTURES):
        self.world=world;self.bridge=world.bridge;self.captures=captures
        self.hooked=[];self.trampolines=[];self.closed=False

    def start(self):
        b=self.bridge;w=self.world
        if (w.closed or not w.trial.installed or not w.allocation
                or b.read_u32(WORLD_POINTER)!=w.allocation or b.read_u32(w.allocation+4)!=1
                or any(b.read_memory(c.hook,len(c.fingerprint))!=c.fingerprint
                       or not self._free_entry(c) for c in self.captures)):
            raise ValueError('Observation des objets incompatible avec le profil vérifié.')
        for c in self.captures:
            code=Path(__file__).with_name(c.binary).read_bytes()
            size=WORLD_RESERVE[c.reserve][1]
            if len(code)>size:raise ValueError('Observation trop grande : '+c.name)
            # The whole block is written: tables after the code start empty.
            if not (b.write_memory(w.allocation+c.offset,code.ljust(size,b'\0'))
                    and b.read_memory(w.allocation+c.offset,len(code))==code):
                raise RuntimeError('Préparation de l’observation non confirmée : '+c.name)
        if any(c.name=='player_life' for c in self.captures):
            from src.memory.party_native import prepare
            prepare(w)
        for c in self.captures:
            self.trampolines.append(c)
            entry=struct.pack('<2I',0xE51FF004,w.allocation+c.offset)
            if not b.write_memory(c.entry,entry) or b.read_memory(c.entry,8)!=entry:
                raise RuntimeError('Entrée de l’observation non confirmée : '+c.name)
            self.hooked.append(c)
            if not b.write_memory(c.hook,c.jump) or b.read_memory(c.hook,4)!=c.jump:
                raise RuntimeError('Installation de l’observation non confirmée : '+c.name)

    def _free_entry(self,c):
        """Its slot is empty. The renderer never rewrites the second trampoline
        area: a trampoline a stopped companion left there is free again once the
        game's own instruction is back (checked with the fingerprint)."""
        entry=self.bridge.read_memory(c.entry,8)
        if entry==bytes(8):return True
        return (TRAMPOLINE_AREA[0]<=c.entry<TRAMPOLINE_AREA[1] and entry is not None
                and struct.unpack_from('<I',entry)[0]==0xE51FF004)

    def close(self):
        if self.closed:return True
        b=self.bridge;w=self.world
        for c in list(self.hooked):
            if b.read_memory(c.hook,4) not in (c.jump,c.original):return False
            if not b.write_memory(c.hook,c.original) or b.read_memory(c.hook,4)!=c.original:return False
            self.hooked.remove(c)
        if self.trampolines:
            # A later frame proves the game thread left every observer.
            frame=b.read_u32(MAILBOX+0x50);deadline=time.monotonic()+2
            if type(frame) is not int:return False
            while b.read_u32(MAILBOX+0x50)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
            for c in list(self.trampolines):
                entry=struct.pack('<2I',0xE51FF004,w.allocation+c.offset)
                if b.read_memory(c.entry,8) not in (entry,bytes(8)):return False
                if not b.write_memory(c.entry,bytes(8)) or b.read_memory(c.entry,8)!=bytes(8):return False
                self.trampolines.remove(c)
        self.closed=True;return True
