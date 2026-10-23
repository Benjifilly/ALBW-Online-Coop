"""Clothing colours for the verified EUR FlatLink ETC1A4 textures.

The source bytes stay in the user's game. Only known clothing/head textures
are accepted; alpha, paper outlines, detail selectors and non-green colours
are retained. Texture layout: Azahar's video_core/texture/etc1.cpp and
texture_decode.cpp (azahar-emu/azahar).
"""
import hashlib
import struct

from src.memory.tunic_texture import etc1_bases
from src.memory.render_state import _heap

CLOTHING_TEXTURES=frozenset((
    'f17adcc1f4be68c4377b8dafd3514642d94d5f1fade32d70d1b2b01ccfc1b412',
    '79baa42fe6e21ebd1366915b6051ef2f3efafe481daa66d1b54ea075b406d245',
    'b79deb8d7fb85ca21be884976a9b58ded34621cb4e345f63273abe0a0b6dcc7a',
    '6c62bc6a6e7360433084ff7eea359fd0c74f63f43e2625781b185288b20d65c7',
    # Freshly loaded Front clothing/head resources, verified by decoding the
    # live local textures after a clean emulator restart.
    '443caafa43bb05a6fe3286ed274d360d854099dcaf646d9ea3ff2db90da79354',
    '4b6059ff8c18090058d0e3a4c8c36f41b7280980811d69e64aaa82cb9366c725',
    # Native local FlatLink atlases from the second test save. Decoded locally:
    # green clothing/cap, paper outline and face; no game pixels are shipped.
    '90cd09d69891c03cd4a8d70dd8ed593675e7189d064c943eb7066b954fa0b0ad',
    '142bf86f9233aa6d99faa983fe018217c6773cf67a6b8120c9b4af63409d2eb6',
    'e00793c84162ab50d3b2b8e8081bec8cab834735bc4d92ea6c90057cb4bcdc56',
    '8fca46797e15a480c5f97b1a6661a729f5526bf7016027d524cf2e01171904aa',
))
COLOURS=('green','blue','red','purple')


def recolour_clothing(data,colour):
    """Recolour the green pigment, preserving ETC1A4 size and alpha exactly."""
    if colour not in COLOURS or len(data)!=128*128:
        raise ValueError('Texture ou couleur de peinture non prise en charge.')
    if colour=='green':return bytes(data)
    result=bytearray(data)
    for offset in range(8,len(data),16):
        word=struct.unpack_from('<Q',data,offset)[0]
        original=etc1_bases(word);colours=[]
        for r,g,b in original:
            if g>r*1.14+8 and g>b*1.08+4:
                rgb={'blue':(b*.95,r,g*1.05),'red':(g*1.05,r*1.02,b*.97),
                     'purple':(g*.9,b*.9,g*1.06)}[colour]
                colours.append(tuple(max(0,min(255,round(v))) for v in rgb))
            else:colours.append((r,g,b))
        if colours==original:continue
        q5=[tuple(round(v*31/255) for v in rgb) for rgb in colours]
        deltas=[b-a for a,b in zip(*q5)]
        word &= ~(((1<<24)-1)<<40)
        if word&(1<<33) and all(-4<=v<=3 for v in deltas):
            for base,delta,shift in zip(q5[0],deltas,(56,48,40)):
                word |= ((base<<3)|(delta&7))<<shift
        else:
            word &= ~(1<<33)
            for rgb,shifts in zip(colours,((60,52,44),(56,48,40))):
                for value,shift in zip(rgb,shifts):word |= round(value/17)<<shift
        struct.pack_into('<Q',result,offset,word)
    return bytes(result)


def validated_clothing(data,colour):
    if hashlib.sha256(data).hexdigest() not in CLOTHING_TEXTURES:
        raise ValueError('Cette texture de peinture ne correspond pas au profil vérifié.')
    return recolour_clothing(data,colour)


class PrivatePaintingPalette:
    """Local material bindings and copied textures within the painting heap.

    The frame hook applies the bindings after material animation/calculation.
    Neither the original texture nor a source-player material is ever written.
    """
    def __init__(self,pool):
        self.pool=pool;self.bridge=pool.bridge
        self.originals={};self.textures={};self.commands={};self.tables={}

    def _read(self,at,size):
        data=self.bridge.read_memory(at,size)
        if data is None or len(data)!=size:raise RuntimeError('Palette murale illisible.')
        return data

    def _material_table(self,model):
        start,end=self.pool.heap,self.pool.allocation+0x200000
        if (type(model) is not int or not start<=model<end-0x178
                or self.bridge.read_u32(model+4)!=self.pool.heap):
            raise ValueError('La palette exige un modèle mural privé.')
        wrapper=self.bridge.read_u32(model+0x20)
        if type(wrapper) is not int or not start<=wrapper<=end-8:raise ValueError('Matériaux hors de la réserve privée.')
        table,count=struct.unpack('<2I',self._read(wrapper,8))
        if count not in (10,11) or not start<=table<=end-count*0x154:
            raise ValueError('Matériaux de peinture non reconnus.')
        return table,count

    def _sources(self,model):
        if model in self.originals:return self.originals[model]
        table,count=self._material_table(model)
        sources=[]
        for index in range(count):
            for offset,registers in ((0x13C,(0x82,0x84,0x85,0x8E)),
                                     (0x144,(0x92,0x94,0x95,0x96)),
                                     (0x14C,(0x9A,0x9C,0x9D,0x9E))):
                field=table+index*0x154+offset
                command=self.bridge.read_u32(field)
                if not _heap(command):continue
                data=self._read(command,48)
                values=struct.unpack('<12I',data)
                dim,h0,lod,h1,address,h2,fmt,h3=values[:8]
                if (dim,h0,lod,h1,h2,fmt,h3)!=(0x800080,0xF0000|registers[0],0,
                        0x40000|registers[1],0xF0000|registers[2],13,0xF0000|registers[3]):continue
                physical=address<<3
                if 0x20000000<=physical<=0x28000000-0x4000:virtual=physical-0xC000000
                elif 0x18000000<=physical<=0x18600000-0x4000:virtual=physical+0x7000000
                else:continue
                texture=self._read(virtual,0x4000)
                digest=hashlib.sha256(texture).hexdigest()
                if digest not in CLOTHING_TEXTURES:continue
                sources.append((field,command,data,texture,digest))
        if not sources:raise ValueError('Les textures de cette peinture ne sont pas reconnues.')
        self.originals[model]=sources
        return sources

    def table_for(self,model,colour):
        if colour not in COLOURS:raise ValueError('Couleur murale inconnue.')
        if (model,colour) in self.tables:return self.tables[model,colour]
        entries=[]
        for field,command,data,texture,digest in self._sources(model):
            if colour=='green':target=command
            else:
                if (digest,colour) not in self.textures:
                    address=(self.pool._alloc(len(texture)+127)+127)&~127
                    self.pool._write(address,recolour_clothing(texture,colour))
                    self.textures[digest,colour]=address
                if (command,colour) not in self.commands:
                    address=(self.pool._alloc(48+15)+15)&~15
                    copied=bytearray(data)
                    struct.pack_into('<I',copied,16,(self.textures[digest,colour]+0xC000000)>>3)
                    self.pool._write(address,copied)
                    self.commands[command,colour]=address
                target=self.commands[command,colour]
            entries.append((field,target))
        # Pointers are local products of checked allocations, never packet data.
        descriptor=struct.pack('<I',len(entries))+b''.join(struct.pack('<2I',*v) for v in entries)
        address=self.pool._alloc(len(descriptor));self.pool._write(address,descriptor)
        self.tables[model,colour]=address
        return address
