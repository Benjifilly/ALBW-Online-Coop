"""Reversible native world events, independent of the render-only companion.

Azahar's code-read callback accepts mapped data pages. The tiny fixed entry is
in verified post-BSS padding; the larger position-independent payload and ring
live in a kernel-owned reservation. This is not a native-3DS plugin loader.
"""
from pathlib import Path
import struct
import time

from src.memory.mass_state import CUT_FUNCTION,CUT_ORIGINAL,CUT_FINGERPRINT
from src.memory.native_jobs import NativeRenderJobs,RenderJob,NativeJobRejected
from src.memory.remote_model import MAILBOX,branch
from src.memory.world_layout import (WORLD_MAGIC,WORLD_ABI,WORLD_POINTER,WORLD_GRASS_ENTRY,WORLD_ENEMY_ENTRY,
    ENEMY_DAMAGE_HOOK,ENEMY_DAMAGE_ORIGINAL,ENEMY_DAMAGE_FINGERPRINT,WORLD_HIT_ENTRY,
    ENEMY_HIT_HOOK,ENEMY_HIT_ORIGINAL,ENEMY_HIT_FINGERPRINT,reserve)


class NativeWorldEvents:
    def __init__(self,trial,context,player):
        self.trial=trial;self.bridge=trial.bridge;self.jobs=NativeRenderJobs(trial)
        self.context=context;self.player=player;self.allocation=0;self.sequence=0
        self.pending=None;self.read_sequence=0;self.published=False;self.uncertain=False
        self.hooked=False;self.closed=False
        self._reserve_uncertain=False
        self.enemy_hooked=False;self.combat_sequence=0
        self.hit_hooked=False;self.hit_sequence=0
        self.player_handle=None
        self.impact=None
        self.pose=None
        self.foliage=None
        self.captures=None
        self.mirror=None
        self.items=None
        self.jump=branch(CUT_FUNCTION,WORLD_GRASS_ENTRY)
        self.enemy_jump=branch(ENEMY_DAMAGE_HOOK,WORLD_ENEMY_ENTRY)
        self.hit_jump=branch(ENEMY_HIT_HOOK,WORLD_HIT_ENTRY)

    def start(self):
        b=self.bridge;c=self.context
        if (not self.trial.installed or b.attached_pid!=c.pid or b.read_u32(WORLD_POINTER)
                or b.read_memory(CUT_FUNCTION,len(CUT_FINGERPRINT))!=CUT_FINGERPRINT
                or b.read_memory(ENEMY_DAMAGE_HOOK,len(ENEMY_DAMAGE_FINGERPRINT))!=ENEMY_DAMAGE_FINGERPRINT
                or b.read_memory(ENEMY_HIT_HOOK,len(ENEMY_HIT_FINGERPRINT))!=ENEMY_HIT_FINGERPRINT):
            raise ValueError('Le pipeline des herbes ne correspond pas à la version vérifiée.')
        try:result,address=self.jobs.call(RenderJob.RESERVE,timeout=3)
        except NativeJobRejected:raise
        except RuntimeError:
            # An unanswered kernel allocation may have succeeded. Keep this
            # extension's ownership and its shared queue; never auto-retry or
            # certify a clean shutdown while the returned address is unknown.
            self._reserve_uncertain=True;self.uncertain=True
            raise
        if result or not 0x14000000<=address<=0x1C000000-0x200000:
            raise RuntimeError('Réserve privée du monde indisponible.')
        self.allocation=address
        initial=bytearray(0x100)
        struct.pack_into('<I',initial,0,WORLD_MAGIC)
        struct.pack_into('<I',initial,0x90,WORLD_ABI)
        body=b.read_u32(self.player+0x600);handle=b.read_u32(self.player+0x2CC)
        self.player_handle=handle
        struct.pack_into('<10I',initial,0x18,self.player,handle,body,c.owner,c.game,c.table,c.end,*c.room,120)
        # Scene lighting of the private models, called by the dispatch each frame.
        light=address+reserve('model_light')
        struct.pack_into('<I',initial,0xB8,light)
        code=Path(__file__).with_name('world_payload.bin').read_bytes()
        lighting=Path(__file__).with_name('model_light.bin').read_bytes()
        if not (b.write_memory(address,bytes(0x13000)) and b.write_memory(light,lighting)
                and b.read_memory(light,len(lighting))==lighting and b.write_memory(address,initial)
                and b.write_memory(address+0x1000,code)
                and b.read_memory(address+0x1000,len(code))==code):
            raise RuntimeError('Préparation du monde impossible.')
        # The pointer and source jump are the only publication points. On a
        # lost acknowledgement assume publication and retain ownership.
        self.published=True
        if not b.write_u32(WORLD_POINTER,address):
            self.uncertain=True;raise RuntimeError('Publication du monde incertaine.')
        self.hooked=True
        if not b.write_memory(CUT_FUNCTION,self.jump):
            self.uncertain=True;raise RuntimeError('Observation native incertaine.')
        if b.read_memory(CUT_FUNCTION,4)!=self.jump or b.read_u32(WORLD_POINTER)!=address:
            self.uncertain=True;raise RuntimeError('L’observation native n’a pas confirmé son installation.')
        self.enemy_hooked=True
        if not b.write_memory(ENEMY_DAMAGE_HOOK,self.enemy_jump) or b.read_memory(ENEMY_DAMAGE_HOOK,4)!=self.enemy_jump:
            self.uncertain=True;raise RuntimeError('L’observation des impacts n’a pas confirmé son installation.')
        self.hit_hooked=True
        if not b.write_memory(ENEMY_HIT_HOOK,self.hit_jump) or b.read_memory(ENEMY_HIT_HOOK,4)!=self.hit_jump:
            self.uncertain=True;raise RuntimeError('L’observation des contacts n’a pas confirmé son installation.')
        if not b.write_u32(address+4,1):raise RuntimeError('Activation du monde impossible.')

    def renew(self):
        if self.closed or self.uncertain:return False
        return self.bridge.write_u32(self.allocation+0x3C,120)

    def captured(self):
        b=self.bridge;a=self.allocation
        if not a or self.closed:return []
        if b.read_u32(a+0x60):raise RuntimeError('Trop d’événements natifs : synchronisation interrompue.')
        end=b.read_u32(a+0x58)
        if type(end) is not int or not self.read_sequence<=end<=self.read_sequence+64:
            raise RuntimeError('Journal natif incohérent.')
        events=[]
        for sequence in range(self.read_sequence+1,end+1):
            raw=b.read_memory(a+0x100+((sequence-1)&63)*32,32)
            if raw is None or len(raw)!=32:raise RuntimeError('Événement natif incomplet.')
            serial,handle,index,kind,course,stage,owner,state=struct.unpack('<8I',raw)
            if serial!=sequence:raise RuntimeError('Événement natif remplacé pendant sa lecture.')
            if (course,stage)!=self.context.room or owner!=self.context.owner:
                raise RuntimeError('Une zone a changé pendant un événement natif.')
            events.append(dict(handle=handle,index=index,effect=kind,before=state))
        return events

    def acknowledge_capture(self,count):
        # Call only once those observations are durably queued in the local
        # reliable journal. A full network queue must never silently drop cuts.
        if type(count) is not int or not 0<=count<=64:return False
        self.read_sequence+=count
        return self.bridge.write_u32(self.allocation+0x5C,self.read_sequence)

    def submit(self,instance,effect):
        if self.pending is not None or self.uncertain or self.closed:return False
        # Mass effects 0..5; actor props carry from<<4|to (validated natively).
        if type(effect) is not int or not 0<=effect<=0xFF:return False
        b=self.bridge;a=self.allocation
        data=struct.pack('<5I',instance.handle,instance.index,effect,instance.address,instance.records)
        if not b.write_memory(a+0x40,data):raise RuntimeError('Préparation d’une interaction impossible.')
        self.sequence+=1;self.pending=self.sequence
        if not b.write_u32(a+8,self.sequence):
            self.uncertain=True;raise RuntimeError('Interaction publiée sans confirmation.')
        return True

    def combat_captured(self):
        """Diagnostic evidence only; these records cannot become wire commands."""
        b=self.bridge;a=self.allocation
        if not a or self.closed:return []
        if b.read_u32(a+0x80):raise RuntimeError('Le relevé natif des combats a débordé.')
        end=b.read_u32(a+0x78)
        if type(end) is not int or not self.combat_sequence<=end<=self.combat_sequence+64:
            raise RuntimeError('Journal de combats incohérent.')
        result=[]
        for sequence in range(self.combat_sequence+1,end+1):
            raw=b.read_memory(a+0x10000+((sequence-1)&63)*128,128)
            if raw is None or len(raw)!=128 or struct.unpack_from('<I',raw)[0]!=sequence:
                raise RuntimeError('Impact natif incomplet.')
            serial,handle,kind,placement,damage,before,maximum,course,stage,owner=struct.unpack_from('<10I',raw)
            signed=lambda value:value if value<0x80000000 else value-0x100000000
            result.append(dict(serial=serial,handle=handle,type=kind,placement_id=signed(placement),
                damage=damage,hp_before=signed(before),max_hp=signed(maximum),room=[course,stage],
                initial_position=list(struct.unpack_from('<3f',raw,0x28)),
                frame=struct.unpack_from('<I',raw,0x34)[0],attack_record=raw[0x38:].hex()))
        self.combat_sequence=end
        if not b.write_u32(a+0x7C,end):raise RuntimeError('Accusé de lecture des combats impossible.')
        return result

    def contacts_captured(self):
        b=self.bridge;a=self.allocation
        if not a or self.closed:return []
        if b.read_u32(a+0x8C):raise RuntimeError('Le relevé natif des contacts a débordé.')
        end=b.read_u32(a+0x84)
        if type(end) is not int or not self.hit_sequence<=end<=self.hit_sequence+64:
            raise RuntimeError('Journal de contacts incohérent.')
        result=[]
        for sequence in range(self.hit_sequence+1,end+1):
            raw=b.read_memory(a+0x20000+((sequence-1)&63)*256,228)
            if raw is None or len(raw)!=228 or struct.unpack_from('<I',raw)[0]!=sequence:
                raise RuntimeError('Contact natif incomplet.')
            serial,handle,kind,placement,accepted,frame,course,stage=struct.unpack_from('<8I',raw)
            result.append(dict(contact_serial=serial,handle=handle,type=kind,
                placement_id=placement if placement<0x80000000 else placement-0x100000000,
                deposit_result=accepted,frame=frame,room=[course,stage],
                hit_info=raw[0x20:0x68].hex(),attack_desc=raw[0x68:0x80].hex(),candidate_record=raw[0x80:0xC8].hex(),
                current_position=list(struct.unpack_from('<3f',raw,0xC8)),vtable=struct.unpack_from('<I',raw,0xD4)[0],
                current_record_summary=list(struct.unpack_from('<3I',raw,0xD8)),capture_abi=WORLD_ABI))
        self.hit_sequence=end
        if not b.write_u32(a+0x88,end):raise RuntimeError('Accusé de lecture des contacts impossible.')
        return result

    def poll(self):
        if self.pending is None:return None
        raw=self.bridge.read_memory(self.allocation+12,8)
        if raw is None or len(raw)!=8:return None
        sequence,result=struct.unpack('<2I',raw)
        if sequence!=self.pending:return None
        self.pending=None
        if result not in (1,2,3):raise RuntimeError('Réponse native du monde invalide.')
        return result

    def close(self):
        if self.closed:return True
        if self._reserve_uncertain:return False
        if self.items:
            if not self.items.close():return False
            self.items=None
        if self.mirror:
            if not self.mirror.close():return False
            self.mirror=None
        if self.captures:
            if not self.captures.close():return False
            self.captures=None
        if self.foliage:
            if not self.foliage.close():return False
            self.foliage=None
        if self.pose:
            if not self.pose.close():return False
            self.pose=None
        if self.impact:
            if not self.impact.close():return False
            self.impact=None
        if not self.allocation:self.closed=True;return True
        b=self.bridge;a=self.allocation
        if b.attached_pid!=self.context.pid or not self.trial.installed:return False
        if not b.write_u32(a+4,0):return False
        if self.hooked:
            if b.read_memory(CUT_FUNCTION,4) not in (self.jump,CUT_ORIGINAL):return False
            if not b.write_memory(CUT_FUNCTION,CUT_ORIGINAL):return False
        if self.enemy_hooked:
            if b.read_memory(ENEMY_DAMAGE_HOOK,4) not in (self.enemy_jump,ENEMY_DAMAGE_ORIGINAL):return False
            if not b.write_memory(ENEMY_DAMAGE_HOOK,ENEMY_DAMAGE_ORIGINAL):return False
        if self.hit_hooked:
            if b.read_memory(ENEMY_HIT_HOOK,4) not in (self.hit_jump,ENEMY_HIT_ORIGINAL):return False
            if not b.write_memory(ENEMY_HIT_HOOK,ENEMY_HIT_ORIGINAL):return False
        if self.published:
            # A later main-thread callback proves prior source/call invocations
            # returned. No debugger resume and no forced progress while paused.
            frame=b.read_u32(a+0x74);deadline=time.monotonic()+2
            while b.read_u32(a+0x74)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
            if self.pending is not None and self.poll() is None:return False
            if not b.write_u32(WORLD_POINTER,0):return False
            frame=b.read_u32(MAILBOX+0x50)
            while b.read_u32(MAILBOX+0x50)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
        try:
            result,_=self.jobs.call(RenderJob.RELEASE,a,timeout=2)
        except RuntimeError:return False
        if result:return False
        self.closed=True;self.allocation=0;self.hooked=self.published=False
        return True
