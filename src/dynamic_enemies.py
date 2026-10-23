"""Logical identities for verified soldiers and bees born from shared drops."""
from src.combat_profiles import sword_target

KINDS={526,529,531,585}

def key_for(room,epoch,revision,ordinal,kind):
    return f'{room[0]}:{room[1]}:spawn:{epoch}:{revision}:{ordinal}:{kind}'

def parse_key(key,room=None):
    if not isinstance(key,str) or len(key)>120:return None
    p=key.split(':')
    if len(p)!=7 or p[2]!='spawn':return None
    try:c,s,rev,ordinal,kind=(int(p[i]) for i in (0,1,4,5,6))
    except ValueError:return None
    epoch=p[3]
    if (not 0<=c<=23 or not 0<=s<=65535 or not 1<=rev<=8192 or not 0<=ordinal<8
            or kind not in KINDS or sword_target(kind) is None or len(epoch)!=16
            or any(x not in '0123456789abcdef' for x in epoch)
            or key_for((c,s),epoch,rev,ordinal,kind)!=key or room is not None and (c,s)!=tuple(room)):
        return None
    return (c,s),epoch,rev,ordinal,kind

def binding_for(bridge,handle,room,kind,placement,initial=None):
    """Revalidate the owned native receipt, including retired lethal targets.

    Live writers must also validate the actor slot, generation and header.
    """
    from src.memory.shared_loot import POINTER,MAGIC,DROPS
    entry=getattr(bridge,'shared_dynamic_enemies',{}).get(handle)
    if (entry is None or kind not in KINDS or placement!=-1 or entry['type']!=kind
            or tuple(room or ())!=entry['room'] or parse_key(entry['key'],room) is None
            or bridge.attached_pid!=entry['pid']):return None
    if initial is not None and tuple(round(v*10000) for v in initial)!=entry['initial_key']:return None
    a=entry['service'];row=a+DROPS+(entry['record']-1)*128
    if (not 1<=entry['record']<=4096 or bridge.read_u32(POINTER)!=a or bridge.read_u32(a)!=MAGIC
            or bridge.read_u32(a+4)!=1 or not (bridge.read_u32(a+8) or 0)>0
            or bridge.read_u32(row)!=entry['record'] or bridge.read_u32(row+0x48)!=1
            or bridge.read_u32(row+0x40)!=entry['address'] or bridge.read_u32(row+0x44)!=handle
            or bridge.read_u32(row+0x14)!=kind):return None
    return entry
