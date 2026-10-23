"""Read-only actor identity survey. No gameplay/HP/position writes are exposed."""
from collections import defaultdict
import math
import struct
from src.memory.render_state import _heap
from src.rpc.azahar_bridge import TITLE_ID_EUR

LOOKUP_SIGNATURE=bytes.fromhex('000051e30a00000a042090e5210ba0e1')
TYPE_SIGNATURE=bytes.fromhex('d00290e5080090e51eff2fe1')


def placement_key(actor,room):
    if actor.get('placement_id',-1)<=0:return None
    xyz=actor['initial_position']
    return (*room,actor['type'],actor['placement_id'],*(round(v*10000) for v in xyz))


def compare_surveys(first,second):
    if first.get('room')!=second.get('room'):
        return dict(status='different_rooms',matched=[],ambiguous=[],only_first=[],only_second=[])
    room=first['room'];tables=[]
    for survey in (first,second):
        table=defaultdict(list)
        for actor in survey['actors']:
            key=placement_key(actor,room)
            if key is not None:table[key].append(actor)
        tables.append(table)
    a,b=tables;matched=[];ambiguous=[]
    for key in a.keys()&b.keys():
        if len(a[key])==len(b[key])==1:
            matched.append(dict(key=list(key),first_handle=a[key][0]['handle'],second_handle=b[key][0]['handle']))
        else:ambiguous.append(list(key))
    return dict(status='compared',matched=sorted(matched,key=lambda x:x['key']),ambiguous=sorted(ambiguous),
        only_first=[list(k) for k in sorted(a.keys()-b.keys())],
        only_second=[list(k) for k in sorted(b.keys()-a.keys())])


class ActorSurvey:
    def __init__(self,bridge):self.bridge=bridge

    def read(self,room):
        b=self.bridge
        if b.attached_title_id!=TITLE_ID_EUR or b.read_memory(0x52FB78,16)!=LOOKUP_SIGNATURE or b.read_memory(0x588BBC,12)!=TYPE_SIGNATURE:
            raise ValueError('Liste d’acteurs non validée pour cette version du jeu.')
        owner=b.read_u32(0x706E20)
        if not _heap(owner):raise ValueError('Gestionnaire de monde indisponible.')
        manager=owner+0x3DBBC
        table=b.read_u32(manager+4);end=b.read_u32(manager+0xC)
        if not _heap(table) or not _heap(end) or not 0<=end-table<=4096*4:
            raise ValueError('Tableau d’acteurs invalide.')
        data=b.read_memory(table,end-table)
        if data is None or len(data)!=end-table:raise ValueError('Tableau d’acteurs incomplet.')
        actors=[]
        for slot,(actor,) in enumerate(struct.iter_unpack('<I',data)):
            if not _heap(actor):continue
            header=b.read_memory(actor,0x2D8)
            if not header or len(header)!=0x2D8 or header[0x2D7]:continue
            handle,profile=struct.unpack_from('<2I',header,0x2CC)
            valid_profile=_heap(profile) or (0x100000<=profile<0x7B0000 and profile%4==0)
            if not handle or handle>>22!=slot or not valid_profile:continue
            kind=b.read_u32(profile+8)
            xyz=struct.unpack_from('<3f',header,8)
            if kind is None or not all(math.isfinite(v) and abs(v)<=1e6 for v in xyz):continue
            # Actor destruction/recycling can occur while reading through RPC.
            if b.read_u32(actor+0x2CC)!=handle or b.read_u8(actor+0x2D7)!=0:continue
            actors.append(dict(handle=handle,type=kind,placement_id=struct.unpack_from('<h',header,0x58)[0],
                initial_position=list(xyz),vtable=struct.unpack_from('<I',header)[0]))
        return dict(room=list(room),actors=actors,slots=len(data)//4)
