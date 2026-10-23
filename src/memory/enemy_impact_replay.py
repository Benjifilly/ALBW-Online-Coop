"""Native impact replay (closed captured attack profiles) in the owned world reservation.

No packet selects an address, native function, damage amount, or actor pointer.
Closed EUR targets retain native gates and reactions. AI and loot authority
are not implemented here; this is not universal world synchronization.
"""
import struct
import time
from pathlib import Path

from src.memory.enemy_impact_trial import (HOOK,ORIGINAL,FINGERPRINTS,CONTROL_OFFSET,
    CONTROL_SIZE,TrialContext,TrialState,assembled_payload,build_image,read_result)
from src.memory.enemy_sync import EnemySynchronizer
from src.combat_profiles import ATTACK_PROFILES,SWORD_PROFILE
from src.memory.native_jobs import PRIVATE_SIZE
from src.memory.remote_model import MAILBOX,branch
from src.memory.world_layout import WORLD_POINTER,WORLD_ABI,ENEMY_DAMAGE_HOOK,slot,reserve

OFFSET=reserve('impact')
TRAMPOLINE=slot('impact_trampoline')
HEALTH_OFFSET=reserve('health')


class NativeEnemyImpactReplay:
    def __init__(self,world):
        self.world=world;self.trial=world.trial;self.bridge=world.bridge
        self.address=world.allocation+OFFSET;self.control=self.address+CONTROL_OFFSET
        self.machine=assembled_payload();self.jump=branch(HOOK,TRAMPOLINE)
        self.hooked=False;self.trampoline=False;self.closed=False;self.uncertain=False
        self.pending=None;self.sequence=0;self.finished_frame=None;self.deadline=0
        self.disarming_frame=None
        self.receipt_deadline=0
        self.last_result=None
        self.reconciliation=False

    def start(self,*,reconciliation=False):
        b=self.bridge;w=self.world
        if (not w.allocation or w.closed or not self.trial.installed
                or not 0x14000000<=w.allocation<=0x1C000000-PRIVATE_SIZE
                or b.read_u32(WORLD_POINTER)!=w.allocation
                or b.read_u32(w.allocation+0x90)!=WORLD_ABI
                or b.read_memory(TRAMPOLINE,8)!=bytes(8)):
            raise ValueError('Le monde natif ne peut pas posséder le rejeu de dégâts.')
        for at,data in FINGERPRINTS.items():
            expected=w.enemy_jump+data[4:] if at==ENEMY_DAMAGE_HOOK else data
            if b.read_memory(at,len(data))!=expected:raise ValueError('Pipeline de combat différent du profil vérifié.')
        image=self.machine.ljust(CONTROL_OFFSET+CONTROL_SIZE,b'\0')
        if not b.write_memory(self.address,image) or b.read_memory(self.address,len(image))!=image:
            raise RuntimeError('Le code privé des impacts n’est pas confirmé.')
        if reconciliation:
            self.reconciliation=True
            code=Path(__file__).with_name('enemy_health.bin').read_bytes()
            if not (len(code)<=0x1000 and b.read_u32(w.allocation+4)==1
                    and b.write_memory(w.allocation+HEALTH_OFFSET,code)
                    and b.read_memory(w.allocation+HEALTH_OFFSET,len(code))==code
                    and b.write_u32(w.allocation+0xA4,self.control)
                    and b.write_u32(w.allocation+0xA0,w.allocation+HEALTH_OFFSET)):
                raise RuntimeError('La réaction native des ennemis attend son propriétaire.')
        self.trampoline=True
        entry=struct.pack('<2I',0xE51FF004,self.address)
        if not b.write_memory(TRAMPOLINE,entry) or b.read_memory(TRAMPOLINE,8)!=entry:
            self.uncertain=True;raise RuntimeError('Entrée privée des impacts incertaine.')
        self.hooked=True
        if not b.write_memory(HOOK,self.jump) or b.read_memory(HOOK,4)!=self.jump:
            self.uncertain=True;raise RuntimeError('Observation des commandes d’impact incertaine.')

    def sensitivity(self,address,profile):
        """Native sensitivity halfword of a profile's reaction type for this actor."""
        table=self.bridge.read_u32(address+0x4B0)
        if type(table) is not int or not table:return None
        value=self.bridge.read_u16(table+0xE+2*ATTACK_PROFILES[profile].reaction)
        return value if type(value) is int else None

    def usable_profile(self,address,preferred=SWORD_PROFILE):
        """The wanted profile, else the first one this enemy takes damage from.

        A low byte 0 refuses the hit and 1 keeps only the reaction (34C8CC):
        a reconciliation needs a profile whose damage reaches the subtraction.
        """
        order=[preferred]+[i for i in range(len(ATTACK_PROFILES)) if i!=preferred]
        for profile in order:
            value=self.sensitivity(address,profile)
            if value is not None and value&0xFF>=2:return profile
        return None

    def submit(self,target,player,vectors,*,target_hp=None,profile=SWORD_PROFILE):
        if self.closed or self.uncertain or self.pending is not None:return False
        if target_hp is not None and not self.reconciliation:return False
        # The non-reconciled trial confirms the sword's own native consequence.
        if target_hp is None and profile!=SWORD_PROFILE:return False
        if type(profile) is not int or not 0<=profile<len(ATTACK_PROFILES):return False
        b=self.bridge;c=target['_context'];survey=EnemySynchronizer(b)
        fresh=survey._read_actor(c,target['handle'])
        if fresh is None or survey._identity(fresh)!=survey._identity(target) or fresh['hp']!=target['hp']:
            return False
        sensitivity=b.read_u32(fresh['address']+0x4B0)
        if type(sensitivity) is not int:return False
        reaction=ATTACK_PROFILES[profile].reaction
        try:
            context=TrialContext(c.owner,c.game,c.table,c.end,*c.room,b.read_u32(0x712468),
                player,b.read_u32(player+0x2CC),b.read_u32(player+0x600),fresh['address'],fresh['handle'],
                fresh['type'],fresh['placement_id'],fresh['profile'],sensitivity,
                b.read_u32(fresh['address']+0x43C),fresh['hp'],fresh['max_hp'],
                b.read_u32(player+0x2D0),b.read_u32(player+0x614),b.read_u16(sensitivity+0xE+2*reaction),
                dynamic_binding=bool(fresh.get('dynamic_key')),profile=profile)
            self.sequence=(self.sequence+1)&0xFFFFFFFF or 1
            image=build_image(context,nonce=self.sequence,synthetic_geometry_acknowledged=True,
                              vectors=vectors,target_hp=target_hp)
        except (ValueError,TypeError):
            return False
        data=bytearray(image[CONTROL_OFFSET:]);struct.pack_into('<I',data,8,0)
        # The prior command has completed and crossed a native frame before
        # poll() releases it. Keep state0 throughout all argument preparation.
        if not b.write_u32(self.control+8,0) or b.read_u32(self.control+8)!=0:
            self.uncertain=True;raise RuntimeError('L’ancien impact ne peut pas être désarmé.')
        if not b.write_memory(self.control,data) or b.read_memory(self.control,len(data))!=data:
            self.uncertain=True;raise RuntimeError('Les données d’impact ne sont pas confirmées.')
        self.pending=dict(target=fresh,sequence=self.sequence,target_hp=target_hp);self.finished_frame=None
        self.receipt_deadline=0
        self.disarming_frame=None;self.deadline=time.monotonic()+2
        if not b.write_u32(self.control+8,TrialState.ARMED):
            self.uncertain=True;raise RuntimeError('Impact publié sans confirmation ; aucune répétition.')
        return True

    def poll(self):
        if self.pending is None:return None
        raw=self.bridge.read_memory(self.control,CONTROL_SIZE)
        if raw is None or len(raw)!=CONTROL_SIZE:return None
        if struct.unpack_from('<I',raw,0x10)[0]!=self.pending['sequence']:
            self.uncertain=True;raise RuntimeError('La commande d’impact a changé de génération.')
        result=read_result(raw);self.last_result=result;state=result['state']
        frame=self.bridge.read_u32(MAILBOX+0x50)
        if state in (TrialState.RETURNED.name,TrialState.REJECTED.name):
            if type(frame) is not int:return None
            if self.finished_frame is None:
                self.finished_frame=frame;self.receipt_deadline=time.monotonic()+2;return None
            if frame==self.finished_frame:return None
            if self.pending['target_hp'] is not None:
                # Only the final native subtraction can set this nonce. A
                # later frame proves its displaced SUB/STRH have also run.
                if (result['applied_nonce']==self.pending['sequence']
                        and result['applied_hp']==self.pending['target_hp']
                        and result['applied_damage']==self.pending['target']['hp']-self.pending['target_hp']):
                    self.pending=None
                    return dict(outcome='retained',native=result)
                if (result['native_calls']==0 or result['commit_calls']==0):
                    # No committed hit: busy record, cooldown or changed HP.
                    # It is safe to inspect a new local epoch before retrying.
                    self.pending=None
                    return dict(outcome='deferred',native=result)
                self.uncertain=True
                raise RuntimeError('Un coup natif engagé attend sa conséquence ; aucune répétition.')
            if result['record_retained']:
                target=self.pending['target'];c=target['_context'];survey=EnemySynchronizer(self.bridge)
                fresh=survey._read_actor(c,target['handle'])
                if fresh is not None:
                    result['observed_hp_after']=fresh['hp']
                    outcome='retained' if fresh['hp']==max(0,target['hp']-8) else 'diverged'
                elif target['hp']<=8 and survey._read_context(c.room)==c:
                    slot=c.table+(target['handle']>>22)*4
                    current=self.bridge.read_u32(slot)
                    retired=(type(current) is int and current!=target['address']
                             and self.bridge.read_u32(slot)==current)
                    if current==target['address']:
                        retired=(self.bridge.read_u32(current+0x2CC)==target['handle']
                                 and self.bridge.read_u8(current+0x2D7)==1
                                 and self.bridge.read_u32(slot)==current)
                    if not retired:
                        if time.monotonic()<self.receipt_deadline:return None
                        self.uncertain=True;raise RuntimeError('Le retrait du soldat attend une lecture fiable.')
                    outcome='retained';result['observed_retirement']=True
                else:
                    if time.monotonic()<self.receipt_deadline:return None
                    self.uncertain=True;raise RuntimeError('Les conséquences du coup attendent une lecture fiable.')
            elif (state==TrialState.REJECTED.name and result['refusal']=='LOCAL_RECORD_BUSY'
                  and result['native_calls']==0 and result['commit_calls']==0):
                # The pre-call guard saw an existing native hit. It performed
                # no work, so this delivery can wait for the occupied record.
                outcome='deferred'
            else:outcome='refused'
            self.pending=None
            return dict(outcome=outcome,native=result)
        if time.monotonic()<self.deadline:return None
        if state==TrialState.ARMED.name:
            if self.disarming_frame is None:
                if type(frame) is not int:return None
                if not self.bridge.write_u32(self.control+0x14,0):
                    self.uncertain=True;raise RuntimeError('Expiration de l’impact non confirmée.')
                self.disarming_frame=frame;return None
            if type(frame) is not int or frame==self.disarming_frame:return None
            # It is still ARMED after disable+later frame: no native call ran.
            if self.bridge.read_u32(self.control+0x14)!=0:return None
            self.pending=None
            return dict(outcome='deferred',native=result)
        self.uncertain=True
        raise RuntimeError('Un impact réclamé attend son retour ; aucune répétition automatique.')

    def close(self):
        if self.closed:return True
        b=self.bridge
        if self.reconciliation:
            if not b.write_u32(self.world.allocation+0xA0,0):return False
        if self.hooked:
            if not b.write_u32(self.control+0x14,0):return False
            if b.read_memory(HOOK,4) not in (self.jump,ORIGINAL):return False
            if not b.write_memory(HOOK,ORIGINAL) or b.read_memory(HOOK,4)!=ORIGINAL:return False
            initial=b.read_u32(MAILBOX+0x50);deadline=time.monotonic()+2
            if type(initial) is not int:return False
            while True:
                frame=b.read_u32(MAILBOX+0x50)
                if type(frame) is int and frame!=initial:break
                if time.monotonic()>=deadline:return False
                time.sleep(.02)
            self.hooked=False
        if self.trampoline:
            expected=struct.pack('<2I',0xE51FF004,self.address)
            if b.read_memory(TRAMPOLINE,8) not in (expected,bytes(8)):return False
            if not b.write_memory(TRAMPOLINE,bytes(8)) or b.read_memory(TRAMPOLINE,8)!=bytes(8):return False
            self.trampoline=False
        # The world retains/frees the containing reservation after its own
        # later native barrier. This service never frees a sub-allocation.
        self.closed=True;return True
