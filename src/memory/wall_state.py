"""Read the painting's final visual state; never serialize native pointers."""
import struct

from src.memory.render_state import _heap,_number,_integer


def validate_wall_state(value):
    if not isinstance(value,dict) or not _integer(value.get('variant'),0,3):return None
    matrix,scale=value.get('matrix'),value.get('scale')
    if not isinstance(matrix,list) or len(matrix)!=12 or any(
            not _number(v,-1e6 if i%4==3 else -8,1e6 if i%4==3 else 8) for i,v in enumerate(matrix)):
        return None
    if not isinstance(scale,list) or len(scale)!=3 or not all(_number(v,0,8) for v in scale):return None
    meshes=value.get('meshes');animations=value.get('animations')
    if not isinstance(meshes,list) or not 1<=len(meshes)<=64 or any(type(v) is not bool for v in meshes):return None
    if not isinstance(animations,list) or len(animations)!=3:return None
    phases=[]
    for phase in animations:
        if phase is None:
            phases.append(None)
            continue
        if not isinstance(phase,list) or len(phase)!=5 or not _integer(phase[0],0,15):return None
        if not all(_number(v,-1,10000) for v in (phase[1],phase[2],phase[4])):return None
        if not _number(phase[3],-32,32):return None
        if any(v>max(1,phase[4])+1 for v in phase[1:3]):return None
        phases.append(list(phase))
    clean = dict(variant=value['variant'],matrix=list(matrix),scale=list(scale),
                 meshes=list(meshes),animations=phases)
    if 'surface' in value and value['surface'] is not None:
        from src.memory.fresco_model import validate_fresco_surface
        if isinstance(value['surface'],dict) and 'encoding' in value['surface']:
            from src.memory.fresco_wire import decode_surface
            surface=decode_surface(value['surface'])
        else:
            surface = validate_fresco_surface(value['surface'])
        if surface is not None:
            clean['surface'] = surface
    return clean


def read_wall_state(reader,player):
    b=reader.bridge
    controller=b.read_u32(player+0x604)
    wall=b.read_u32(controller+0x28) if _heap(controller) else None
    data=b.read_memory(wall,0xD0) if _heap(wall) else None
    if not data or len(data)!=0xD0 or struct.unpack_from('<I',data)[0]!=player:return None
    variant=data[0x24]
    if variant>=4:return None
    root,*animations=struct.unpack_from('<4I',data,0x28+variant*0x28)
    if not _heap(root) or b.read_u32(root)!=0x6EBEF0:return None
    transform=b.read_memory(root+0x74,60) if _heap(root) else None
    if not transform or len(transform)!=60:return None
    count=b.read_u32(root+0x5C)
    if not _integer(count,1,64):return None
    meshes=getattr(reader,'_visible_batch',lambda r:None)(root)
    if meshes is None or len(meshes)!=count:
        meshes=[reader._visible(root,index) for index in range(count)]
    value=dict(variant=variant,scale=list(struct.unpack_from('<3f',transform)),
        matrix=list(struct.unpack_from('<12f',transform,12)),meshes=meshes,
        animations=[reader._phase(p,65535) for p in animations])
    try:
        fresco_reader = getattr(reader, '_fresco_reader', None)
        if fresco_reader is None:
            from src.memory.fresco_geometry import FrescoGeometryReader
            fresco_reader = reader._fresco_reader = FrescoGeometryReader(b)
        surf = fresco_reader.read(player)
        # A native buffer flip can overlap these read-only RPC calls. Retry
        # once before dropping a complete moving surface from the snapshot.
        if surf is None:surf=fresco_reader.read(player)
        if surf and surf.get('triangle_count', 0) <= 12:
            value['surface'] = surf
    except (OSError,ValueError,RuntimeError):
        pass
    # A native variant switch during the read invalidates only the painting.
    if (b.read_u32(player+0x604)!=controller or b.read_u32(controller+0x28)!=wall
            or b.read_u8(wall+0x24)!=variant or b.read_u32(wall+0x28+variant*0x28)!=root):return None
    return validate_wall_state(value)
