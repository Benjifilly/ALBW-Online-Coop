"""Repère EUR de partie chargée, conservé pendant la vie du jeu.

Trois écritures de mode (MainGame/FileSelect), jamais un hook par image.
Leurs petits relais restent dans le padding déclaré, sans allocation ni bail.
Un nouveau chargement incrémente la génération, même compagnon arrêté.
"""
import secrets
import struct
from src.memory.world_layout import slot
from src.rpc.azahar_bridge import TITLE_ID_EUR

STATE=slot('life_session_state')
# site, relais, instructions précédentes, instruction déplacée
SITES=(
    (0x1E3208,slot('life_session_main'),bytes.fromhex('1c109fe5001091e5'),bytes.fromhex('2520c1e5')),
    (0x1E3248,slot('life_session_main_alt'),bytes.fromhex('1c009fe5000090e5'),bytes.fromhex('2510c0e5')),
    (0x29D5EC,slot('life_session_menu'),bytes.fromhex('000098e50210a0e3'),bytes.fromhex('2510c0e5')),
)


def branch(source,target):
    return struct.pack('<I',0xEA000000|(((target-source-8)//4)&0xFFFFFF))


def relay(hook,entry,original):
    # strb original ; compteur, sans changer les registres, LR ou les drapeaux.
    return (original+struct.pack('<6I',0xE92D0003,0xE59F0010,0xE5901004,
            0xE2811001,0xE5801004,0xE8BD0003)+branch(entry+28,hook+4)+struct.pack('<I',STATE))


class LoadedGameStamp:
    def __init__(self,bridge):self.bridge=bridge;self.ready=False;self.identity=None

    def read(self):
        b=self.bridge;identity=(b.attached_pid,b.attached_title_id)
        if identity[1]!=TITLE_ID_EUR or type(identity[0]) is not int:return None
        if identity!=self.identity:self.ready=False;self.identity=identity
        raw=b.read_memory(STATE,8)
        if raw is None or len(raw)!=8:return None
        if self.ready and struct.unpack_from('<I',raw)[0]:return raw.hex()
        self.ready=False
        owned=[]
        for hook,entry,prefix,original in SITES:
            if b.read_memory(hook-8,8)!=prefix:return None
            code=relay(hook,entry,original);live=b.read_memory(entry,len(code));jump=b.read_memory(hook,4)
            if jump not in (original,branch(hook,entry)) or live not in (bytes(len(code)),code):return None
            if jump!=original and live!=code:return None
            owned.append((hook,entry,original,code,live))
        # Une publication interrompue se complète ; une zone inconnue est refusée.
        if any(raw) and (not struct.unpack_from('<I',raw)[0] or not any(v==c for h,e,o,c,v in owned)):return None
        if not any(raw) and any(b.read_memory(h,4)!=o for h,e,o,c,v in owned):return None
        for hook,entry,original,code,live in owned:
            if live!=code and not (b.write_memory(entry,code) and b.read_memory(entry,len(code))==code):return None
        if not any(raw):
            raw=struct.pack('<2I',secrets.randbelow(0xFFFFFFFF)+1,secrets.randbits(32))
            if not b.write_memory(STATE,raw):return None
        for hook,entry,original,code,live in owned:
            jump=branch(hook,entry)
            if b.read_memory(hook,4)!=jump and not b.write_memory(hook,jump):return None
            if b.read_memory(hook,4)!=jump:return None
        self.ready=True
        raw=b.read_memory(STATE,8)
        return raw.hex() if raw else None
