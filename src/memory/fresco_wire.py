"""Bounded float32 surface payload, matching the game's numeric precision."""
import base64
import binascii
import struct
from src.memory.fresco_model import validate_fresco_surface

MAX_WIRE_TRIANGLES=12

def encode_surface(surface):
    clean=validate_fresco_surface(surface)
    if clean is None or clean['triangle_count']>MAX_WIRE_TRIANGLES:
        raise ValueError('Surface hors du budget réseau.')
    values=[v for key in ('matrix','projection_matrix','positions','vectors','uv') for v in clean[key]]
    result=dict(encoding='f32le',triangle_count=clean['triangle_count'],
                data=base64.b64encode(struct.pack(f'<{len(values)}f',*values)).decode('ascii'))
    if 'uv_transform' in clean:result['uv_transform']=clean['uv_transform']
    return result

def decode_surface(value):
    if not isinstance(value,dict) or value.get('encoding')!='f32le':return None
    count=value.get('triangle_count');data=value.get('data')
    if type(count) is not int or not 1<=count<=MAX_WIRE_TRIANGLES or not isinstance(data,str):return None
    size=(24+24*count)*4
    if len(data)!=4*((size+2)//3):return None
    try:raw=base64.b64decode(data,validate=True)
    except (ValueError,binascii.Error):return None
    if len(raw)!=size:return None
    floats=struct.unpack(f'<{size//4}f',raw)
    clean=dict(triangle_count=count,matrix=floats[:12],
        projection_matrix=floats[12:24],positions=floats[24:24+9*count],
        vectors=floats[24+9*count:24+18*count],uv=floats[24+18*count:])
    if 'uv_transform' in value:clean['uv_transform']=value['uv_transform']
    return validate_fresco_surface(clean)

def player_for_wire(player):
    render=player.get('render_state')
    wall=render.get('wall') if isinstance(render,dict) else None
    if not isinstance(wall,dict) or not wall.get('surface'):return player
    return dict(player,render_state=dict(render,wall=dict(wall,surface=encode_surface(wall['surface']))))
