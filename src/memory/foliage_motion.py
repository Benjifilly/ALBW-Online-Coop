"""Publish nearby moving companions to the native grass visual response."""
import math
import struct
import time
from pathlib import Path
from src.memory.remote_model import branch,MAILBOX
from src.memory.world_layout import WORLD_POINTER,slot,reserve

HOOK=0x236454
FINGERPRINT=bytes.fromhex('000050e300f020e31800000a34021fe5')
ORIGINAL=FINGERPRINT[:4]
ENTRY=slot('foliage_trampoline')
OFFSET=reserve('foliage')
BUFFERS=(reserve('foliage_buffers'),reserve('foliage_buffers')+0x100)
# MassShrubLight321/6D7C50: intact contact 238DD4, before its attack switch.
SHRUB_HOOK=0x238F28
SHRUB_FINGERPRINT=bytes.fromhex('d000d5e1100050e300f19f373a0000ea')
SHRUB_ORIGINAL=SHRUB_FINGERPRINT[:4]
SHRUB_ENTRY=slot('shrub_motion_trampoline')
SHRUB_OFFSET=reserve('shrub_motion')

class NativeFoliageMotion:
    def __init__(self,world):
        self.world=world;self.bridge=world.bridge;self.hooked=False;self.closed=False
        self.trampoline=False;self.previous={};self.sequence=0;self.buffer=1;self.next_tick=0
        self.shrub_hooked=False;self.shrub_trampoline=False
        self.jump=branch(HOOK,ENTRY);self.shrub_jump=branch(SHRUB_HOOK,SHRUB_ENTRY)

    def start(self):
        b=self.bridge;w=self.world
        if (w.closed or not w.trial.installed or not w.allocation
                or b.read_u32(WORLD_POINTER)!=w.allocation or b.read_u32(w.allocation+4)!=1
                or b.read_memory(HOOK,len(FINGERPRINT))!=FINGERPRINT
                or b.read_memory(ENTRY,8)!=bytes(8)
                or b.read_memory(SHRUB_HOOK,len(SHRUB_FINGERPRINT))!=SHRUB_FINGERPRINT
                or b.read_memory(SHRUB_ENTRY,8)!=bytes(8)):
            raise ValueError('Réaction des herbes incompatible avec le profil vérifié.')
        code=Path(__file__).with_suffix('.bin').read_bytes()
        shrub=Path(__file__).with_name('shrub_motion.bin').read_bytes()
        if len(code)>0x1000 or len(shrub)>0x1000:raise ValueError('Réaction des herbes trop grande.')
        if not (b.write_memory(w.allocation+OFFSET,code)
                and b.read_memory(w.allocation+OFFSET,len(code))==code
                and b.write_memory(w.allocation+SHRUB_OFFSET,shrub)
                and b.read_memory(w.allocation+SHRUB_OFFSET,len(shrub))==shrub
                and b.write_u32(w.allocation+0xA8,0)):
            raise RuntimeError('Préparation du feuillage non confirmée.')
        self.trampoline=True
        entry=struct.pack('<2I',0xE51FF004,w.allocation+OFFSET)
        if not b.write_memory(ENTRY,entry) or b.read_memory(ENTRY,8)!=entry:
            raise RuntimeError('Entrée du feuillage non confirmée.')
        self.hooked=True
        if not b.write_memory(HOOK,self.jump) or b.read_memory(HOOK,4)!=self.jump:
            raise RuntimeError('Installation du feuillage non confirmée.')
        self.shrub_trampoline=True
        entry=struct.pack('<2I',0xE51FF004,w.allocation+SHRUB_OFFSET)
        if not b.write_memory(SHRUB_ENTRY,entry) or b.read_memory(SHRUB_ENTRY,8)!=entry:
            raise RuntimeError('Entrée des buissons non confirmée.')
        self.shrub_hooked=True
        if not b.write_memory(SHRUB_HOOK,self.shrub_jump) or b.read_memory(SHRUB_HOOK,4)!=self.shrub_jump:
            raise RuntimeError('Installation des buissons non confirmée.')

    def publish(self,peers):
        now=time.monotonic()
        if now<self.next_tick:return True
        self.next_tick=now+1/30
        b=self.bridge;w=self.world
        if (self.closed or w.closed or b.read_u32(WORLD_POINTER)!=w.allocation
                or b.read_u32(w.allocation+4)!=1):return False
        frame=b.read_u32(w.allocation+0x74)
        if type(frame) is not int:return False
        rows=[];previous={}
        for peer in peers:
            if (peer.get('in_game') is not True or not peer.get('pose_verified')
                    or (peer.get('course'),peer.get('stage'))!=w.context.room
                    or peer.get('render_state',{}).get('merged',False)
                    or peer.get('health',0)<=0):continue
            xyz=tuple(peer.get(k) for k in ('x','y','z'));yaw=peer.get('yaw')
            if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e6 for v in (*xyz,yaw)):continue
            key=peer.get('player_id');old=self.previous.get(key)
            moved=old is not None and sum((a-c)**2 for a,c in zip(xyz,old[0]))>.000025
            active=now if moved else (old[1] if old else 0)
            previous[key]=(xyz,active)
            if now-active>.12:continue
            rotation=round((yaw%math.tau)/math.tau*(1<<32))&0xFFFFFFFF
            rows.append(struct.pack('<3fI',*xyz,rotation))
        self.previous=previous
        if len(rows)>3:raise ValueError('Trop de compagnons pour le feuillage.')
        self.sequence=(self.sequence+2)&0xFFFFFFFE;self.buffer=1-self.buffer
        at=w.allocation+BUFFERS[self.buffer]
        block=struct.pack('<4I',self.sequence|1,frame,len(rows),0)+b''.join(rows)
        if not (b.write_u32(at,self.sequence|1) and b.write_memory(at,block)
                and b.write_u32(at,self.sequence) and b.write_u32(w.allocation+0xA8,at)):
            raise RuntimeError('Publication du feuillage non confirmée.')
        return True

    def close(self):
        if self.closed:return True
        b=self.bridge;w=self.world
        if not b.write_u32(w.allocation+0xA8,0):return False
        if self.hooked:
            if b.read_memory(HOOK,4) not in (self.jump,ORIGINAL):return False
            if not b.write_memory(HOOK,ORIGINAL) or b.read_memory(HOOK,4)!=ORIGINAL:return False
        self.hooked=False
        if self.shrub_hooked:
            if b.read_memory(SHRUB_HOOK,4) not in (self.shrub_jump,SHRUB_ORIGINAL):return False
            if (not b.write_memory(SHRUB_HOOK,SHRUB_ORIGINAL)
                    or b.read_memory(SHRUB_HOOK,4)!=SHRUB_ORIGINAL):return False
        self.shrub_hooked=False
        if self.trampoline or self.shrub_trampoline:
            frame=b.read_u32(MAILBOX+0x50);deadline=time.monotonic()+2
            if type(frame) is not int:return False
            while b.read_u32(MAILBOX+0x50)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
            # The containing world waits for a later native frame before free.
            for flag,slot,offset in (('trampoline',ENTRY,OFFSET),('shrub_trampoline',SHRUB_ENTRY,SHRUB_OFFSET)):
                if not getattr(self,flag):continue
                entry=struct.pack('<2I',0xE51FF004,w.allocation+offset)
                if b.read_memory(slot,8) not in (entry,bytes(8)):return False
                if not b.write_memory(slot,bytes(8)) or b.read_memory(slot,8)!=bytes(8):return False
                setattr(self,flag,False)
        self.closed=True;return True
