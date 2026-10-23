"""Verified EUR shared-prop identities (Mass batches and actor props). Read-only."""
from dataclasses import dataclass
import math
import struct

from src.memory.enemy_sync import EnemySynchronizer
from src.memory.world_actors import placement_key
from src.memory.render_state import _heap
from src.mass_families import family,grass_profile,mass_profile,actor_prop

GRASS_TYPE=316
GRASS_VTABLE=0x6EE60C
CUT_FUNCTION=0x237288
CUT_ORIGINAL=bytes.fromhex('f04f2de9')
CUT_FINGERPRINT=bytes.fromhex('f04f2de90050a0e1020052e30170a0e1')


@dataclass(frozen=True)
class MassInstance:
    target:tuple
    address:int
    handle:int
    index:int
    records:int
    state:int


class MassStateReader:
    def __init__(self,bridge):
        self.bridge=bridge;self.actors=EnemySynchronizer(bridge)
        self._xyz_cache={}
        self._cache_context=None

    def read(self,room):
        context=self.actors._read_context(tuple(room))
        if context!=self._cache_context:
            self._xyz_cache={};self._cache_context=context
        if context is None:return None,{}
        current_cache={}
        b=self.bridge;result={};duplicates=set()
        survey=self.actors.survey.read(tuple(room))
        batches=[a for a in survey['actors'] if mass_profile(a['type'])==a['vtable']]
        group_keys=[placement_key(a,room) for a in batches]
        for actor,key in zip(batches,group_keys):
            if key is None or group_keys.count(key)!=1:continue
            address=self.actors._resolve_actor_address(actor['handle'],context)
            if address is None:continue
            header=b.read_memory(address,0x638)
            if not header or len(header)!=0x638:continue
            if struct.unpack_from('<I',header)[0]!=mass_profile(actor['type']) or header[0x2D7]:continue
            if struct.unpack_from('<I',header,0x2CC)[0]!=actor['handle']:continue
            shape=family(actor['type']);records_at,size=shape.records_offset,shape.record_size
            count=struct.unpack_from('<I',header,shape.count_offset)[0];records=struct.unpack_from('<I',header,records_at)[0]
            placement_count=struct.unpack_from('<I',header,0x478)[0]
            placement_table=struct.unpack_from('<I',header,0x480)[0]
            timelines=struct.unpack_from('<I',header,0x610)[0]
            # The timeline table is only verified for grass; shrubs bound it natively.
            if (not 0<count<=4096 or count!=placement_count
                    or (grass_profile(actor['type']) and count!=timelines)):continue
            if not _heap(records) or not _heap(placement_table):continue
            raw=b.read_memory(records,count*size)
            ptrs=b.read_memory(placement_table,count*4)
            if raw is None or len(raw)!=count*size or ptrs is None or len(ptrs)!=count*4:continue
            local=[]
            for index,(pointer,) in enumerate(struct.iter_unpack('<I',ptrs)):
                if not _heap(pointer):continue
                cache_key=(actor['handle'],records,placement_table,index,pointer)
                xyz=self._xyz_cache.get(cache_key)
                if xyz is None:
                    placement=b.read_memory(pointer,0x10)
                    if placement is None or len(placement)!=0x10:continue
                    xyz=struct.unpack_from('<3f',placement,4)
                    if not all(math.isfinite(v) and abs(v)<=1e6 for v in xyz):continue
                current_cache[cache_key]=xyz
                # Stable batch placement + native per-instance position. An
                # index alone is never enough to identify an object remotely.
                target=(*key[2:],index,*(round(v*10000) for v in xyz))
                state=raw[index*size]
                local.append(MassInstance(target,address,actor['handle'],index,records,state))
            if (b.read_u32(address+0x2CC)!=actor['handle'] or b.read_u8(address+0x2D7)
                    or b.read_u32(address+records_at)!=records):continue
            for instance in local:
                if instance.target in result:duplicates.add(instance.target)
                result[instance.target]=instance
        self._read_props(survey,room,context,result,duplicates)
        if self.actors._read_context(tuple(room))!=context:
            self._xyz_cache={};self._cache_context=None;return None,{}
        self._xyz_cache=current_cache
        for key in duplicates:result.pop(key,None)
        return context,result

    def _read_props(self,survey,room,context,result,duplicates):
        """Whole-actor props: identity [type, placement, x, y, z], state of their machine."""
        b=self.bridge
        props=[a for a in survey['actors'] if actor_prop(a['type']) and actor_prop(a['type']).vtable==a['vtable']]
        keys=[placement_key(a,room) for a in props]
        for actor,key in zip(props,keys):
            if key is None or keys.count(key)!=1:continue
            address=self.actors._resolve_actor_address(actor['handle'],context)
            if address is None:continue
            prop=actor_prop(actor['type'])
            if (b.read_u32(address)!=prop.vtable or b.read_u32(address+0x2CC)!=actor['handle']
                    or b.read_u8(address+0x2D7)):continue
            state=b.read_u8(address+prop.machine_offset+4)
            if type(state) is not int:continue
            target=tuple(key[2:])
            if target in result:duplicates.add(target)
            result[target]=MassInstance(target,address,actor['handle'],0,0,state)
