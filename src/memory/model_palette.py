"""Per-model violet texture, built only from the user's live game resources.

The ARM renderer allocates separate linear kernel storage. Original blue textures
remain intact, so a blue Link and a violet Link can coexist in the same view.
"""
import hashlib
import struct
from src.memory.remote_model import PALETTE_SIZE
from src.memory.tunic_texture import (BLUE_TEXTURE_SHA256, locate_blue_texture,
                                     violet_texture, _heap)


class ModelPalette:
    def __init__(self,bridge):
        self.bridge=bridge
        self.buffer=0
        self.original=0
        self.bindings={}
        self.pid=bridge.attached_pid

    def prepare(self,allocation,local_model):
        if not 0x14000000<=allocation<=0x1C000000-PALETTE_SIZE:
            return False
        buffer=(allocation+127)&~127
        animation=self.bridge.read_u32(local_model+0x18)
        state=self.bridge.read_u32(animation+0x14) if _heap(animation) else 0
        resource=self.bridge.read_u32(state+4) if _heap(state) else 0
        if not _heap(resource):
            return False
        if resource==buffer:
            resource=self.bridge.read_u32(buffer+0x188)
        location=locate_blue_texture(self.bridge,resource)
        if location is None:
            return False
        address,length=location
        texture=self.bridge.read_memory(address,length)
        if texture is None or hashlib.sha256(texture).hexdigest()!=BLUE_TEXTURE_SHA256:
            return False
        header=self.bridge.read_memory(resource,24)
        if not header or len(header)!=24:
            return False
        raw,model,bindings,count,table,textures=struct.unpack('<6I',header)
        if count!=1 or textures!=3 or not _heap(table) or not _heap(bindings):
            return False
        entries=self.bridge.read_memory(table,12)
        track=self.bridge.read_memory(bindings,4)
        if not entries or not track:
            return False
        green,blue,red=struct.unpack('<3I',entries)
        descriptor=self.bridge.read_memory(blue,32) if _heap(blue) else None
        if not descriptor or len(descriptor)!=32:
            return False
        result=bytearray(0xAC80)
        struct.pack_into('<6I',result,0,raw,model,buffer+0x20,1,buffer+0x30,3)
        result[0x20:0x24]=track
        struct.pack_into('<3I',result,0x30,green,buffer+0x50,red)
        result[0x50:0x70]=descriptor
        for offset,slot,registers in zip((0x70,0xA0,0xD0),(0,8,16),
                ((0x82,0x84,0x85,0x8E),(0x92,0x94,0x95,0x96),(0x9A,0x9C,0x9D,0x9E))):
            pointer=struct.unpack_from('<I',descriptor,slot)[0]
            command=self.bridge.read_memory(pointer,48)
            if command is None or len(command)!=48:
                return False
            values=struct.unpack('<12I',command)
            expected=(0x800100,0xF0000|registers[0],0x40000,0x40000|registers[1],
                (address-0x7000000)>>3,0xF0000|registers[2],13,0xF0000|registers[3])
            if values[:8]!=expected:
                return False
            result[offset:offset+48]=command
            struct.pack_into('<I',result,0x50+slot,buffer+offset)
            struct.pack_into('<I',result,offset+16,(buffer+0x200+0xC000000)>>3)
        result[0x180:0x188]=b'COOPPAL1'
        struct.pack_into('<3I',result,0x188,resource,address,length)
        result[0x194:0x1B4]=hashlib.sha256(texture).digest()
        result[0x200:]=violet_texture(texture)
        current=self.bridge.read_memory(buffer,len(result))
        if current!=result:
            if not self.bridge.write_memory(buffer,bytes(result)):
                return False
            if self.bridge.read_memory(buffer,len(result))!=result:
                return False
        self.buffer,self.original,self.pid=buffer,resource,self.bridge.attached_pid
        return True

    def set_colour(self,model,colour):
        if not self.buffer or self.bridge.attached_pid!=self.pid or self.bridge.read_u32(model)!=0x6DA424:
            return False
        animation=self.bridge.read_u32(model+0x18)
        state=self.bridge.read_u32(animation+0x14) if _heap(animation) else 0
        if not _heap(state):
            return False
        current=self.bridge.read_u32(state+4)
        if current not in (self.original,self.buffer):
            return False
        target=self.buffer if colour=='purple' else self.original
        if current!=target:
            # Record the state first, including the lost-acknowledgement case.
            self.bindings[state]=model
            if not self.bridge.write_u32(state+4,target):
                return False
            if not self.bridge.write_u8(state+0x26,1):
                return False
        return True

    def restore(self):
        if self.bridge.attached_pid!=self.pid:
            return not self.bindings
        ok=True
        for state,model in list(self.bindings.items()):
            # An unloaded model's memory belongs to its new owner; leave it alone.
            animation=self.bridge.read_u32(model+0x18) if self.bridge.read_u32(model)==0x6DA424 else 0
            live_state=self.bridge.read_u32(animation+0x14) if _heap(animation) else 0
            if live_state!=state:
                del self.bindings[state]
                continue
            current=self.bridge.read_u32(state+4)
            if current==self.original:
                del self.bindings[state]
            elif current==self.buffer:
                if self.bridge.write_u32(state+4,self.original) and self.bridge.write_u8(state+0x26,1):
                    del self.bindings[state]
                else:ok=False
            else:ok=False
        return ok
