"""Private native Fresco projection. Network values never select native pointers."""
import math
import struct
from src.memory.native_jobs import NativeJobRejected, RenderJob as Job, PRIVATE_SIZE
from src.memory.render_state import _heap, _number
from src.memory.remote_model import MAILBOX

MAILBOX_PENDING_SURFACE=MAILBOX+0x1F8

FRESCO_VTABLE=0x6DBB00
RTT_VTABLE=0x6DE858
MAX_TRIANGLES=90
IDENTITY_3X4=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.]

def _matrix12(values):
    return (isinstance(values,(list,tuple)) and len(values)==12 and all(
        _number(v,-1e6 if i%4==3 else -8,1e6 if i%4==3 else 8)
        for i,v in enumerate(values)))

def validate_fresco_surface(data):
    if not isinstance(data,dict):return None
    count=data.get('triangle_count')
    if type(count) is not int or not 1<=count<=90:return None
    for key,stride,bound in (('positions',9,1e6),('vectors',9,1.01),('uv',6,4.)):
        values=data.get(key)
        if (not isinstance(values,(list,tuple)) or len(values)!=count*stride
                or not all(_number(v,-bound,bound) for v in values)):return None
    vectors=data['vectors']
    if any(not .8<=sum(v*v for v in vectors[i:i+3])<=1.2 for i in range(0,len(vectors),3)):return None
    matrix=data.get('matrix',IDENTITY_3X4);projection=data.get('projection_matrix',IDENTITY_3X4)
    if not _matrix12(matrix) or not _matrix12(projection):return None
    clean=dict(triangle_count=count,positions=list(data['positions']),vectors=list(vectors),
               uv=list(data['uv']),matrix=list(matrix),projection_matrix=list(projection))
    if 'uv_transform' in data:
        transform=data['uv_transform']
        if not isinstance(transform,(list,tuple)) or list(transform) not in ([1.,0.],[-1.,1.]):return None
        if any(type(v) not in (int,float) for v in transform):return None
        clean['uv_transform']=list(transform)
    return clean

def make_test_quad(wall_x=9.,y_bottom=3.,y_top=5.,z_start=46.8,z_end=48.,normal=(-1.,0.,0.)):
    """Offline fixture only; never substitute synthetic geometry in a real room."""
    return dict(triangle_count=2,
        positions=[wall_x,y_bottom,z_start,wall_x,y_top,z_start,wall_x,y_top,z_end,
                   wall_x,y_bottom,z_start,wall_x,y_top,z_end,wall_x,y_bottom,z_end],
        vectors=list(normal)*6,uv=[0.,0.,0.,1.,1.,1.,0.,0.,1.,1.,1.,0.],
        matrix=list(IDENTITY_3X4),projection_matrix=list(IDENTITY_3X4))

class PrivateFrescoModel:
    def __init__(self,trial,player,room,allocation,heap,jobs):
        self.trial=trial;self.bridge=trial.bridge;self.player=player;self.room=tuple(room)
        self.allocation=allocation;self.heap=heap;self.jobs=jobs
        self.model=self.wrapper=self.rtt=self.flat=0;self.rtt_initialized=False
        self.cmd_addr_ptr=self.cmd_size_ptr=0
        self.active_slot=0;self.slot_buffers=[]
        self.uncertain=False;self.prepared=False;self.visible=False;self.view_correction=False

    def _inside(self,pointer,size):
        return type(pointer) is int and self.heap+0xA8<=pointer<=self.allocation+PRIVATE_SIZE-size

    def _read(self,pointer,size):
        data=self.bridge.read_memory(pointer,size)
        if data is None or len(data)!=size:raise RuntimeError('Lecture Fresco incomplète.')
        return data

    def _write(self,pointer,data):
        if not self.allocation<=pointer<=self.allocation+PRIVATE_SIZE-len(data):
            raise ValueError('Écriture Fresco hors de sa réserve privée.')
        if not self.bridge.write_memory(pointer,data):raise RuntimeError('Écriture Fresco impossible.')

    def _native(self,job,*args,**kwargs):
        self.uncertain=True
        try:result=self.jobs.call(job,*args,heap=self.heap,**kwargs)
        except NativeJobRejected:
            self.uncertain=False
            raise
        self.uncertain=False
        return result

    def _alloc(self,size,alignment=4):
        base,_=self._native(Job.ALLOCATE,size+alignment-1)
        if not self._inside(base,size+alignment-1):raise RuntimeError('Allocation Fresco non privée.')
        return (base+alignment-1)&-alignment

    def source(self):
        b=self.bridge
        controller=b.read_u32(self.player+0x604)
        wall=b.read_u32(controller+0x28) if _heap(controller) else 0
        native=b.read_u32(wall+8) if _heap(wall) else 0
        if not _heap(native) or b.read_u32(native)!=FRESCO_VTABLE:
            raise RuntimeError('Ressource FrescoBase native non chargée.')
        resource=b.read_u32(native+0x50)
        raw=b.read_u32(resource+8) if _heap(resource) else 0
        if not _heap(raw):raise RuntimeError('Ressource FrescoBase inaccessible.')
        count=struct.unpack('<H',self._read(raw+0x38,2))[0];table=b.read_u32(raw+0x34)
        if not 1<=count<=8 or not _heap(table):raise RuntimeError('Matériaux Fresco non reconnus.')
        # 2C1270 writes shared material+C4 only when zero, including -0.0.
        # Require the local game's initialization, never perform that mutation.
        for i in range(count):
            material=b.read_u32(table+i*0x2C)
            value=struct.unpack('<f',self._read(material+0xC4,4))[0] if _heap(material) else 0.
            if not math.isfinite(value) or value==0.:
                raise RuntimeError('Initialisation partagée de FrescoBase encore nécessaire.')
        return dict(native_fresco=native,resource=resource,priority=self._read(native+0x16A,1)[0])

    def prepare(self,source_info=None):
        if self.prepared:return
        if self.model or self.uncertain:raise RuntimeError('Création Fresco précédente incomplète.')
        source=self.source();b=self.bridge # Re-read trusted local pointers.
        free,_=self._native(Job.FREE_SIZE,self.heap)
        largest,_=self._native(Job.MAX_BLOCK,self.heap,4)
        if min(free,largest)<0x40000:raise RuntimeError('Réserve Fresco insuffisante.')
        descriptor=bytearray(0x40)
        struct.pack_into('<5I',descriptor,0,0x6F5988,0x6C57E0,0x6F5988,0x6C57E0,source['resource'])
        struct.pack_into('<I',descriptor,0x18,1)
        descriptor[0x1C:0x1E]=b'\x02\x01'
        descriptor[0x38:0x3A]=bytes((1,source['priority']))
        self._write(self.allocation+0x400,descriptor)
        model,_=self._native(Job.CREATE_MODEL,self.allocation+0x400)
        if not self._inside(model,0x220):
            self.uncertain=True
            raise RuntimeError('FrescoBase créé hors de son heap privé.')
        self.model=model
        if b.read_u32(model)!=FRESCO_VTABLE or b.read_u32(model+4)!=self.heap:
            self.uncertain=True
            raise RuntimeError('Propriétaire de FrescoBase non reconnu.')
        self.wrapper=b.read_u32(model+0x178)
        if not self._inside(self.wrapper,0x24):raise RuntimeError('Descripteur Fresco non privé.')
        if not self._inside(b.read_u32(model+0x1D4),0x21C):raise RuntimeError('Indices Fresco non privés.')
        for index in (0,1):
            address=model+0x17C+index*0x28
            cap_pos,pos,cap_uv,uv,cap_vec,vec,patch_a,patch_b,cmd,words=struct.unpack('<10I',self._read(address,40))
            if ((cap_pos,cap_uv,cap_vec)!=(90,90,90) or not 1<=words<=4096
                    or not all(self._inside(at,size) for at,size in
                        ((pos,90*36),(uv,90*24),(vec,90*36),(cmd,words*4)))
                    or not all(cmd<=patch<=cmd+words*4-4 for patch in (patch_a,patch_b))):
                raise RuntimeError('Buffers Fresco non privés.')
            self.slot_buffers.append(dict(slot_addr=address,pos_ptr=pos,uv_ptr=uv,vec_ptr=vec))
        self.active_slot=b.read_u32(model+0x1CC)
        if self.active_slot not in (0,1):raise RuntimeError('Slot Fresco non reconnu.')
        self._isolate_texture_commands()
        flags=b.read_u32(model+0x164)
        self._write(model+0x164,struct.pack('<I',flags&~1))
        self._write(model+0x134,struct.pack('<f',0.))
        self._prepare_rtt()
        self.prepared=True

    def _isolate_texture_commands(self):
        b=self.bridge;model=self.model;wrapper=b.read_u32(model+0x20)
        if not self._inside(wrapper,8):raise RuntimeError('Matériaux Fresco non privés.')
        table,count=struct.unpack('<2I',self._read(wrapper,8))
        if not 1<=count<=8 or not self._inside(table,count*0x154):
            raise RuntimeError('Table de matériaux Fresco non privée.')
        addr=b.read_u32(model+0x1E8);dim=b.read_u32(model+0x1EC);bindings=[]
        for i in range(count):
            field=table+i*0x154+0x13C
            commands,words=struct.unpack('<2I',self._read(field,8))
            if type(words) is int and 2<=words<=1024 and _heap(commands):
                if all(type(at) is int and commands<=at<=commands+words*4-4 for at in (addr,dim)):
                    bindings.append((field,commands,words))
        if len(bindings)!=1:raise RuntimeError('Commandes Texture0 Fresco non reconnues.')
        field,commands,words=bindings[0];data=self._read(commands,words*4)
        for at,register in ((addr,0x85),(dim,0x82)):
            offset=at-commands
            if (offset%4 or offset+8>len(data)
                    or struct.unpack_from('<I',data,offset+4)[0]!=(0xF0000|register)):
                raise RuntimeError('Registres Texture0 Fresco inattendus.')
        private=self._alloc(len(data),16);self._write(private,data)
        self.cmd_addr_ptr=private+addr-commands;self.cmd_size_ptr=private+dim-commands
        self._write(field,struct.pack('<I',private)) # The actual renderer consumes this pointer.
        self._write(model+0x1E8,struct.pack('<2I',self.cmd_addr_ptr,self.cmd_size_ptr))

    def _prepare_rtt(self):
        b=self.bridge;self.rtt=self._alloc(0x18C)
        self._native(Job.CREATE_RTT,self.rtt)
        self.rtt_initialized=True
        if b.read_u32(self.rtt)!=RTT_VTABLE or b.read_u32(self.rtt+4)!=self.heap:
            self.uncertain=True
            raise RuntimeError('Propriétaire RTT non reconnu.')
        depth=self._alloc(128*128*2,128)
        self._native(Job.CONFIGURE_RTT,self.rtt,128,128,2,stack=(1,self.heap,depth))
        if (b.read_u32(self.rtt+0x44)!=128 or b.read_u32(self.rtt+0x48)!=128
                or b.read_u32(self.rtt+0x60)!=depth or b.read_u32(self.rtt+0x17C)!=2
                or not self._inside(b.read_u32(self.rtt+0x3C),65536)
                or not self._inside(b.read_u32(self.rtt+0x180),8)):
            raise RuntimeError('Buffers RTT non privés ou dimensions inattendues.')
        self._write(self.rtt+0x164,struct.pack('<fI3f',1.1,0x80000000,0.,.89,0.))
        physical=b.read_u32(self.rtt+0x3C)+0xC000000
        self._write(self.cmd_addr_ptr,struct.pack('<I',physical>>3))
        self._write(self.cmd_size_ptr,struct.pack('<I',0x00800080))

    def bind_model(self,flat):
        if flat==self.flat:return
        flags=self.bridge.read_u32(flat+0x164) if self._inside(flat,0x178) else None
        if (flags is None or self.bridge.read_u32(flat)!=0x6EBEF0
                or self.bridge.read_u32(flat+4)!=self.heap):
            raise ValueError('Le RTT exige un FlatLink privé dans son heap.')
        # The generic descriptor enables the ordinary pass. Native wall actors
        # disable passes 0..5: their meshes are drawn exclusively by RTT pass 7.
        self._write(flat+0x164,struct.pack('<I',flags|0x3F))
        # The presenter binds this Flat atomically after its world matrix and
        # animations have been calculated; never clear a visible RTT in Python.
        self.flat=flat

    def set_surface(self,surface,control_address=0,queued=False):
        if self.view_correction:
            raise ValueError('L’ancien essai de rotation hors du mur est retiré ; utilisez la projection native.')
        state=validate_fresco_surface(surface)
        if state is None:raise ValueError('Surface Fresco invalide.')
        if not self.prepared:self.prepare()
        if queued:
            pending=self.bridge.read_u32(MAILBOX_PENDING_SURFACE)
            if pending is None:raise RuntimeError('Publication Fresco illisible.')
            if pending:return False
        active=self.bridge.read_u32(self.model+0x1CC)
        if active not in (0,1):raise RuntimeError('Slot Fresco non reconnu.')
        index=1-active;slot=self.slot_buffers[index];count=state['triangle_count']
        for key,stride,field in (('positions',9,'pos_ptr'),('vectors',9,'vec_ptr'),('uv',6,'uv_ptr')):
            self._write(slot[field],struct.pack(f'<{count*stride}f',*state[key]))
        stage=self.allocation+0x700
        if control_address and not self.allocation<=control_address<=self.allocation+0x1000-0xC0:
            raise ValueError('Descripteur Fresco hors de sa réserve.')
        self._write(stage,struct.pack('<2I24f3I',index,count,*state['matrix'],*state['projection_matrix'],int(self.view_correction),control_address,self.model))
        if queued:
            if not self.bridge.write_u32(MAILBOX_PENDING_SURFACE,stage):raise RuntimeError('Publication Fresco incertaine.')
        else:
            result,_=self._native(Job.FRESCO_SURFACE,self.model,stage)
            if result!=1:raise RuntimeError('Publication de la surface refusée.')
        self.active_slot=index
        return True

    def hide(self):
        if not self.model:return True
        if self.uncertain or self.jobs.pending:return False
        self._native(Job.REGISTER,self.model,0,0)
        if self.rtt_initialized:self._native(Job.REMOVE_RTT,self.rtt)
        self.visible=False
        return True

    def close(self):
        if self.uncertain or self.jobs.pending:return False
        if not self.hide():return False
        if self.rtt_initialized:
            self._native(Job.CLEAR_RTT,self.rtt)
            self._native(Job.DESTROY_RTT,self.rtt);self.rtt=self.flat=0;self.rtt_initialized=False
        if self.model:self._native(Job.DESTROY_MODEL,self.model,0);self.model=0
        self.prepared=False;self.wrapper=0;self.slot_buffers=[]
        return True

