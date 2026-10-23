"""Petits datagrammes UDP ; assemblage borné des instantanés plus denses."""
import secrets
import struct
import zlib

MTU=1200
LIMIT=16384
HEADER=struct.Struct('!4s8sBBH')
PART=MTU-HEADER.size
COMPRESSED=b'ACZ1'
FRAGMENT=b'ACF1'


def encode(raw):
    if len(raw)>LIMIT:raise ValueError('Message trop volumineux.')
    if len(raw)<=MTU:return [raw]
    packed=COMPRESSED+zlib.compress(raw,3)
    if len(packed)<len(raw):raw=packed
    if len(raw)<=MTU:return [raw]
    ident=secrets.token_bytes(8);count=(len(raw)+PART-1)//PART
    return [HEADER.pack(FRAGMENT,ident,count,i,len(raw))+raw[i*PART:(i+1)*PART] for i in range(count)]


class Datagrams:
    def __init__(self):self.pending={}

    def receive(self,raw,endpoint,now):
        self.pending={key:row for key,row in self.pending.items() if now-row['created']<1.5}
        if not raw or len(raw)>LIMIT:return None
        if raw.startswith(FRAGMENT):
            if not HEADER.size<len(raw)<=MTU:return None
            _,ident,count,index,total=HEADER.unpack_from(raw)
            if not MTU<total<=LIMIT or count!=(total+PART-1)//PART or not 0<=index<count:return None
            body=raw[HEADER.size:]
            if len(body)!=min(PART,total-index*PART):return None
            key=(endpoint,ident);row=self.pending.get(key)
            if row is None:
                if len(self.pending)>=32:return None
                row=self.pending[key]=dict(created=now,count=count,total=total,parts={})
            if (row['count'],row['total'])!=(count,total):return None
            if index in row['parts'] and row['parts'][index]!=body:return None
            row['parts'][index]=body
            if len(row['parts'])!=count:return None
            raw=b''.join(row['parts'][i] for i in range(count));self.pending.pop(key,None)
        if raw.startswith(COMPRESSED):
            try:
                stream=zlib.decompressobj();raw=stream.decompress(raw[len(COMPRESSED):],LIMIT+1)
                if len(raw)>LIMIT or not stream.eof or stream.unused_data or stream.unconsumed_tail:return None
            except zlib.error:return None
        return raw
