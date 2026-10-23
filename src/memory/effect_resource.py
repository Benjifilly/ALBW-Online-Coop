"""Read a bounded, locally registered EFLK bank; never change the archive."""
import struct
from src.memory.attack_effects import AttackEffectReader
from src.memory.render_state import _heap


def parser_prefix(header,counts):
    _,particles,aux,names,_,strings,selectors=counts
    definitions=header+28
    bindings=definitions+particles*104
    name_table=bindings+particles*selectors*4
    extra=name_table+names*20
    timelines=extra+aux*60
    return struct.pack('<8I',header,header,definitions,bindings,extra,name_table,timelines,header+strings)


def find_bank(bridge,manager,name):
    registry=bridge.read_u32(manager+0x2C)
    if not _heap(registry):raise ValueError('Registre EFLK absent.')
    count=bridge.read_u32(registry+4);table=bridge.read_u32(registry+8)
    if type(count) is not int or not 0<count<=64 or not _heap(table):raise ValueError('Registre EFLK invalide.')
    reader=AttackEffectReader(bridge)
    for i in range(count):
        archive=bridge.read_u32(table+i*4)
        raw=bridge.read_memory(archive,16) if _heap(archive) else None
        if not raw or len(raw)!=16 or raw[:4]!=b'eflk':continue
        version,entries,strings=struct.unpack_from('<3I',raw,4)
        if version!=9 or not 0<entries<=4096 or not 16+entries*8<=strings<=0x800000:continue
        raw_entries=bridge.read_memory(archive+16,entries*8)
        if not raw_entries or len(raw_entries)!=entries*8:continue
        records=list(struct.iter_unpack('<2I',raw_entries))
        for offset,label in records:
            if label>0x100000 or reader._string(archive+strings+label)!=name:continue
            boundaries=[at for at,_ in records if at>offset]
            if strings>offset:boundaries.append(strings)
            if not boundaries:raise ValueError('Fin de banque EFLK inconnue.')
            size=min(boundaries)-offset;header=archive+offset
            if not 28<=size<=0x40000:raise ValueError('Taille de banque EFLK hors budget.')
            data=bridge.read_memory(header,size)
            if not data or len(data)!=size:raise ValueError('Banque EFLK incomplète.')
            counts=struct.unpack_from('<7I',data)
            if (counts[0]!=9 or any(v>512 for v in counts[1:5]) or counts[6]>64
                    or not 28<=counts[5]<size):raise ValueError('Entête EFLK hors profil.')
            prefix=parser_prefix(header,counts)
            name_table=struct.unpack_from('<I',prefix,20)[0]
            if name_table-header+counts[3]*20>counts[5]:raise ValueError('Tables EFLK hors du blob.')
            names=set();parsed=True
            for j in range(counts[3]):
                at=name_table-header+j*20
                relative,pointer=struct.unpack_from('<2I',data,at)
                if counts[5]+relative>=size:raise ValueError('Nom EFLK hors de sa banque.')
                text=data[counts[5]+relative:].split(b'\0',1)[0]
                if not text or len(text)>63:raise ValueError('Nom EFLK inattendu.')
                names.add(text.decode('ascii'))
                parsed=parsed and pointer==header+counts[5]+relative
            return dict(header=header,counts=counts,names=names,prefix=prefix,
                        unparsed=None if parsed else data)
    raise ValueError('Banque EFLK non enregistrée : '+name)
