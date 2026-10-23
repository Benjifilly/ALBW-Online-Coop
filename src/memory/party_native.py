"""Deux commandes natives de défaite, avec reçus hors des ressources du rendu."""
from pathlib import Path
import struct
from src.memory.world_layout import reserve,slot

CONTROL=0x800
MAGIC=0x50525459
FINGERPRINTS={
    0x255AEC:bytes.fromhex('70402de90150b0e10040a0e12700000a'),
    0x4EF3F0:bytes.fromhex('f0472de90040a0e10150a0e10260a0e1'),
    0x520B20:bytes.fromhex('0010a0e3012180e0102092e5e921d2e5'),
}


def prepare(world):
    b=world.bridge
    if any(b.read_memory(at,len(raw))!=raw for at,raw in FINGERPRINTS.items()):
        raise ValueError('Le retour du groupe ne correspond pas au profil EUR vérifié.')
    code=Path(__file__).with_suffix('.bin').read_bytes();at=world.allocation+reserve('party_life')
    if not (b.write_memory(at,code.ljust(0x1000,b'\0')) and b.read_memory(at,len(code))==code):
        raise RuntimeError('Préparation du retour du groupe non confirmée.')


class NativePartyReturn:
    def __init__(self,life):self.life=life;self.bridge=life.bridge;self.pending=None

    @property
    def control(self):return self.life.world.allocation+reserve('party_life')+CONTROL

    def step(self,op,serial,anchor):
        """None en attente, nombre de rubis perdus une fois la commande terminée."""
        b=self.bridge;c=self.control
        if not self.life._ready() or b.read_u32(c)!=MAGIC:return None
        if self.pending is not None:
            if b.read_u32(c+8)!=self.pending:
                if b.read_u32(c+4)!=self.pending:b.write_u32(c+4,self.pending)
                return None
            self.pending=None
            if b.read_u32(c+0xc)==1:return b.read_u32(c+0x3c) if op==2 else 0
        generation=b.read_u32(slot('life_session_state')+4)
        if type(generation) is not int:return None
        room=anchor['room'];yaw=round(anchor['yaw']*(1<<32)/6.283185307179586)&0xFFFFFFFF
        raw=struct.pack('<3I B3xIHBB3fI',op,generation,serial,*room,anchor['entrance'],1,0,*anchor['pos'],yaw)
        if not b.write_memory(c+0x10,raw):return None
        nonce=max(b.read_u32(c+4) or 0,b.read_u32(c+8) or 0)+1
        self.pending=nonce
        # Une réponse RPC perdue laisse la requête en attente, jamais redoublée.
        b.write_u32(c+4,nonce)
        return None
