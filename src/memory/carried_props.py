"""Carried pots, bushes and rocks seen by the other players.

The lifter's game keeps its own carried actor. CARRY_SITES bind that actor to
the Mass instance it came from (CARRIED_TABLE). Each tick the lifter publishes
{target, pos} in its render state. The other games, where the journal already
removed (pot, rock) or cut (bush: stump) that instance, show a private model
built from the same loaded resource (GrassLight, PotLight, RockSLight...) at
that position: carried above the head, then thrown. carried_mirror.arm hands
the lease-guarded descriptors to the renderer's present_private_model once per
frame; the game's own Mass models are never touched.
"""
import math
import itertools
import struct
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from src.mass_families import family,grass_profile
from src.memory.capture_hooks import CARRIED_TABLE
from src.memory.native_jobs import RenderJob as Job,NativeJobRejected
from src.memory.remote_model import MAILBOX
from src.memory.render_state import _heap
from src.memory.world_layout import WORLD_POINTER,reserve

MIRROR=reserve('carried_mirror')
MIRROR_TABLE=MIRROR+0x800      # 4 private model descriptors, 0x100 each
MIRROR_SLOTS=4
# Break replays (carried_mirror.arm): 2 x 0x30 (request, batch handle, index,
# xyz, then native restore state); the lottery suppression read by
# shared_loot.arm (batch handle, index).
BREAK_TABLE=MIRROR+0x700
BREAK_SLOT=0x30
SUPPRESS_SLOT=MIRROR+0x7F0
# Families whose thrown copy can replay a break here: pots, rocks, bushes.
BREAKABLE=frozenset({327,328,323,324,325,326,316,317,319,320})
DESCRIPTOR_MAGIC=0x57414C4C    # present_private_model ('WALL')
LEASE=60
# Mesh shown above the head: grass/lawn models hold the stump (0) and the
# bush (1); pots and rocks have a single mesh.
def carried_mesh(kind):return 1 if grass_profile(kind) else 0
# ObjPotLight/ObjPotSkull mark their break before the lottery (4AFA50...).
POT_BROKEN=0x56C
# Receiver-local event identifiers, never sent over the network. A new room
# must not reuse an identifier still remembered by the particle presenter.
_BREAK_SERIALS=itertools.count(1)


def validate_carried(value):
    """{target, pos} while carried or thrown; broken=True once it broke there."""
    if not isinstance(value,dict) or set(value)-{'broken'}!={'target','pos'}:return None
    if 'broken' in value and value['broken'] is not True:return None
    target,pos=value['target'],value['pos']
    from src.network.world_journal import event_payload
    if event_payload(dict(kind='mass_cut',target=target,effect=0)) is None:return None
    if (not isinstance(pos,list) or len(pos)!=3
            or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in pos)):return None
    clean=dict(target=list(target),pos=[float(v) for v in pos])
    if value.get('broken'):clean['broken']=True
    return clean


def read_local_carried(bridge,world,instances,resolve,bindings=None):
    """The newest live carried actor of this game, as {target, pos}, or None.

    resolve(handle) -> actor address or None (handle table of the scene).
    bindings {actor handle: (serial, target)} outlives the scene: the native
    carried table is rebuilt with each world, while Link keeps carrying his
    actor through a room change.
    """
    raw=bridge.read_memory(world+CARRIED_TABLE+0x10,8*16)
    if not isinstance(raw,(bytes,bytearray)) or len(raw)!=8*16:return None
    by_batch={(v.handle,v.index):v for v in instances.values() if len(v.target)==9}
    bindings=bindings if bindings is not None else {}
    for actor,batch,index,serial in struct.iter_unpack('<4I',raw):
        instance=by_batch.get((batch,index)) if actor else None
        if instance is not None:bindings[actor]=(serial,tuple(instance.target))
    best=None
    for actor,(serial,target) in list(bindings.items()):
        if best and serial<=best[0]:continue
        address=resolve(actor)
        if address is None:
            del bindings[actor];continue
        instance=SimpleNamespace(target=target)
        header=bridge.read_memory(address+0x2CC,12)
        if not isinstance(header,(bytes,bytearray)) or len(header)!=12 or struct.unpack_from('<I',header)[0]!=actor or header[0xB]:continue
        broken=instance.target[0] in (327,328) and bool(bridge.read_u8(address+POT_BROKEN))
        pos=bridge.read_memory(address+0x3D0,12)
        if not isinstance(pos,(bytes,bytearray)) or len(pos)!=12:continue
        xyz=list(struct.unpack('<3f',pos))
        if not all(math.isfinite(v) and abs(v)<=1e6 for v in xyz):continue
        best=(serial,dict(target=list(instance.target),pos=xyz))
        if broken:best[1]['broken']=True
    return best[1] if best else None


class CarriedMirror:
    """Private models of the objects other players carry, per world scene."""
    def __init__(self,world):
        self.world=world;self.bridge=world.bridge;self.installed=False;self.closed=False
        self.pool=None;self.models={};self.rows={};self.unavailable=set();self.notes=[]
        self.targets={}
        self.effects=[]
        # Placement runs on the presentation thread; scene changes on the sync thread.
        self.lock=threading.RLock()

    def drain_notes(self):
        notes,self.notes=self.notes,[];return notes

    def drain_effects(self):
        """New, fixed-position bush bursts for the private particle presenter.

        The presenter keeps them until their native particle instance retires.
        No arbitrary frame delay or temporary binding to the stump is needed.
        """
        with self.lock:
            effects,self.effects=self.effects,[]
            return effects

    def _refuse(self,resource,why):
        self.unavailable.add(resource);self.notes.append('Objet porté non affichable : '+why)
        return None

    def start(self):
        b=self.bridge;w=self.world
        if (w.closed or not w.trial.installed or not w.allocation
                or b.read_u32(WORLD_POINTER)!=w.allocation or b.read_u32(w.allocation+4)!=1):
            raise ValueError('Objets portés : monde natif indisponible.')
        code=Path(__file__).with_name('carried_mirror.bin').read_bytes()
        if len(code)>0x700:raise ValueError('Objets portés : code trop grand.')
        if not (b.write_memory(w.allocation+MIRROR,code.ljust(0x1000,b'\0'))
                and b.read_memory(w.allocation+MIRROR,len(code))==code):
            raise RuntimeError('Préparation des objets portés non confirmée.')
        self.installed=True
        if not b.write_u32(w.allocation+0xB0,w.allocation+MIRROR):
            raise RuntimeError('Activation des objets portés non confirmée.')

    def _ready(self,instance):
        """The already created model for this instance's resource, or None."""
        shape=family(instance.target[0]);b=self.bridge
        source=b.read_u32(instance.records+instance.index*shape.record_size+8)
        resource=b.read_u32(source+0x50) if _heap(source) else None
        return self.models.get(resource)

    def _model(self,instance):
        """One private model per loaded resource (GrassLight, PotLight...)."""
        b=self.bridge;shape=family(instance.target[0])
        source=b.read_u32(instance.records+instance.index*shape.record_size+8)
        resource=b.read_u32(source+0x50) if _heap(source) else None
        if not _heap(resource) or b.read_u32(source)!=0x6DBB44:return None
        if resource in self.models:return self.models[resource]
        if resource in self.unavailable:return None
        from src.memory.wall_model import PrivateWallModel
        if self.pool is None:
            self.pool=PrivateWallModel(self.world.trial,self.world.player,self.world.context.room)
        pool=self.pool
        raw=b.read_u32(resource+8);count=b.read_u32(source+0x5C)
        header=b.read_memory(resource+0x4C,16)
        mesh=carried_mesh(instance.target[0])
        if (not _heap(raw) or b.read_u8(raw) not in (0,2) or type(count) is not int or not mesh<count<=4
                or not header or len(header)!=16):
            return self._refuse(resource,'ressource hors profil.')
        sizes=struct.unpack('<4I',header)
        if any(size>0x10000 for size in sizes):return self._refuse(resource,'ressource trop grande.')
        renderer=b.read_u32(0x70B960)
        if not _heap(renderer) or b.read_u32(renderer+0x60)-b.read_u32(renderer+0x5C)<4 or not b.read_u32(renderer+0x68):
            return None
        if not pool.heap:pool.reserve()
        budget=sum(sizes)+count*(0xB0+0x14+64)+0x3000
        free,_=pool.jobs.call(Job.FREE_SIZE,pool.heap,heap=pool.heap)
        largest,_=pool.jobs.call(Job.MAX_BLOCK,pool.heap,4,heap=pool.heap)
        if min(free,largest)<budget:return self._refuse(resource,'réserve privée insuffisante.')
        descriptor=bytearray(0x40)
        # Same constructor descriptor as the companion's held items; the name
        # field is only a label ("PlayerItem").
        struct.pack_into('<5I',descriptor,0,0x6F5988,0x6C57E0,0x6F5988,0x434DC4,resource)
        struct.pack_into('<I',descriptor,0x18,1)
        descriptor[0x1C:0x1E]=b'\x01\x01';descriptor[0x38:0x3A]=b'\x01\xC4'
        pool._write(pool.allocation+0x200,descriptor)
        pool.uncertain=True
        try:model,_=pool.jobs.call(Job.CREATE_MODEL,pool.allocation+0x200,heap=pool.heap)
        except NativeJobRejected:
            pool.uncertain=False;raise
        if not pool.heap<=model<pool.allocation+0x200000 or b.read_u32(model+4)!=pool.heap:
            raise RuntimeError('Le modèle porté est hors de sa réserve privée.')
        pool.models[resource]=dict(model=model);pool.uncertain=False
        for index in range(count):
            pool.jobs.call(Job.SHOW_MESH,model,int(index==mesh),index,heap=pool.heap)
        self.models[resource]=model
        self.notes.append(f'Modèle d’objet porté prêt ({instance.target[0]}).')
        return model

    def _descriptor(self,slot,model,xyz,room,enabled):
        at=self.world.allocation+MIRROR_TABLE+slot*0x100
        data=bytearray(0xC4)
        struct.pack_into('<4I',data,0,DESCRIPTOR_MAGIC,LEASE,int(enabled),model)
        struct.pack_into('<I',data,0x20,at+0xC0)
        struct.pack_into('<12f3f',data,0x24,1,0,0,xyz[0],0,1,0,xyz[1],0,0,1,xyz[2],1,1,1)
        # Mode 1: world-space model, no automatic mesh-0 toggle (bush stump).
        struct.pack_into('<3I',data,0xA0,1,*room)
        return at,bytes(data)

    def set_targets(self,targets):
        """{player_id: MassInstance} carried by other players; prepares their models.

        Sync thread only: model creation submits native render jobs.
        """
        with self.lock:
            if self.closed:return
            self.targets=dict(targets)
            for instance in self.targets.values():self._model(instance)

    def request_break(self,instance,xyz):
        """Replay a thrown object's native break where it broke (sound, effects).

        Pots and rocks use the checked native routine, with their lottery
        suppressed because shared loot already decides. Bushes use a private
        Break particle at the landing point; their original stump is untouched.
        """
        with self.lock:
            if self.closed or not self.installed or instance.target[0] not in BREAKABLE:return False
            if (not isinstance(xyz,(list,tuple)) or len(xyz)!=3
                    or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in xyz)):
                return False
            if grass_profile(instance.target[0]):
                # 2C42AC -> ac_Break contains one immediate EFLK effect Break.
                # Its native bank cached MODEL+B0 at init, so moving a placement
                # cannot move the burst. Use the same local bank resource in the
                # existing private particle pool; never touch stump/entry/bank.
                if len(self.effects)>=16:return False
                serial=next(_BREAK_SERIALS)
                if serial>=0x80000000:return False
                x,y,z=map(float,xyz)
                self.effects.append(dict(id=0x80000000|serial,
                    bank=3 if instance.target[0] in (316,317) else 4,name='Break',selectors=[0,0],
                    matrix=[1,0,0,x,0,1,0,y,0,0,1,z]))
                return True
            at=self.world.allocation+BREAK_TABLE
            for slot in range(2):
                if self.bridge.read_u32(at+slot*BREAK_SLOT)==0:
                    data=struct.pack('<2I3f',instance.handle,instance.index,*xyz)
                    return (self.bridge.write_memory(at+slot*BREAK_SLOT+4,data)
                            and self.bridge.write_u32(at+slot*BREAK_SLOT,1))
            return False

    def place(self,positions,create=True):
        """Present each target at its carrier's interpolated position (player_id -> xyz)."""
        with self.lock:
            # Not yet started (new scene) or already closed: nothing to place.
            if self.closed or not self.installed:return True
            shown=[(instance,positions[pid]) for pid,instance in sorted(self.targets.items()) if pid in positions]
            return self.publish(shown,create)

    def publish(self,shown,create=True):
        """shown: up to MIRROR_SLOTS (MassInstance, (x, y, z)) carried by others.

        Enabled descriptors are rewritten every call, which renews their lease.
        A slot that stops, or changes model, is first disabled for a frame so
        the renderer unregisters the model it was presenting.
        """
        if self.closed or not self.installed:return False
        room=self.world.context.room;wanted={}
        for slot,(instance,xyz) in enumerate(shown[:MIRROR_SLOTS]):
            model=self._model(instance) if create else self._ready(instance)
            if model:wanted[slot]=(model,tuple(xyz))
        rows={}
        for slot in range(MIRROR_SLOTS):
            new=wanted.get(slot);old=self.rows.get(slot)
            if old and old[1] is not None and (new is None or new[0]!=old[0]):
                new=(old[0],None)
            elif new is None:continue
            model,xyz=new
            at,data=self._descriptor(slot,model,xyz or (0,0,0),room,xyz is not None)
            if not self.bridge.write_memory(at,data):return False
            if xyz is not None:rows[slot]=new
        self.rows=rows
        return True

    def close(self):
        with self.lock:return self._close()

    def _close(self):
        if self.closed:return True
        self.effects=[]
        b=self.bridge;a=self.world.allocation
        if self.installed:
            # Disable every descriptor; a later world frame unregisters them.
            for slot in range(MIRROR_SLOTS):
                if not b.write_u32(a+MIRROR_TABLE+slot*0x100+8,0):return False
            frame=b.read_u32(a+0x74);deadline=time.monotonic()+2
            if type(frame) is not int:return False
            while b.read_u32(a+0x74)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
            if not b.write_u32(a+0xB0,0):return False
            frame=b.read_u32(MAILBOX+0x50)
            while b.read_u32(MAILBOX+0x50)==frame:
                if time.monotonic()>=deadline:return False
                time.sleep(.01)
            self.installed=False
        if self.pool is not None:
            pool=self.pool
            if pool.uncertain or pool.jobs.pending:return False
            if pool.allocation:
                for key,item in list(pool.models.items()):
                    pool.jobs.call(Job.REGISTER,item['model'],0,0,heap=pool.heap)
                    pool.uncertain=True
                    pool.jobs.call(Job.DESTROY_MODEL,item['model'],0,heap=pool.heap)
                    del pool.models[key];pool.uncertain=False
                if pool.heap:
                    pool.jobs.call(Job.DESTROY_HEAP,pool.heap,heap=pool.heap);pool.heap=0
                rc,_=pool.jobs.call(Job.RELEASE,pool.allocation)
                if rc:return False
                pool.allocation=0
            self.pool=None;self.models={}
        self.closed=True;return True
