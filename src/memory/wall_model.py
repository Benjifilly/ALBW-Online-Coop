"""Private FlatLink prototype. Explicit use only; not enabled by the normal UI."""
import struct

from src.memory.native_jobs import NativeRenderJobs,NativeJobRejected,RenderJob as Job,PRIVATE_SIZE
from src.memory.remote_model import MAILBOX
from src.memory.render_state import _heap
from src.memory.wall_state import validate_wall_state


class PrivateWallModel:
    fresco = None

    def __init__(self,trial,player,room):
        self.trial=trial;self.bridge=trial.bridge;self.jobs=NativeRenderJobs(trial)
        self.player=player;self.room=tuple(room)
        self.allocation=self.heap=0;self.models={};self.current=None;self.sequence=0
        self.uncertain=False
        self.palette=None
        # Real surfaces already contain the game's projection onto the wall.
        # The optional extra basis is only for the synthetic in-air diagnostic.
        self.fresco=None;self.control=0;self.debug_solid=False;self.view_correction=False

    def _read(self,at,size):
        data=self.bridge.read_memory(at,size)
        if data is None or len(data)!=size:raise RuntimeError('Ressource murale incomplète.')
        return data

    def _write(self,at,data):
        if not self.allocation<=at<=self.allocation+PRIVATE_SIZE-len(data):
            raise ValueError('Écriture hors de la réserve privée.')
        if not self.bridge.write_memory(at,data):raise RuntimeError('Écriture du rendu mural impossible.')

    def _alloc(self,size):
        pointer,_=self.jobs.call(Job.ALLOCATE,size,heap=self.heap)
        if not self.heap+0xA8<=pointer<=self.allocation+PRIVATE_SIZE-size:
            raise RuntimeError('Allocation murale hors de la réserve privée.')
        return pointer

    def source(self,variant):
        b=self.bridge
        if variant not in (0,1):raise ValueError('Cette famille de peinture n’est pas encore prise en charge.')
        controller=b.read_u32(self.player+0x604)
        wall=b.read_u32(controller+0x28) if _heap(controller) else 0
        fields=self._read(wall+0x28+variant*0x28,16) if _heap(wall) else None
        if not fields:raise RuntimeError('Entrez une fois dans un mur pour charger les ressources de peinture.')
        root,*anims=struct.unpack('<4I',fields)
        if not _heap(root) or b.read_u32(root)!=0x6EBEF0:
            raise RuntimeError('Cette variante murale n’est pas chargée.')
        resource=b.read_u32(root+0x50)
        raw=b.read_u32(resource+8) if _heap(resource) else 0
        header=self._read(resource,0x100) if _heap(resource) else None
        if not header or not _heap(raw) or self._read(raw,1)!=b'\x02':
            raise RuntimeError('Le profil graphique de cette peinture n’est pas vérifié.')
        mode=b.read_u32(0x70BB68)
        if mode is None or mode not in (0xFFFFFFFF,1):raise RuntimeError('Mode graphique global non pris en charge.')
        count=b.read_u32(root+0x5C)
        sizes=struct.unpack_from('<4I',header,0x4C)
        if type(count) is not int or not 1<=count<=64 or any(size>0x10000 for size in sizes):
            raise RuntimeError('Budget du modèle mural hors du profil vérifié.')
        # Material/skeleton/visibility buffers are sized by these resource fields.
        # Each mesh adds B0+14 bytes; include every block header/alignment and a
        # conservative extra page for native object/list initialization.
        budget=sum(sizes)+count*(0xB0+0x14+64)+0x3000
        animation_resources=[]
        for anim in anims:
            h=self._read(anim,0x18) if _heap(anim) else None
            if not h:raise RuntimeError('Contrôleur mural absent.')
            kind,n,table=struct.unpack_from('<3I',h)
            if kind not in (0,1,2) or not 1<=n<=16 or not _heap(table):
                raise RuntimeError('Ressources d’animation murale non reconnues.')
            pointers=list(struct.unpack(f'<{n}I',self._read(table,n*4)))
            entries=[];tracks=0
            for pointer in pointers:
                wrapper=self._read(pointer,24) if _heap(pointer) else None
                if not wrapper:raise RuntimeError('Animation murale non chargée.')
                raw_animation,bound=struct.unpack_from('<2I',wrapper)
                if bound!=resource or not _heap(raw_animation):raise RuntimeError('Animation liée à une autre ressource.')
                desc=self._read(raw_animation,0x14)
                track_count=struct.unpack_from('<H',desc,6)[0]
                entry_count=struct.unpack_from('<I',desc,0x10)[0]
                if track_count>4096 or entry_count>4096:raise RuntimeError('Animation trop grande pour cet essai.')
                tracks=max(tracks,track_count)
                entries.append(entry_count)
            budget+=0xA8+2*40+20+n*4+max(entries)*4+tracks*32+0x1000
            animation_resources.append((kind,pointers))
        if budget>0x100000:raise RuntimeError('La réserve prévue est insuffisante pour cette variante.')
        renderer=b.read_u32(0x70B960)
        used=b.read_u32(renderer+0x5C) if _heap(renderer) else None
        capacity=b.read_u32(renderer+0x60) if _heap(renderer) else None
        if used is None or capacity is None or capacity-used<4 or not b.read_u32(renderer+0x68):
            raise RuntimeError('Le moteur n’a pas assez de places de rendu libres.')
        return dict(root=root,resource=resource,count=count,budget=budget,animations=animation_resources)

    def reserve(self):
        result,pointer=self.jobs.call(Job.RESERVE)
        if result or not 0x14000000<=pointer<=0x1C000000-PRIVATE_SIZE or pointer%0x1000:
            raise RuntimeError('La réserve murale privée n’est pas disponible.')
        self.allocation=pointer
        data=bytearray(0x1000)
        struct.pack_into('<2I',data,0,0x6F5988,pointer+0x20)
        data[0x20:0x2A]=b'CoopWall\0\0'
        self._write(pointer,data)
        self.uncertain=True
        try:
            heap,_=self.jobs.call(Job.CREATE_HEAP,pointer+0x1000,PRIVATE_SIZE-0x1000,pointer,0)
        except NativeJobRejected:
            self.uncertain=False
            raise
        if heap!=pointer+0x1000 or self.bridge.read_u32(heap)!=0x6F7584:
            raise RuntimeError('La réserve murale n’a pas pu être initialisée.')
        self.heap=heap;self.uncertain=False

    def prepare(self,state):
        state=validate_wall_state(state)
        if state is None:raise ValueError('État mural invalide.')
        variant=state['variant']
        if variant in self.models:
            self._prepare_fresco()
            return
        source=self.source(variant)
        if not self.heap:self.reserve()
        free,_=self.jobs.call(Job.FREE_SIZE,self.heap,heap=self.heap)
        largest,_=self.jobs.call(Job.MAX_BLOCK,self.heap,4,heap=self.heap)
        if free<source['budget'] or largest<source['budget']:
            raise RuntimeError('La réserve privée restante ne couvre pas la création.')
        descriptor=bytearray(0x40)
        struct.pack_into('<5I',descriptor,0,0x6F5988,0x6C57E0,0x6F5988,
                         0x2D201C if variant==0 else 0x2D2524,source['resource'])
        struct.pack_into('<I',descriptor,0x18,1)
        descriptor[0x1C:0x1E]=b'\x01\x01';descriptor[0x38:0x3A]=b'\x01\xC4'
        self._write(self.allocation+0x200,descriptor)
        self.uncertain=True
        try:
            model,_=self.jobs.call(Job.CREATE_MODEL,self.allocation+0x200,heap=self.heap)
        except NativeJobRejected:
            self.uncertain=False
            raise
        if not self.heap<=model<self.allocation+PRIVATE_SIZE or self.bridge.read_u32(model+4)!=self.heap:
            raise RuntimeError('Le modèle mural n’appartient pas à sa réserve.')
        self.models[variant]=dict(model=model,animations=[],phase_ids=[None]*3,
            resource_counts=[len(resources) for _,resources in source['animations']],meshes=list(state['meshes']))
        self.uncertain=False
        self.jobs.call(Job.REGISTER,model,0,0,heap=self.heap)
        if self.bridge.read_u32(model+0x16C)!=0:raise RuntimeError('Un callback mural inattendu est installé.')
        for kind,resources in source['animations']:
            animation=self._alloc(0xA8)
            self.jobs.call(Job.CREATE_ANIMATION,animation,len(resources),0,1,heap=self.heap)
            for index,resource in enumerate(resources):
                self.jobs.call(Job.ADD_RESOURCE,animation,index,resource,heap=self.heap)
            # R3=0 deliberately keeps the source wrappers read-only. They bind
            # by index to the same immutable constRes as the private G3D.
            self.jobs.call(Job.BIND_ANIMATION,animation,0xFFFFFFFF,0xFFFFFFFF,0,heap=self.heap)
            self.models[variant]['animations'].append(animation)
        for index,visible in enumerate(state['meshes']):
            if index>=source['count']:raise RuntimeError('La forme distante ne correspond pas au modèle local.')
            if not visible:
                self.jobs.call(Job.SHOW_MESH,model,0,index,heap=self.heap)
        self._prepare_fresco()

    def _prepare_fresco(self):
        if self.fresco is None:
            from src.memory.fresco_model import PrivateFrescoModel
            self.fresco=PrivateFrescoModel(
                self.trial,self.player,self.room,self.allocation,self.heap,self.jobs)
        self.fresco.prepare()
        self.fresco.view_correction=self.view_correction

    def publish(self,state,lease=60,colour=None):
        state=validate_wall_state(state)
        if state is None or type(lease) is not int or not 1<=lease<=120:raise ValueError('État mural invalide.')
        if not state.get('surface'):raise ValueError('La surface murale réelle est absente.')
        self.prepare(state)
        pending=self.bridge.read_u32(MAILBOX+0x1F8)
        if pending is None:raise RuntimeError('État de publication murale illisible.')
        if pending:return False
        variant=state['variant'];item=self.models[variant]
        self.current=variant
        for index,(animation,phase) in enumerate(zip(item['animations'],state['animations'])):
            if phase is None or item['phase_ids'][index]==phase[0]:continue
            if phase[0]>=item['resource_counts'][index]:raise ValueError('Animation absente de la ressource locale.')
            desc=bytearray(0x5C);address=self.allocation+0x300
            struct.pack_into('<3I',desc,0,1,3,address+0xC)
            struct.pack_into('<I4fI',desc,0xC,phase[0],phase[1],phase[3],-1,1,0xFFFFFFFF)
            self._write(address,desc)
            self.jobs.call(Job.SELECT_ANIMATION,animation,address,heap=self.heap)
            item['phase_ids'][index]=phase[0]
        if len(state['meshes'])!=len(item['meshes']):raise ValueError('Le nombre de formes a changé.')
        for index,(before,after) in enumerate(zip(item['meshes'],state['meshes'])):
            if before!=after:self.jobs.call(Job.SHOW_MESH,item['model'],int(after),index,heap=self.heap)
        item['meshes']=list(state['meshes'])
        phases=b''.join(struct.pack('<I4f',*(phase if phase is not None else (0xFFFFFFFF,0,0,0,0))) for phase in state['animations'])
        if item.get('published_phases')!=phases:
            self.sequence=(self.sequence+1)&0xFFFFFFFF
            item['published_phases']=phases;item['sequence']=self.sequence
        control=bytearray(0xC0)
        struct.pack_into('<7I',control,0,0x57414C4C,lease,1,item['model'],*item['animations'])
        struct.pack_into('<2I',control,0x1C,item['sequence'],self.allocation+0x900+variant*4)
        struct.pack_into('<12f3f',control,0x24,*state['matrix'],*state['scale'])
        for at,phase in zip((0x60,0x74,0x88),state['animations']):
            struct.pack_into('<I4f',control,at,*(phase if phase is not None else (0xFFFFFFFF,0,0,0,0)))
        # Private descriptor mode, never a value accepted from the network.
        # FlatLink uses its world rotation without the extra renderer basis.
        struct.pack_into('<I',control,0xA0,3 if self.debug_solid else 2)
        struct.pack_into('<2I',control,0xA4,*self.room)
        if colour is not None:
            if self.palette is None:
                from src.memory.painting_palette import PrivatePaintingPalette
                self.palette=PrivatePaintingPalette(self)
            if item.get('palette_ready') is not True:
                self.jobs.call(Job.CALCULATE,item['model'],heap=self.heap)
                item['palette_ready']=True
            struct.pack_into('<I',control,0xAC,self.palette.table_for(item['model'],colour))
        self.fresco.bind_model(item['model'])
        if self.debug_solid:
            self._write(self.bridge.read_u32(self.fresco.rtt+0x3C),b'\xff'*65536)
        struct.pack_into('<2I',control,0xB0,self.fresco.model,self.fresco.rtt)
        struct.pack_into('<2f',control,0xB8,*state['surface'].get('uv_transform',[1.,0.]))
        # Never overwrite the descriptor being consumed by the frame hook.
        address=self.allocation+(0x800 if self.control==self.allocation+0x100 else 0x100)
        self._write(address,control)
        # The closed surface job flips geometry and the complete descriptor
        # together. A frame cannot observe a new pose with the previous RTT.
        self.fresco.set_surface(state['surface'],control_address=address,queued=True)
        self.control=address
        return True

    def hide(self):
        # The game thread removes the model on its next update. This also works
        # when an actor/menu transition currently prevents new native jobs.
        return not self.control or self.bridge.write_u32(self.control+8,0)

    def keep_visible(self):
        return not self.control or self.bridge.write_u32(self.control+4,60)

    def close(self):
        if self.uncertain or self.jobs.pending or self.fresco and self.fresco.uncertain:return False
        if self.allocation:
            if not self.bridge.write_u32(MAILBOX+0x1F8,0):return False
            if not self.bridge.write_u32(MAILBOX+0x158,0):return False
            if self.fresco:
                if not self.fresco.close():return False
                self.fresco=None
            for item in self.models.values():
                self.jobs.call(Job.REGISTER,item['model'],0,0,heap=self.heap)
            # G3D objects also belong to immutable-resource lists. Remove those
            # registrations before releasing the heap that holds their nodes.
            for key,item in list(self.models.items()):
                self.uncertain=True
                self.jobs.call(Job.DESTROY_MODEL,item['model'],0,heap=self.heap)
                del self.models[key]
                self.uncertain=False
            if self.heap:
                self.jobs.call(Job.DESTROY_HEAP,self.heap,heap=self.heap);self.heap=0
            rc,_=self.jobs.call(Job.RELEASE,self.allocation)
            if rc:return False
            self.allocation=0;self.control=0
        return True
