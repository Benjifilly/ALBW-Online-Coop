"""Private visual Sword/Shield resources; no item actors or combat callbacks.

Resources and lifetime were verified in bounded native trials on the EUR build.
The native body contains the sheath and Ravio bracelet, not the held weapons.
"""
import struct
from src.memory.native_jobs import RenderJob as Job
from src.memory.remote_model import MAILBOX
from src.memory.render_state import _heap, _number
from src.memory.wall_model import PrivateWallModel

GEAR=(('sword',0,('SwordA','SwordB','SwordC','SwordD')),
      ('shield',10,('ShieldA','ShieldB')))


class EquipmentUnavailable(RuntimeError):
    """This local save has not loaded the other player's cosmetic resource."""


def validate_equipment(value):
    if not isinstance(value,list) or len(value)!=2:return None
    clean=[]
    for item,(kind,_,names) in zip(value,GEAR):
        if not isinstance(item,dict) or item.get('kind')!=kind:return None
        variant=item.get('variant')
        if type(variant) is not int or not 0<=variant<len(names):return None
        if type(item.get('visible')) is not bool:return None
        matrix,scale=item.get('matrix'),item.get('scale')
        if not isinstance(matrix,list) or len(matrix)!=12 or any(
            not _number(v,-1e6 if i%4==3 else -8,1e6 if i%4==3 else 8) for i,v in enumerate(matrix)):return None
        if not isinstance(scale,list) or len(scale)!=3 or not all(_number(v,0,8) for v in scale):return None
        clean.append(dict(kind=kind,variant=variant,visible=item['visible'],matrix=list(matrix),scale=list(scale)))
    return clean


def resource_name(bridge,root):
    resource=bridge.read_u32(root+0x50) if _heap(root) else 0
    raw=bridge.read_u32(resource+8) if _heap(resource) else 0
    name=bridge.read_u32(raw+0x84) if _heap(raw) else 0
    # Packed BCH strings are byte-aligned, unlike the structures above.
    text=bridge.read_memory(name,16) if type(name) is int and 0x08000000<=name<0x1F000000-16 else None
    if not text:return None
    return text.split(b'\0',1)[0].decode('ascii',errors='replace')


def read_equipment(bridge,player):
    result=[]
    for kind,slot,names in GEAR:
        obj=bridge.read_u32(player+0x630+slot*4)
        root=bridge.read_u32(obj+4) if _heap(obj) else 0
        if not _heap(root) or bridge.read_u32(root)!=0x6EBEF0:return None
        name=resource_name(bridge,root)
        if name not in names:return None
        transform=bridge.read_memory(root+0x74,60)
        mode=bridge.read_u8(root+0x30)
        if not transform or len(transform)!=60 or mode not in (0,1):return None
        result.append(dict(kind=kind,variant=names.index(name),visible=bool(mode),
            scale=list(struct.unpack_from('<3f',transform)),matrix=list(struct.unpack_from('<12f',transform,12))))
    return validate_equipment(result)


class PrivateEquipmentModel:
    def __init__(self,trial,player,room):
        self.pool=PrivateWallModel(trial,player,room)
        self.bridge=trial.bridge;self.player=player;self.room=tuple(room)
        self.active={};self.sequence=0;self.controls={}

    def _prepare(self,kind,variant):
        key=(kind,variant)
        if key in self.pool.models:return self.pool.models[key]['model']
        b=self.bridge
        _,slot,names=next(item for item in GEAR if item[0]==kind)
        wrapper=b.read_u32(self.player+0x630+slot*4)
        if not _heap(wrapper):raise RuntimeError('Équipement local indisponible.')
        candidates=[b.read_u32(wrapper+0x1C+i*4) for i in range(4)]
        source=next((root for root in candidates if resource_name(b,root)==names[variant]),None)
        if not source or b.read_u32(source)!=0x6EBEF0:raise EquipmentUnavailable('Variante d’équipement non chargée.')
        resource=b.read_u32(source+0x50);raw=b.read_u32(resource+8)
        count=b.read_u32(source+0x5C)
        sizes=struct.unpack('<4I',self.pool._read(resource+0x4C,16))
        if b.read_u8(raw) not in (0,2) or count!=1 or any(size>0x10000 for size in sizes):
            raise RuntimeError('Ressource d’équipement hors du profil vérifié.')
        renderer=b.read_u32(0x70B960)
        if not _heap(renderer) or b.read_u32(renderer+0x60)-b.read_u32(renderer+0x5C)<4 or not b.read_u32(renderer+0x68):
            raise RuntimeError('Places de rendu insuffisantes.')
        if not self.pool.heap:self.pool.reserve()
        budget=sum(sizes)+0x4000
        free,_=self.pool.jobs.call(Job.FREE_SIZE,self.pool.heap,heap=self.pool.heap)
        largest,_=self.pool.jobs.call(Job.MAX_BLOCK,self.pool.heap,4,heap=self.pool.heap)
        if min(free,largest)<budget:raise RuntimeError('Réserve d’équipement insuffisante.')
        descriptor=bytearray(0x40)
        struct.pack_into('<5I',descriptor,0,0x6F5988,0x6C57E0,0x6F5988,
                         0x434D98 if kind=='sword' else 0x434DB4,resource)
        struct.pack_into('<I',descriptor,0x18,1)
        descriptor[0x1C:0x1E]=b'\x01\x01';descriptor[0x38:0x3A]=b'\x01\xC4'
        address=self.pool.allocation+0x200
        self.pool._write(address,descriptor)
        self.pool.uncertain=True
        from src.memory.native_jobs import NativeJobRejected
        try:
            model,_=self.pool.jobs.call(Job.CREATE_MODEL,address,heap=self.pool.heap)
        except NativeJobRejected:
            self.pool.uncertain=False
            raise
        if not self.pool.heap<=model<self.pool.allocation+0x200000 or b.read_u32(model+4)!=self.pool.heap:
            raise RuntimeError('Le modèle d’équipement est hors de sa réserve privée.')
        self.pool.models[key]=dict(model=model)
        self.pool.uncertain=False
        self.pool.jobs.call(Job.REGISTER,model,0,0,heap=self.pool.heap)
        if b.read_u32(model+0x16C):raise RuntimeError('Callback d’équipement inattendu.')
        self.pool.jobs.call(Job.SHOW_MESH,model,1,0,heap=self.pool.heap)
        return model

    def prepared(self,state):
        """Every visible item already has its private model (no native job needed)."""
        state=validate_equipment(state)
        return state is not None and all((item['kind'],item['variant']) in self.pool.models
                                         for item in state if item['visible'])

    def publish(self,state):
        state=validate_equipment(state)
        if state is None:raise ValueError('Équipement distant invalide.')
        self.sequence=(self.sequence+1)&0xFFFFFFFF
        for index,item in enumerate(state):
            kind=item['kind']
            if not item['visible']:
                # Leave a disabled descriptor for the game thread to consume.
                # Removing its pointer would strand the previously drawn mesh.
                if kind in self.controls and not self.bridge.write_u32(self.controls[kind]+8,0):
                    raise RuntimeError('Masquage d’équipement impossible.')
                continue
            try:model=self._prepare(kind,item['variant'])
            except EquipmentUnavailable:
                # An unloaded resource is an expected save difference. Disable
                # only this held item, preserving the independent body/wall.
                if kind in self.controls and not self.bridge.write_u32(self.controls[kind]+8,0):
                    raise RuntimeError('Masquage d’équipement indisponible impossible.')
                continue
            if kind in self.active and self.active[kind]!=model:
                self.pool.jobs.call(Job.SHOW_MESH,self.active[kind],0,0,heap=self.pool.heap)
                self.pool.jobs.call(Job.REGISTER,self.active[kind],0,0,heap=self.pool.heap)
            self.active[kind]=model
            control=bytearray(0xB0)
            struct.pack_into('<4I',control,0,0x57414C4C,60,1,model)
            struct.pack_into('<I',control,0x1C,self.sequence)
            struct.pack_into('<I',control,0x20,self.pool.allocation+0x900+index*4)
            struct.pack_into('<12f3f',control,0x24,*item['matrix'],*item['scale'])
            struct.pack_into('<2I',control,0xA4,*self.room)
            # Publish a complete alternate block instead of overwriting one
            # while the native frame reads its transform or visibility.
            first=self.pool.allocation+0x400+index*0x100
            address=first+0x200 if self.controls.get(kind)==first else first
            self.pool._write(address,control)
            if not self.bridge.write_u32(MAILBOX+0x15C+index*4,address):raise RuntimeError('Publication d’équipement impossible.')
            self.controls[kind]=address

    def close(self):
        if self.pool.uncertain or self.pool.jobs.pending:return False
        for offset in (0x15C,0x160):
            if not self.bridge.write_u32(MAILBOX+offset,0):return False
        return self.pool.close()

    def hide(self):
        if not self.pool.allocation:return True
        ok=True
        for address in self.controls.values():
            ok=self.bridge.write_u32(address+8,0) and ok
        return ok

    def keep_visible(self):
        # Renew, without enabling a hidden weapon or creating a new variant.
        return all(self.bridge.write_u32(address+4,60) for address in self.controls.values())
