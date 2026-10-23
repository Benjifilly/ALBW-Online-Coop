"""Dernière entrée sauvegardée par le jeu, jamais la pose du chargement."""
import struct
from src.rpc.azahar_bridge import TITLE_ID_EUR

FINGERPRINTS={
    0x4C3C70:bytes.fromhex('140094e5d010d5e1001080e5141094e5'),
    0x4C3C80:bytes.fromhex('040095e5040081e5140094e5061186e0'),
    0x4C3C90:bytes.fromhex('086080e5'),
}


def pointer(value):return type(value) is int and value%4==0 and 0x08000000<=value<0x1F000000


def valid_point(value):
    return (isinstance(value,list) and len(value)==3
            and all(type(v) is int and 0<=v<=limit for v,limit in zip(value,(23,65535,65535)))
            and any(value))


class SavedPointReader:
    def __init__(self,bridge):self.bridge=bridge;self.identity=None;self.supported=False

    def read(self,snapshot):
        b=self.bridge
        if not snapshot.in_game or not snapshot.pose_verified:return None
        identity=b.attached_pid,b.attached_title_id
        if identity!=self.identity:
            observed=[b.read_memory(at,len(raw)) for at,raw in FINGERPRINTS.items()]
            if any(raw is None for raw in observed):return None
            self.identity=identity
            self.supported=(b.attached_title_id==TITLE_ID_EUR
                and observed==list(FINGERPRINTS.values()))
        if not self.supported:return None
        game=b.read_u32(0x709DF8);saves=b.read_u32(0x711DE8)
        if not pointer(game) or not pointer(saves):return None
        save=b.read_u32(saves+0x14)
        if not pointer(save):return None
        if b.read_memory(game+0x60,2)!=b'\0\0' or b.read_memory(game+0x48,1)!=b'\xff':return None
        current=b.read_memory(game+0x18,12);raw=b.read_memory(save,12)
        if not current or not raw:return None
        course,stage,entrance=struct.unpack_from('<B3xIH',current)
        if (course,stage,entrance)!=(snapshot.course,snapshot.stage,snapshot.entrance):return None
        point=list(struct.unpack('<3I',raw))
        if (not valid_point(point) or b.read_u32(0x709DF8)!=game or b.read_u32(0x711DE8)!=saves
                or b.read_u32(saves+0x14)!=save or b.read_memory(save,12)!=raw
                or b.read_memory(game+0x18,12)!=current or b.read_memory(game+0x60,2)!=b'\0\0'
                or b.read_memory(game+0x48,1)!=b'\xff'):return None
        return point
