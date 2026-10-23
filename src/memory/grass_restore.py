"""Room-independent native terminal foliage state, applied before actor draw.

The reservation belongs to the renderer lifetime, not a world scene. Loader
hooks remain valid while the local hero scrolls and the world journal detaches.
"""
from pathlib import Path
import struct
import time
from src.memory.native_jobs import NativeRenderJobs,RenderJob,NativeJobRejected
from src.memory.remote_model import MAILBOX,branch
from src.memory.world_layout import slot

POINTER=slot('restore_pointer')
ENTRIES=tuple(slot('restore_trampolines')+8*i for i in range(3))
BUFFERS=(0x10000,0x80000)
MAGIC=0x47525331
MAX_CUTS=8192
# Generated from and checked against the local EUR executable by the builder.
HOOKS={0x4D4608:bytes.fromhex('f88fbde8'),0x50665C:bytes.fromhex('f08fbde8'),
       0x2356D4:bytes.fromhex('10402de9')}

class NativeGrassRestore:
    def __init__(self,trial):
        self.trial=trial;self.bridge=trial.bridge;self.jobs=NativeRenderJobs(trial)
        self.allocation=0;self.closed=False;self.uncertain=False
        self.hooked=[];self.published=False;self.buffer=1;self.sequence=0;self.last=None

    def start(self):
        b=self.bridge
        if (b.read_u32(POINTER) or any(b.read_memory(at,4)!=raw for at,raw in HOOKS.items())
                or any(b.read_memory(at,8)!=bytes(8) for at in ENTRIES)):
            raise ValueError('Le chargement des herbes ne correspond pas à la version vérifiée.')
        try:result,address=self.jobs.call(RenderJob.RESERVE,timeout=3)
        except NativeJobRejected:raise
        except RuntimeError:self.uncertain=True;raise
        if result or not 0x14000000<=address<=0x1BE00000:
            raise RuntimeError('Réserve des herbes indisponible.')
        self.allocation=address
        code=Path(__file__).with_suffix('.bin').read_bytes()
        if not (b.write_memory(address,bytes(0x100)) and b.write_memory(address+0x1000,code)
                and b.read_memory(address+0x1000,len(code))==code
                and b.write_memory(address,struct.pack('<3I',MAGIC,1,120))):
            raise RuntimeError('Préparation de la restauration des herbes impossible.')
        self.published=True
        if not b.write_u32(POINTER,address):raise RuntimeError('Publication des herbes incertaine.')
        for (hook,original),entry,offset in zip(HOOKS.items(),ENTRIES,(4,8,16)):
            trampoline=struct.pack('<2I',0xE51FF004,address+0x1000+offset)
            if not b.write_memory(entry,trampoline) or b.read_memory(entry,8)!=trampoline:
                raise RuntimeError('Entrée de chargement des herbes non confirmée.')
            self.hooked.append((hook,entry,original))
            jump=branch(hook,entry)
            if not b.write_memory(hook,jump) or b.read_memory(hook,4)!=jump:
                raise RuntimeError('Installation des herbes non confirmée.')

    def publish(self,manifest):
        if self.closed or not self.allocation or self.uncertain:return False
        from src.mass_families import family,grass_profile
        rows=[]
        for room,target in sorted(set((tuple(r),tuple(t)) for r,t in manifest)):
            # Families without a loader-time route (shrubs) share only the journal.
            if len(target)==9 and family(target[0]) and not family(target[0]).loader_restore:continue
            if (len(room)!=2 or len(target)!=9 or grass_profile(target[0]) is None
                    or not 0<=room[0]<=23 or not 0<=room[1]<=65535
                    or not 1<=target[1]<=32767 or not 0<=target[5]<=4095):
                raise ValueError('Identité de restauration des herbes invalide.')
            rows.append(struct.pack('<4I3fI3fI',*room,*target[:2],
                        *(v/10000 for v in target[2:5]),target[5],
                        *(v/10000 for v in target[6:]),0))
        if len(rows)>MAX_CUTS:raise RuntimeError('Trop d’herbes conservées dans les salles actives.')
        block=b''.join(rows);b=self.bridge;a=self.allocation
        if block!=self.last:
            self.sequence=(self.sequence+2)&0xFFFFFFFE;self.buffer=1-self.buffer
            at=a+BUFFERS[self.buffer]
            if not (b.write_u32(at,self.sequence|1)
                    and b.write_memory(at+4,struct.pack('<3I',len(rows),0,0)+block)
                    and b.write_u32(at,self.sequence) and b.write_u32(a+0x10,at)):
                raise RuntimeError('Publication des états d’herbe non confirmée.')
            self.last=block
        return b.write_u32(a+8,120)

    def close(self):
        if self.closed:return True
        if self.uncertain:return False
        b=self.bridge;a=self.allocation
        if not a:self.closed=True;return True
        if not self.trial.installed or b.attached_pid!=self.trial.pid:return False
        if not b.write_u32(a+4,0):return False
        for hook,entry,raw in self.hooked:
            if b.read_memory(hook,4) not in (branch(hook,entry),raw):return False
            if not b.write_memory(hook,raw) or b.read_memory(hook,4)!=raw:return False
        # Later game-thread progress proves init/drop callbacks have returned.
        frame=b.read_u32(MAILBOX+0x50);deadline=time.monotonic()+2
        while b.read_u32(MAILBOX+0x50)==frame:
            if time.monotonic()>=deadline:return False
            time.sleep(.01)
        if self.published and not b.write_u32(POINTER,0):return False
        for entry in ENTRIES:
            if not b.write_memory(entry,bytes(8)):return False
        # The frame dispatcher may already have fetched the service pointer.
        frame=b.read_u32(MAILBOX+0x50)
        while b.read_u32(MAILBOX+0x50)==frame:
            if time.monotonic()>=deadline:return False
            time.sleep(.01)
        try:result,_=self.jobs.call(RenderJob.RELEASE,a,timeout=2)
        except RuntimeError:return False
        if result:return False
        self.closed=True;self.allocation=0;return True
