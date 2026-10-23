"""Read-only EUR particle telemetry. Wire records contain no native addresses.

Only PlayerObject effects of this Link and banks owned by a real live
ObjSwordBeam, BombS, Arrow or HookShot of this Link are eligible. The renderer
resolves their names against its own local resources. No projectile actor,
collision or damage routine is exposed here.
"""
import re
import struct
import time

from src.memory.render_state import _heap, _integer, _number

# Sword, item readiness (WaitItem: bow drawn, hookshot ready) and item shots.
PLAYER_EFFECTS=frozenset(('SwordCut','RollCut','RollCutEX','Chargre','WaitItem','WaitItem1Time',
                          'BowShot','HookShot'))
BEAM_BANKS=frozenset(('SwordBeam','ObjSwordBeam'))
BOMB_EFFECTS=frozenset(('Bomb','LandWater','Plop','PlopWaterFall','ReactInvalid','SandFall','Spark','Valley'))
# Arrow impact/bounce and hookshot grab or refusal (EUR item probe, 21/10).
ARROW_EFFECTS=frozenset(('ReactInvalid','IntoLavaUpSide'))
HOOK_EFFECTS=frozenset(('ReactInvalid','Stick','grip','IntoLavaUpSide'))
# Banks owned by one live actor: kind -> (bank names, actor vtable).
OWNED_BANKS={1:(BEAM_BANKS,0x6D19A8),2:(frozenset(('ObjBombS',)),0x6ED19C),
             5:(frozenset(('ObjArrow',)),0x6ED014),6:(frozenset(('ObjHookShot',)),0x6CD7E4)}
# Kinds 3/4 are receiver-local Mass bursts; kind 1 accepts any beam name.
EFFECT_NAMES={0:PLAYER_EFFECTS,2:BOMB_EFFECTS,3:frozenset(('Break','IceBreak')),
              4:frozenset(('Break','IceBreak')),5:ARROW_EFFECTS,6:HOOK_EFFECTS}
BANK_KINDS=7
# An arrow or hook is this Link's when it starts at most this far from him;
# archers' arrows start at the archer.
SHOT_REACH=2.0
MAX_EFFECTS=8
EFFECT_FINGERPRINTS={
    0x4A1B74:bytes.fromhex('30402de90140a0e10050a0e10c0094e5'),
    0x4A290C:bytes.fromhex('f0412de90040a0e130d04de23c0090e5'),
    0x3E6DB4:bytes.fromhex('080a91ec672f80e2080a82ec202081e2'),
}


def _selector(value):
    return _integer(value,0,15) or type(value) is int and value==0xFFFFFFFF


def validate_effects(value):
    if not isinstance(value,list) or len(value)>MAX_EFFECTS:return None
    result=[];ids=set()
    for effect in value:
        if not isinstance(effect,dict):return None
        serial,bank,name=effect.get('id'),effect.get('bank'),effect.get('name')
        if not _integer(serial,1,0xFFFFFFFF) or serial in ids:return None
        if not _integer(bank,0,BANK_KINDS-1) or not isinstance(name,str):return None
        if bank==1 and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',name) is None:return None
        if bank!=1 and name not in EFFECT_NAMES[bank]:return None
        selectors=effect.get('selectors');matrix=effect.get('matrix')
        if not isinstance(selectors,list) or len(selectors)!=2 or not all(_selector(v) for v in selectors):return None
        if not isinstance(matrix,list) or len(matrix)!=12 or any(
                not _number(v,-1e6 if i%4==3 else -64,1e6 if i%4==3 else 64) for i,v in enumerate(matrix)):return None
        ids.add(serial)
        result.append(dict(id=serial,bank=bank,name=name,selectors=list(selectors),matrix=list(matrix)))
    return result


class AttackEffectReader:
    def __init__(self,bridge):
        self.bridge=bridge;self.identity=None;self.supported=False
        self._owned={};self._next_survey=0;self._player=None;self._shooters={}

    def _string(self,pointer):
        if type(pointer) is not int or not (0x100000<=pointer<0x7B0000-64
                or 0x08000000<=pointer<0x1F000000-64):return None
        raw=self.bridge.read_memory(pointer,64)
        if not raw or b'\0' not in raw:return None
        try:return raw.split(b'\0',1)[0].decode('ascii')
        except UnicodeDecodeError:return None

    def _beams(self,manager):
        return self._survey(manager).get(1,[])

    def _mine(self,kind,actor):
        """Bombs carry their origin (+6B4 byte, 0 = Link). Arrows and hooks are
        this Link's when they start near him, decided once per actor handle."""
        b=self.bridge
        if kind==2:return b.read_u8(actor+0x6B4)==0
        if kind not in (5,6):return True
        handle=b.read_u32(actor+0x2CC)
        if handle not in self._shooters:
            start=b.read_memory(actor+8,12);player=b.read_memory(self._player+0x3D0,12) if _heap(self._player) else None
            if not start or not player or len(start)!=12 or len(player)!=12:return False
            (x,_,z),(px,_,pz)=struct.unpack('<3f',start),struct.unpack('<3f',player)
            if len(self._shooters)>=64:self._shooters.clear()
            self._shooters[handle]=(x-px)**2+(z-pz)**2<=SHOT_REACH**2
        return self._shooters[handle]

    def _survey(self,manager):
        b=self.bridge;now=time.monotonic()
        if now<self._next_survey:return self._owned
        self._next_survey=now+.03;self._owned={}
        head=b.read_memory(manager,0x40) if _heap(manager) else None
        if not head or len(head)!=0x40:return {}
        node,count,offset=struct.unpack_from('<3I',head,8)
        if count>128 or offset>0x30:return {}
        for _ in range(min(count,128)):
            if not _heap(node) or node==manager+4:break
            wrapper=node-offset;raw=b.read_memory(wrapper,0x34)
            if not raw or len(raw)!=0x34:break
            name=self._string(struct.unpack_from('<I',raw,8)[0])
            kind=next((k for k,(names,_) in OWNED_BANKS.items() if name in names),0)
            if kind:
                vtable=OWNED_BANKS[kind][1]
                bank_node,bank_count,bank_offset=struct.unpack_from('<3I',raw,0x14)
                if bank_count<=8 and bank_offset<=0x58:
                    for _ in range(bank_count):
                        bank=bank_node-bank_offset
                        component=b.read_u32(bank+0x30) if _heap(bank) else None
                        actor=component-0x324 if _heap(component) else None
                        if (_heap(actor) and b.read_u32(actor)==vtable
                                and b.read_u32(actor+0x344)==bank
                                and b.read_u8(actor+0x2D7)==0 and self._mine(kind,actor)):
                            self._owned.setdefault(kind,[]).append(bank)
                        bank_node=b.read_u32(bank_node+4) if _heap(bank_node) else None
                        if bank_node==wrapper+0x10:break
            node=struct.unpack_from('<I',raw,offset+4)[0]
        return self._owned

    def _bank(self,bank,kind):
        b=self.bridge;raw=b.read_memory(bank,0x5C) if _heap(bank) else None
        if not raw or len(raw)!=0x5C:return []
        actor=handle=None
        if kind:
            component=struct.unpack_from('<I',raw,0x30)[0]
            actor=component-0x324 if _heap(component) else None
            if (not _heap(actor) or b.read_u32(actor)!=OWNED_BANKS[kind][1]
                    or b.read_u32(actor+0x344)!=bank or b.read_u8(actor+0x2D7)!=0
                    or not self._mine(kind,actor)):return []
            handle=b.read_u32(actor+0x2CC)
            if not handle:return []
        selectors=list(struct.unpack_from('<2I',raw,0x24))
        node,count,offset=struct.unpack_from('<3I',raw,0x48)
        if count>64 or offset>0x40 or not all(_selector(v) for v in selectors):return []
        wrapper=struct.unpack_from('<I',raw,0xC)[0]
        parser=b.read_u32(wrapper+0xC) if _heap(wrapper) else None
        header=b.read_u32(parser) if _heap(parser) else None
        table=b.read_u32(parser+0x14) if _heap(parser) else None
        names=b.read_u32(header+0xC) if _heap(header) else None
        if not _integer(names,1,512) or not _heap(table):return []
        result=[]
        for _ in range(count):
            if not _heap(node) or node==bank+0x44:break
            instance=node-offset;data=b.read_memory(instance,0x44)
            if not data or len(data)!=0x44:break
            word=lambda off:struct.unpack_from('<I',data,off)[0]
            entry,core=word(8),word(0x20)
            if (word(0x18)==bank and table<=entry<table+names*20 and (entry-table)%20==0
                    and _heap(core) and word(0x1C) and not word(0x3C)&8):
                name=self._string(b.read_u32(entry+4))
                live=b.read_memory(core,16);matrix=b.read_memory(core+0x19C,48)
                if (live and len(live)==16 and struct.unpack_from('<i',live,4)[0]>0
                        and struct.unpack_from('<I',live,12)[0]==word(0x24) and matrix and len(matrix)==48
                        and (kind==1 or name in EFFECT_NAMES[kind])):
                    item=dict(id=word(0x1C),bank=kind,name=name,selectors=selectors,
                              matrix=list(struct.unpack('<12f',matrix)))
                    if (validate_effects([item]) is not None and b.read_u32(instance+0x1C)==item['id']
                            and b.read_u32(instance+0x18)==bank and b.read_u32(instance+0x20)==core
                            and b.read_u32(core+0xC)==word(0x24)):result.append(item)
            node=word(offset+4)
        if actor and (b.read_u32(actor+0x2CC)!=handle or b.read_u8(actor+0x2D7)!=0
                or b.read_u32(actor+0x344)!=bank):return []
        return result

    def read(self,player):
        b=self.bridge;identity=(b.attached_pid,b.attached_title_id,player)
        if identity!=self.identity:
            self.identity=identity;self._owned={};self._shooters={};self._next_survey=0
            self.supported=all(b.read_memory(at,len(data))==data for at,data in EFFECT_FINGERPRINTS.items())
        if not self.supported:return []
        self._player=player
        bank=b.read_u32(player+0x344)
        if not _heap(bank):return []
        result=self._bank(bank,0)
        for kind,banks in sorted(self._survey(b.read_u32(bank+8)).items()):
            for owned in banks:result+=self._bank(owned,kind)
        # Native handle generations are globally unique until uint32 wrap.
        result=result[:MAX_EFFECTS]
        return result if b.read_u32(player+0x344)==bank and validate_effects(result) is not None else []
