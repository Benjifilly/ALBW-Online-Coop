"""Closed EUR held-tool presentation: Bow, nocked Arrow and Hookshot Shooter.

Only final transforms and mesh flags cross the network. Local immutable BCH
resources are selected by a known player wrapper and an equal inventory level;
unavailable variants remain hidden. This creates G3Ds, never game actors,
projectiles, item grants, collision shapes or combat callbacks.
"""
from dataclasses import dataclass
from pathlib import Path
import struct
import threading
import time

from src.memory.equipment_model import resource_name, resource_name_of, read_model, EquipmentUnavailable
from src.memory.native_jobs import RenderJob as Job, NativeJobRejected, PRIVATE_SIZE
from src.memory.remote_model import MAILBOX
from src.memory.render_state import _heap, _number, RenderStateReader
from src.memory.wall_model import PrivateWallModel
from src.memory.world_layout import WORLD_POINTER, reserve

ITEM_REGION=reserve('item_visuals')
ITEM_TABLE=ITEM_REGION+0x800
ITEM_SLOTS=24
HELD_ITEMS=8
ITEM_CONTROL=0xB4
DESCRIPTOR_MAGIC=0x57414C4C
LEASE=60


@dataclass(frozen=True)
class ItemProfile:
    kind: str
    slot: int
    inventory: int
    wrapper_vtable: int
    resource: str
    meshes: int


# EUR player wrappers observed in both independent games. Bow A/B/R archive
# names all expose the BCH model named Bow; they are not Sword-style roots.
ITEM_PROFILES=(
    ItemProfile('bow',4,4,0x6E495C,'Bow',1),
    ItemProfile('nocked_arrow',5,4,0x6E6CA0,'Arrow',1),
    ItemProfile('hookshot',8,11,0x6E8F7C,'Shooter',2),
)
PROFILE_BY_KIND={profile.kind:profile for profile in ITEM_PROFILES}


def validate_item_visuals(value):
    if not isinstance(value,list) or len(value)>HELD_ITEMS:return None
    clean=[];seen=set()
    for item in value:
        if not isinstance(item,dict) or set(item)!={'kind','variant','visible','matrix','scale','meshes'}:return None
        kind=item['kind'];profile=PROFILE_BY_KIND.get(kind) if isinstance(kind,str) else None
        if profile is None or kind in seen:return None
        seen.add(kind)
        variant=item['variant']
        if type(variant) is not int or not 0<=variant<=3 or type(item['visible']) is not bool:return None
        matrix,scale,meshes=item['matrix'],item['scale'],item['meshes']
        if not isinstance(matrix,list) or len(matrix)!=12 or any(
                not _number(v,-1e6 if i%4==3 else -8,1e6 if i%4==3 else 8)
                for i,v in enumerate(matrix)):return None
        if not isinstance(scale,list) or len(scale)!=3 or not all(_number(v,0,8) for v in scale):return None
        if not isinstance(meshes,list) or len(meshes)!=profile.meshes or any(type(v) is not bool for v in meshes):return None
        clean.append(dict(kind=kind,variant=variant,visible=item['visible'],matrix=list(matrix),scale=list(scale),meshes=list(meshes)))
    return clean


def _source(bridge,player,profile):
    """Verified local wrapper/current root. Nothing from this result is wire data."""
    if not _heap(player):return None
    wrapper=bridge.read_u32(player+0x630+profile.slot*4)
    if not _heap(wrapper):return None
    raw=bridge.read_memory(wrapper,12)
    if not raw or len(raw)!=12:return None
    vtable,root,owner=struct.unpack('<3I',raw)
    if vtable!=profile.wrapper_vtable or owner!=player+0x40C or not _heap(root):return None
    if bridge.read_u32(root)!=0x6EBEF0 or resource_name(bridge,root)!=profile.resource:return None
    variant=bridge.read_u32(player+0x434+profile.inventory*4)
    if type(variant) is not int or not 0<=variant<=3:return None
    return wrapper,root,variant


class ItemVisualReader:
    def __init__(self,bridge):
        self.bridge=bridge;self.mesh_reader=RenderStateReader(bridge)

    def read(self,player):
        """The same checks as _source, with every wrapper (+630) and inventory
        variant (+434) in one request and each model in one (25/10)."""
        result=[];b=self.bridge
        block=b.read_memory(player+0x434,0x22C) if _heap(player) else None
        if not block or len(block)!=0x22C:return result
        for profile in ITEM_PROFILES:
            wrapper=struct.unpack_from('<I',block,0x630-0x434+profile.slot*4)[0]
            raw=b.read_memory(wrapper,12) if _heap(wrapper) else None
            if not raw or len(raw)!=12:continue
            vtable,root,owner=struct.unpack('<3I',raw)
            if vtable!=profile.wrapper_vtable or owner!=player+0x40C:continue
            model=read_model(b,root)
            # Hidden tools do not need to inflate every player snapshot.
            if model is None or model[0]!=0x6EBEF0 or model[1]!=1:continue
            if resource_name_of(b,model[2])!=profile.resource:continue
            variant=struct.unpack_from('<I',block,profile.inventory*4)[0]
            if not 0<=variant<=3:continue
            meshes=self.mesh_reader._visible_batch(root)
            if meshes is None or len(meshes)!=profile.meshes:continue
            if b.read_u32(wrapper+4)!=root or b.read_u8(root+0x30)!=1:continue
            item=dict(kind=profile.kind,variant=variant,visible=True,scale=model[3],matrix=model[4],meshes=meshes)
            if validate_item_visuals([item]) is not None:result.append(item)
        return result


class PrivateItemModels:
    """A scene owns every model, private heap, native table and short lease."""
    def __init__(self,world):
        self.world=world;self.bridge=world.bridge;self.pool=None
        self.installed=False;self.closed=False;self.lock=threading.RLock()
        self.controls={};self.published={};self.rows={};self.retiring={}
        self.unavailable=set();self.notes=[];self.sequence=0
        self.bomb_slots={}

    def drain_notes(self):
        notes,self.notes=self.notes,[];return notes

    def start(self):
        w=self.world;b=self.bridge
        if (w.closed or not w.trial.installed or not w.allocation
                or b.read_u32(WORLD_POINTER)!=w.allocation or b.read_u32(w.allocation+4)!=1):
            raise ValueError('Objets tenus : monde natif indisponible.')
        code=Path(__file__).with_name('item_visuals.bin').read_bytes()
        if not code or len(code)>0x800:raise ValueError('Objets tenus : code hors réserve.')
        if not (b.write_memory(w.allocation+ITEM_REGION,code.ljust(0x1000,b'\0'))
                and b.read_memory(w.allocation+ITEM_REGION,len(code))==code):
            raise RuntimeError('Préparation des objets tenus non confirmée.')
        if not b.write_u32(w.allocation+ITEM_CONTROL,w.allocation+ITEM_REGION):
            raise RuntimeError('Activation des objets tenus non confirmée.')
        self.installed=True

    def _prepare(self,pid,item,create):
        key=(pid,item['kind'],item['variant'])
        if self.pool is not None and key in self.pool.models:return self.pool.models[key]['model']
        if not create:return None
        b=self.bridge;profile=PROFILE_BY_KIND[item['kind']]
        source=_source(b,self.world.player,profile)
        if source is None or source[2]!=item['variant']:
            # The active local resource is the only variant established for
            # these wrappers. Never relabel its texture as the remote upgrade.
            if key not in self.unavailable:self.notes.append('Variante d’objet tenue non chargée : '+item['kind'])
            self.unavailable.add(key)
            return None
        self.unavailable.discard(key)
        _,root,_=source;resource=b.read_u32(root+0x50)
        raw=b.read_u32(resource+8) if _heap(resource) else None
        header=b.read_memory(resource+0x4C,16) if _heap(resource) else None
        count=b.read_u32(root+0x5C)
        if (not _heap(raw) or b.read_u8(raw) not in (0,2) or count!=profile.meshes
                or not header or len(header)!=16):
            raise EquipmentUnavailable('Ressource d’objet tenu hors du profil vérifié.')
        sizes=struct.unpack('<4I',header)
        if any(size>0x10000 for size in sizes):raise EquipmentUnavailable('Ressource d’objet tenu trop grande.')
        return self._clone(key,item['kind'],resource,count,sizes)

    def _clone(self,key,label,resource,count,sizes):
        """Clone only a locally resolved resource; never an actor or its callbacks."""
        b=self.bridge
        renderer=b.read_u32(0x70B960)
        used=b.read_u32(renderer+0x5C) if _heap(renderer) else None
        capacity=b.read_u32(renderer+0x60) if _heap(renderer) else None
        if (type(used) is not int or type(capacity) is not int
                or capacity-used<4 or not b.read_u32(renderer+0x68)):
            return None
        if self.pool is None:self.pool=PrivateWallModel(self.world.trial,self.world.player,self.world.context.room)
        pool=self.pool
        if not pool.heap:pool.reserve()
        budget=sum(sizes)+(count if count is not None else 4)*(0xB0+0x14+64)+0x4000
        free,_=pool.jobs.call(Job.FREE_SIZE,pool.heap,heap=pool.heap)
        largest,_=pool.jobs.call(Job.MAX_BLOCK,pool.heap,4,heap=pool.heap)
        if min(free,largest)<budget:raise EquipmentUnavailable('Réserve d’objet tenu insuffisante.')
        descriptor=bytearray(0x40)
        struct.pack_into('<5I',descriptor,0,0x6F5988,0x6C57E0,0x6F5988,0x434DC4,resource)
        struct.pack_into('<I',descriptor,0x18,1)
        descriptor[0x1C:0x1E]=b'\x01\x01';descriptor[0x38:0x3A]=b'\x01\xC4'
        pool._write(pool.allocation+0x200,descriptor);pool.uncertain=True
        try:model,_=pool.jobs.call(Job.CREATE_MODEL,pool.allocation+0x200,heap=pool.heap)
        except NativeJobRejected:
            pool.uncertain=False;raise
        if not pool.heap<=model<pool.allocation+PRIVATE_SIZE or b.read_u32(model+4)!=pool.heap:
            raise RuntimeError('Le modèle d’objet tenu est hors de sa réserve privée.')
        pool.models[key]=dict(model=model);pool.uncertain=False
        if b.read_u32(model)!=0x6EBEF0 or b.read_u32(model+0x16C):
            raise RuntimeError('Callback d’objet tenu inattendu.')
        actual=b.read_u32(model+0x5C)
        if type(actual) is not int or not 1<=actual<=4 or (count is not None and actual!=count):
            raise RuntimeError('Nombre de formes d’objet privé inattendu.')
        pool.models[key]['meshes']=actual
        pool.jobs.call(Job.REGISTER,model,0,0,heap=pool.heap)
        for index in range(actual):pool.jobs.call(Job.SHOW_MESH,model,0,index,heap=pool.heap)
        self.notes.append('Modèle d’objet prêt : '+label)
        return model

    def _bomb_states(self,projectiles):
        from src.memory.bomb_state import validate_bombs
        states=[]
        for pid,value in sorted((projectiles or {}).items()):
            clean=validate_bombs(value)
            if clean is None:continue
            states.extend((pid,item) for item in clean if item['phase']=='fuse')
        active={(pid,item['id']) for pid,item in states}
        self.bomb_slots={key:slot for key,slot in self.bomb_slots.items() if key in active}
        for pid,item in states:
            key=(pid,item['id'])
            if key not in self.bomb_slots:
                free=next((slot for slot in range(ITEM_SLOTS) if slot not in self.bomb_slots.values()),None)
                if free is not None:self.bomb_slots[key]=free
        return [(pid,item) for pid,item in states if (pid,item['id']) in self.bomb_slots]

    def _prepare_bomb(self,pid,item,create):
        # A bounded slot pool reuses G3Ds between throws. Logical bomb IDs must
        # never cause unbounded constructors or a new heap per projectile.
        slot=self.bomb_slots.get((pid,item['id']))
        if slot is None:return None
        key=('bomb',slot,item['kind'],item['variant'])
        if self.pool is not None and key in self.pool.models:return self.pool.models[key]['model']
        if not create:return None
        from src.memory.bomb_state import bomb_model_source
        source=bomb_model_source(self.bridge,item['kind'],item['variant'])
        if source is None:
            if key not in self.unavailable:self.notes.append(f'Modèle de projectile {item["kind"]} non chargé.')
            self.unavailable.add(key);return None
        self.unavailable.discard(key)
        model=self._clone(key,source['name'],source['resource'],None,source['sizes'])
        if model and source.get('animations'):self._animate(key,source['animations'])
        return model

    def _prepare_chain(self,pid,item,index,create):
        """One reusable Chain G3D per link of a hook slot (9 at most)."""
        slot=self.bomb_slots.get((pid,item['id']))
        if slot is None:return None
        key=('chain',slot,index)
        if self.pool is not None and key in self.pool.models:return self.pool.models[key]['model']
        if not create:return None
        from src.memory.bomb_state import chain_model_source
        source=chain_model_source(self.bridge)
        if source is None:
            if key not in self.unavailable:self.notes.append('Modèle de chaîne non chargé.')
            self.unavailable.add(key);return None
        self.unavailable.discard(key)
        return self._clone(key,source['name'],source['resource'],None,source['sizes'])

    def _prewarm(self,held):
        """Build the first shot's models while the tool is only held: 10 G3Ds
        created mid-flight arrived after the hook and stalled the sync thread.
        One model per step, in the first projectile slot (0)."""
        from src.memory.bomb_state import ARROW_KIND,HOOK_KIND,CHAIN_LINKS,bomb_model_source,chain_model_source
        wanted=[]
        if 'hookshot' in held:
            wanted=[(('bomb',0,HOOK_KIND,0),lambda:bomb_model_source(self.bridge,HOOK_KIND,0))]
            wanted+=[(('chain',0,i),lambda:chain_model_source(self.bridge)) for i in range(CHAIN_LINKS)]
        elif 'bow' in held:
            wanted=[(('bomb',0,ARROW_KIND,0),lambda:bomb_model_source(self.bridge,ARROW_KIND,0))]
        for key,source in wanted:
            if self.pool is not None and key in self.pool.models or key in self.unavailable:continue
            found=source()
            if found is None:self.unavailable.add(key)
            else:self._clone(key,found['name'],found['resource'],None,found['sizes'])
            return

    def _projectile_rows(self,pid,item,create):
        """The projectile's own model, then its chain links (flying hook)."""
        rows=[]
        model=self._prepare_bomb(pid,item,create)
        if not model:return rows
        key=('bomb',self.bomb_slots[(pid,item['id'])],item['kind'],item['variant'])
        value=dict(matrix=item['matrix'],scale=item['scale'],meshes=[True]*self.pool.models[key]['meshes'],
                   lean=item.get('lean',0.))
        rows.append((model,value,True,self.pool.models[key].get('animation',0),item.get('blink')))
        for index,link in enumerate(item.get('chain',())):
            model=self._prepare_chain(pid,item,index,create)
            if not model:continue
            count=self.pool.models[('chain',self.bomb_slots[(pid,item['id'])],index)]['meshes']
            rows.append((model,dict(matrix=link[:12],scale=link[12:],meshes=[True]*count),True,0,None))
        return rows

    def _animate(self,key,wrappers):
        """The bomb's own material animation (red fuse blink), as the wall model
        builds its animations: same native constructor, resources and binding."""
        pool=self.pool;animation=pool._alloc(0xA8)
        pool.jobs.call(Job.CREATE_ANIMATION,animation,len(wrappers),0,1,heap=pool.heap)
        for index,wrapper in enumerate(wrappers):
            pool.jobs.call(Job.ADD_RESOURCE,animation,index,wrapper,heap=pool.heap)
        pool.jobs.call(Job.BIND_ANIMATION,animation,0xFFFFFFFF,0xFFFFFFFF,0,heap=pool.heap)
        select=pool._alloc(0x5C);desc=bytearray(0x5C)
        struct.pack_into('<3I',desc,0,1,3,select+0xC)
        struct.pack_into('<I4fI',desc,0xC,0,0,1,-1,1,0xFFFFFFFF)
        pool._write(select,desc)
        pool.jobs.call(Job.SELECT_ANIMATION,animation,select,heap=pool.heap)
        pool.models[key]['animation']=animation

    def set_targets(self,targets,projectiles=None):
        """Prepare known visible resources on the sync thread, without drawing."""
        with self.lock:
            if self.closed:return
            prepared=0
            for pid,state in sorted(targets.items()):
                clean=validate_item_visuals(state)
                if clean is None:continue
                for item in clean:
                    if item['visible'] and self._prepare(pid,item,True):prepared+=1
            for pid,item in self._bomb_states(projectiles):
                if item['visible']:prepared+=len(self._projectile_rows(pid,item,True))
            kinds={item['kind'] for state in targets.values() for item in (validate_item_visuals(state) or ())}
            self._prewarm(kinds)
            # Presentation never needs a native allocation merely to publish
            # its first frame. Prepare both descriptor blocks on the sync side,
            # for every slot: a stable model may land on any free slot.
            if self.pool is not None and prepared:
                for slot in range(ITEM_SLOTS):
                    if slot not in self.controls:self.controls[slot]=self.pool._alloc(0x200)

    def _descriptor(self,slot,model,item,enabled,wall_independent=False,animation=0,phase=None):
        pool=self.pool
        if slot not in self.controls:self.controls[slot]=pool._alloc(0x200)
        first=self.controls[slot];old=self.published.get(slot)
        at=first+0x100 if old==first else first
        control=bytearray(0xC8)
        self.sequence=(self.sequence+1)&0xFFFFFFFF
        struct.pack_into('<5I',control,0,DESCRIPTOR_MAGIC,LEASE,int(enabled),model,animation)
        struct.pack_into('<2I',control,0x1C,self.sequence,at+0xC4)
        struct.pack_into('<12f3f',control,0x24,*item['matrix'],*item['scale'])
        # The shared presenter (692E08) sets this phase, else advances natively.
        struct.pack_into('<I4f',control,0x60,*(phase if animation and phase else (0xFFFFFFFF,0,0,0,0)))
        # Mode1 preserves all native mesh choices and world-space matrices.
        struct.pack_into('<3I',control,0xA0,1,*self.world.context.room)
        mask=sum(int(flag)<<i for i,flag in enumerate(item['meshes']))
        struct.pack_into('<2I',control,0xB0,len(item['meshes']),mask)
        struct.pack_into('<If',control,0xB8,int(wall_independent),item.get('lean',0.))
        pool._write(at,control)
        if not self.bridge.write_u32(self.world.allocation+ITEM_TABLE+slot*4,at):
            raise RuntimeError('Publication d’objet tenu impossible.')
        self.published[slot]=at

    def place(self,targets,create=False,projectiles=None,presenter=False):
        """Publish interpolated pointerless item states on the presentation thread.

        The sync thread is only a fallback: two publishers with samples ~100 ms
        apart made a fresh hook appear and vanish on alternate frames.
        """
        with self.lock:
            if self.closed or not self.installed:return True
            now=time.monotonic()
            if presenter:self.presented_at=now
            elif now-getattr(self,'presented_at',0)<.5:return True
            wanted=[]
            for pid,bomb in self._bomb_states(projectiles):
                if bomb['visible']:wanted+=self._projectile_rows(pid,bomb,create)
            for pid,state in sorted(targets.items()):
                clean=validate_item_visuals(state)
                if clean is None:continue
                for item in clean:
                    if not item['visible']:continue
                    model=self._prepare(pid,item,create)
                    if model:wanted.append((model,item,False,0,None))
            frame=self.bridge.read_u32(self.world.allocation+0x74)
            for slot in list(self.retiring):
                if frame is not None and frame!=self.retiring[slot]:
                    self.retiring.pop(slot);self.rows.pop(slot,None)
            # A model keeps its slot while it stays wanted: a reordered list (a
            # hook fired ahead of the held Shooter) must not retire every slot.
            current={model:slot for slot,model in self.rows.items() if slot not in self.retiring}
            placed={};rest=[]
            for row in wanted:
                slot=current.pop(row[0],None)
                if slot is None:rest.append(row)
                else:placed[slot]=row
            free=[slot for slot in range(ITEM_SLOTS) if slot not in self.rows and slot not in self.retiring]
            placed.update(zip(free,rest))
            for slot in range(ITEM_SLOTS):
                if slot in self.retiring:continue
                new=placed.get(slot);old=self.rows.get(slot)
                if old is not None and (new is None or old!=new[0]):
                    at=self.published.get(slot)
                    if at and not self.bridge.write_u32(at+8,0):return False
                    self.retiring[slot]=frame
                    continue
                if new is not None:
                    if not create and slot not in self.controls:continue
                    self._descriptor(slot,*new[:2],True,*new[2:]);self.rows[slot]=new[0]
            return True

    def hide(self):
        with self.lock:
            return all(self.bridge.write_u32(at+8,0) for at in self.published.values())

    def close(self):
        with self.lock:
            if self.closed:return True
            pool=self.pool
            if pool is not None and (pool.uncertain or pool.jobs.pending):return False
            b=self.bridge;world=self.world.allocation
            if self.installed:
                if not self.hide():return False
                frame=b.read_u32(world+0x74);deadline=time.monotonic()+2
                if type(frame) is not int:return False
                while b.read_u32(world+0x74)==frame:
                    if time.monotonic()>=deadline:return False
                    time.sleep(.01)
                if not b.write_u32(world+ITEM_CONTROL,0):return False
                frame=b.read_u32(MAILBOX+0x50)
                while b.read_u32(MAILBOX+0x50)==frame:
                    if time.monotonic()>=deadline:return False
                    time.sleep(.01)
                self.installed=False
            if pool is not None and pool.allocation:
                # Do not call PrivateWallModel.close(): its wall mailbox belongs
                # to the independent painting renderer, not these tools.
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
            self.closed=True;return True
