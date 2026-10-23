"""Verified EUR visual state. Network values never contain game pointers."""
import math
import struct

RENDER_VERSION=1
RENDER_MAGIC=0x31444E52  # RND1
FINGERPRINTS={
    0x28D848:bytes.fromhex('f74f2de90040a0e1028b2dedd0d04de2'),
    0x1692F8:bytes.fromhex('70402de90040a0e1006091e50150a0e1'),
    0x28C484:bytes.fromhex('70402de90040a0e1030051e31c0094e5'),
    0x4CE99C:bytes.fromhex('102a00ee70402de90040a0e1200090e5'),
    0x168E9C:bytes.fromhex('f04f2de90040a0e10170a0e10290a0e1'),
}


def _number(value,low,high):
    return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def _integer(value,low,high):
    return type(value) is int and low<=value<=high


def unmerge_hook_pull(value):
    """The hookshot pull moves Link with the same +5B0/+6BC interpolation as a
    wall merge (22/10): a Link on his own flying hook is never a painting."""
    from src.memory.bomb_state import HOOK_KIND
    if value.get('merged') and any(shot.get('kind')==HOOK_KIND for shot in value.get('bombs',())):
        value['merged']=False;value.pop('wall',None)
    return value


def validate_render_state(value):
    if not isinstance(value,dict) or type(value.get('v')) is not int or value.get('v')!=RENDER_VERSION:
        return None
    matrix,scale,layers=value.get('matrix'),value.get('scale'),value.get('layers')
    if not isinstance(matrix,list) or len(matrix)!=12 or any(
            not _number(v,-1e6 if i%4==3 else -8,1e6 if i%4==3 else 8) for i,v in enumerate(matrix)):
        return None
    if not isinstance(scale,list) or len(scale)!=3 or not all(_number(v,0,8) for v in scale):
        return None
    if not isinstance(layers,list) or len(layers)!=2:
        return None
    clean_layers=[]
    for layer in layers:
        if layer is None:
            clean_layers.append(None)
            continue
        if not isinstance(layer,list) or len(layer)!=5 or not _integer(layer[0],0,175):return None
        if not all(_number(v,-1,10000) for v in (layer[1],layer[2],layer[4])):return None
        if not _number(layer[3],-32,32):return None
        if layer[1]>max(1,layer[4])+1 or layer[2]>max(1,layer[4])+1:return None
        clean_layers.append(list(layer))
    for key,high in (('sword',4),('shield',2),('gloves',3)):
        if not _integer(value.get(key),0,high):return None
    for key in ('sword_visible','shield_visible','boots','merged'):
        if type(value.get(key)) is not bool:return None
    if not _number(value.get('speed'),0,4):return None
    clean=dict(v=RENDER_VERSION,matrix=list(matrix),scale=list(scale),layers=clean_layers,
        sword=value['sword'],shield=value['shield'],gloves=value['gloves'],boots=value['boots'],
        sword_visible=value['sword_visible'],shield_visible=value['shield_visible'],
        speed=value['speed'],merged=value['merged'])
    if 'wall' in value:
        from src.memory.wall_state import validate_wall_state
        wall=validate_wall_state(value['wall'])
        if wall is None:return None
        clean['wall']=wall
    if 'equipment' in value:
        from src.memory.equipment_model import validate_equipment
        equipment=validate_equipment(value['equipment'])
        if equipment is None:return None
        clean['equipment']=equipment
    if 'effects' in value:
        from src.memory.attack_effects import validate_effects
        effects=validate_effects(value['effects'])
        if effects is None:return None
        clean['effects']=effects
    if 'carried' in value:
        from src.memory.carried_props import validate_carried
        carried=validate_carried(value['carried'])
        if carried is None:return None
        clean['carried']=carried
    if 'items' in value:
        from src.memory.item_visuals import validate_item_visuals
        items=validate_item_visuals(value['items'])
        if items is None:return None
        clean['items']=items
    if 'bombs' in value:
        from src.memory.bomb_state import validate_bombs
        bombs=validate_bombs(value['bombs'])
        if bombs is None:return None
        clean['bombs']=bombs
    if 'transition_frames' in value:
        frames=value['transition_frames']
        if not isinstance(frames,list) or len(frames)!=2 or not all(_integer(v,0,60) for v in frames):return None
        clean['transition_frames']=list(frames)
    return clean


def encode_render_state(value):
    value=validate_render_state(value)
    if value is None:return None
    result=bytearray(0x88)
    struct.pack_into('<8If',result,0,RENDER_MAGIC,value['sword'],int(value['sword_visible']),
        value['shield'],int(value['shield_visible']),value['gloves'],int(value['boots']),
        int(value['merged']),value['speed'])
    struct.pack_into('<12f3f',result,0x24,*value['matrix'],*value['scale'])
    for offset,layer in zip((0x60,0x74),value['layers']):
        struct.pack_into('<I4f',result,offset,*(layer if layer is not None else (0xFFFFFFFF,0,0,0,0)))
    return bytes(result)


def _heap(value):return type(value) is int and 0x08000000<=value<0x1F000000 and value%4==0


class RenderStateReader:
    def __init__(self,bridge):
        self.bridge=bridge
        self.identity=None
        self.supported=False

    def _visible(self,root,mesh):
        b=self.bridge
        count=b.read_u32(root+0x5C)
        table=b.read_u32(root+0x64)
        flags=b.read_u32(root+0x28)
        if not _integer(count,1,1024) or not _integer(mesh,0,count-1) or not _heap(table) or not _heap(flags):return None
        obj=b.read_u32(table+mesh*4)
        descriptor=b.read_u32(obj+0x90) if _heap(obj) else None
        index=b.read_u16(descriptor+4) if _heap(descriptor) else None
        if not _integer(index,0,32767):return None
        bits=b.read_u8(flags+(index>>3))
        return bool(bits&(1<<(index&7))) if bits is not None else None

    def _visible_batch(self,root):
        b=self.bridge
        raw=b.read_memory(root+0x28,0x40)
        if not raw or len(raw)!=0x40:return None
        flags=struct.unpack_from('<I',raw,0)[0]
        count=struct.unpack_from('<I',raw,0x5C-0x28)[0]
        table=struct.unpack_from('<I',raw,0x64-0x28)[0]
        if not _integer(count,1,64) or not _heap(table) or not _heap(flags):return None
        table_bytes=b.read_memory(table,count*4)
        if not table_bytes or len(table_bytes)!=count*4:return None
        objs=struct.unpack(f'<{count}I',table_bytes)
        flag_bytes=b.read_memory(flags,32)
        if not flag_bytes or len(flag_bytes)!=32:return None
        result=[]
        for obj in objs:
            if not _heap(obj):return None
            desc=b.read_u32(obj+0x90)
            if not _heap(desc):return None
            idx=b.read_u16(desc+4)
            if not _integer(idx,0,255):return None
            bits=flag_bytes[idx>>3]
            result.append(bool(bits&(1<<(idx&7))))
        return result

    def _phase(self,animation,expected):
        b=self.bridge
        header=b.read_memory(animation,0x18) if _heap(animation) else None
        if not header or len(header)!=0x18:return None
        count=struct.unpack_from('<I',header,0xC)[0]
        active=struct.unpack_from('<I',header,0x14)[0]
        # A blend or a concurrent native switch must not drop the transform,
        # equipment and wall state of the entire snapshot.
        if count!=1 or not _heap(active):return None
        entry=b.read_memory(active,24)
        if not entry or len(entry)!=24:return None
        index,resource,frame,previous,rate,length=struct.unpack('<2I4f',entry)
        if index!=expected and expected!=65535:return None
        if not 0<=index<=175 or not all(_number(v,-1,10000) for v in (frame,previous,length)):
            return None
        if not _number(rate,-32,32) or max(frame,previous)>max(1,length)+1:return None
        return [index,frame,previous,rate,length]

    def _transition_frames(self,animation):
        # Native selectAnimation stores the requested crossfade duration at
        # +78. The wrapper clears its one-shot blend-enable byte each frame,
        # so copying the wrapper only at construction loses every later fade.
        value=self.bridge.read_u32(animation+0x78) if _heap(animation) else None
        return value if _integer(value,0,60) else 0

    def read(self,player,expected_ids):
        b=self.bridge
        identity=(b.attached_pid,b.attached_title_id)
        if identity!=self.identity:
            self.identity=identity
            self.supported=all(b.read_memory(at,len(data))==data for at,data in FINGERPRINTS.items())
        if not self.supported:return {}
        model=b.read_u32(player+0x600)
        raw=b.read_memory(model,0x144) if _heap(model) else None
        if not raw or len(raw)!=0x144 or struct.unpack_from('<I',raw)[0]!=0x6DA424:return {}
        word=lambda offset:struct.unpack_from('<I',raw,offset)[0]
        root=word(0x10)
        transform=b.read_memory(root+0x74,60) if _heap(root) else None
        if not transform or len(transform)!=60:return {}
        scale=list(struct.unpack_from('<3f',transform))
        matrix=list(struct.unpack_from('<12f',transform,12))
        layers=[]
        for slot,expected in zip((0x4C,0x6C),expected_ids):
            layers.append(self._phase(word(slot),expected))
        # Legacy wire field "shield" is the Ravio-bracelet variant. The body
        # routines control sheath/bracelet; held weapons are separate G3Ds.
        shield=word(0x20)
        state=b.read_u32(shield+0x14) if _heap(shield) else None
        shield_frame=b.read_f32(state+8) if _heap(state) else None
        if not _number(shield_frame,0,2) or int(shield_frame)!=shield_frame:return {}
        sword_visible=self._visible(root,word(0xA4))
        shield_visible=self._visible(root,word(0xA8))
        # Native updateModel reads these cosmetic inputs; they are never written.
        equipment=b.read_memory(player+0x434,0x84)
        if not equipment or len(equipment)!=0x84:return {}
        speed=b.read_f32(player+0x5B0)
        # The native presentation branch at 1DD598/1DD5A0 uses this exact
        # interpolation factor, rather than one animation/action identifier.
        merged=_number(speed,0,1) and speed!=1.0 and b.read_u8(player+0x6BC)==1
        value=dict(v=RENDER_VERSION,matrix=matrix,scale=scale,layers=layers,
            sword=struct.unpack_from('<I',equipment)[0],sword_visible=sword_visible,
            shield=int(shield_frame),shield_visible=shield_visible,
            gloves=struct.unpack_from('<I',equipment,0x78)[0],
            boots=bool(struct.unpack_from('<I',equipment,0x80)[0]),speed=speed,merged=merged)
        value['transition_frames']=[self._transition_frames(word(slot)) for slot in (0x4C,0x6C)]
        if merged:
            from src.memory.wall_state import read_wall_state
            wall=read_wall_state(self,player)
            if wall is not None:value['wall']=wall
        from src.memory.equipment_model import read_equipment
        equipment=read_equipment(b,player)
        if equipment is not None:value['equipment']=equipment
        from src.memory.attack_effects import AttackEffectReader
        if not hasattr(self,'_attack_effects'):self._attack_effects=AttackEffectReader(b)
        value['effects']=self._attack_effects.read(player)
        from src.memory.item_visuals import ItemVisualReader
        if not hasattr(self,'_item_visuals'):self._item_visuals=ItemVisualReader(b)
        value['items']=self._item_visuals.read(player)
        from src.memory.bomb_state import BombStateReader
        if not hasattr(self,'_bombs'):self._bombs=BombStateReader(b)
        game=b.read_u32(0x709DF8)
        if _heap(game):
            room=(b.read_u8(game+0x18),b.read_u32(game+0x1C))
            if all(type(v) is int for v in room):value['bombs']=self._bombs.read(player,room)
        return validate_render_state(unmerge_hook_pull(value)) or {}
