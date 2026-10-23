"""Native hit evidence: sword records matched to their exact contact, and the
consumed record of any other weapon (bomb, arrow, thrown object...).

This is local evidence, NOT a network command or an authority decision. A
successful deposit alone never qualifies. The output strips native pointers,
handles and opaque HitInfo fields. Only the explicit experimental relay may
convert this evidence into a closed wire event; it is disabled by default.
"""
from collections import OrderedDict
import math
import struct

from src.memory.enemy_impact_trial import SWORD_DESC
from src.combat_profiles import sword_target,attack_profile,ATTACK_PROFILES
from src.dynamic_enemies import KINDS

LIMIT=128
WINDOW=8


def integer(value,low,high):
    return type(value) is int and low<=value<=high


def room(value):
    return (isinstance(value,(list,tuple)) and len(value)==2
            and integer(value[0],0,23) and integer(value[1],0,65535))


def vector(value):
    return (isinstance(value,(list,tuple)) and len(value)==3
            and all(type(x) in (int,float) and math.isfinite(x) and abs(x)<=1e6 for x in value))


def blob(value,size):
    if not isinstance(value,str) or len(value)!=size*2:return None
    try:return bytes.fromhex(value)
    except ValueError:return None


class CombatEvidence:
    def __init__(self,player_handle,dynamic_key=None):
        if not integer(player_handle,1,0xFFFFFFFF):raise ValueError('Invalid local author')
        self.player_handle=player_handle
        self.contacts=OrderedDict();self.damage=OrderedDict();self.finished=OrderedDict()
        self.latest=None;self.expired=0;self.ambiguous=0;self.direct=[]
        self.dynamic_key=dynamic_key or (lambda row:None)

    def _advance(self,frame):
        if self.latest is None or 0<(frame-self.latest)&0xFFFFFFFF<0x80000000:
            self.latest=frame

    def _trim(self):
        for collection in (self.contacts,self.damage):
            for key,row in list(collection.items()):
                age=(self.latest-row['frame'])&0xFFFFFFFF
                if WINDOW<age<0x80000000:
                    del collection[key];self.expired+=1
            while len(collection)>LIMIT:
                collection.popitem(last=False);self.expired+=1
        while len(self.finished)>LIMIT:self.finished.popitem(last=False)

    @staticmethod
    def _identity(row,serial):
        if (not isinstance(row,dict) or not room(row.get('room'))
                or sword_target(row.get('type')) is None
                or not (integer(row.get('placement_id'),1,32767)
                        or row.get('placement_id')==-1 and row.get('type') in KINDS)
                or not integer(row.get('handle'),1,0xFFFFFFFF)
                or not integer(row.get('frame'),0,0xFFFFFFFF)
                or not integer(row.get(serial),1,0xFFFFFFFF)):
            return None
        return (tuple(row['room']),row['frame'],row['handle'],row[serial])

    def _contact(self,row):
        key=self._identity(row,'contact_serial')
        if key is None or row.get('capture_abi') not in (3,4) or row.get('deposit_result')!=1:return
        if row.get('vtable')!=sword_target(row['type']).vtable or not vector(row.get('current_position')):return
        info=blob(row.get('hit_info'),72);desc=blob(row.get('attack_desc'),24)
        candidate=blob(row.get('candidate_record'),72)
        if info is None or desc!=SWORD_DESC or candidate is None:return
        word=lambda data,offset:struct.unpack_from('<I',data,offset)[0]
        if (word(info,0)!=0x6F0F80 or word(info,0x28)!=self.player_handle
                or word(info,0x30)!=0x04000000 or word(info,0x34)!=0
                or word(candidate,0)!=0x20 or word(candidate,4)!=self.player_handle
                or candidate[8:0x20]!=info[4:0x1C]
                or candidate[0x20:0x38]!=desc
                or candidate[0x38:0x3C]!=info[0x30:0x34]
                or candidate[0x3C:]!=info[0x3C:]):return
        geometry=[list(struct.unpack_from('<3f',candidate,at)) for at in (8,0x14,0x3C)]
        if not all(vector(v) for v in geometry):return
        self.contacts[key]=dict(frame=row['frame'],row=row,record=candidate,geometry=geometry)
        self._advance(row['frame'])

    def _native_damage(self,key,row,record):
        """Any other weapon: the consumed record is itself the native evidence.

        The game already subtracted this damage (4F73F8). Bombs, arrows,
        thrown objects... have no player-authored sword contact to match.
        """
        maximum=sword_target(row['type']).max_hp
        if (struct.unpack_from('<I',record,4)[0]==0
                or not integer(row.get('damage'),1,65535)
                or not integer(row.get('hp_before'),1,maximum)
                or row.get('max_hp')!=maximum
                or not vector(row.get('initial_position'))):return None
        dynamic=self.dynamic_key(row) if row['placement_id']==-1 else None
        if row['placement_id']==-1 and dynamic is None:return None
        index=attack_profile(record[0x20:0x38])
        receipt=dict(evidence_kind='native_damage',room=list(row['room']),
            target=[row['type'],row['placement_id'],*(round(v*10000) for v in row['initial_position'])],
            profile=ATTACK_PROFILES[index].name if index is not None else 'unknown',
            native_serial=row['serial'],native_frame=row['frame'],native_damage=row['damage'],
            hp_before=row['hp_before'],max_hp=row['max_hp'])
        if dynamic:receipt['dynamic_key']=dynamic
        self.finished[key]=True
        return receipt

    def _damage(self,row):
        key=self._identity(row,'serial')
        if key is None or key in self.finished:return
        record=blob(row.get('attack_record'),72)
        if record is not None and record[0x20:0x38]!=SWORD_DESC:
            receipt=self._native_damage(key,row,record)
            if receipt:self.direct.append(receipt)
            return
        maximum=sword_target(row['type']).max_hp
        if (record is None or record[0x20:0x38]!=SWORD_DESC
                or struct.unpack_from('<2I',record)!=(0x20,self.player_handle)
                or not integer(row.get('damage'),1,65535)
                or not integer(row.get('hp_before'),1,maximum)
                or row.get('max_hp')!=maximum
                or not vector(row.get('initial_position'))):return
        self.damage[key]=dict(frame=row['frame'],row=row,record=record)
        self._advance(row['frame'])

    def ingest(self,contacts,damage):
        for row in contacts:self._contact(row)
        self.direct=[]
        for row in damage:self._damage(row)
        results=self.direct;self.direct=[]
        for key,pending in list(self.damage.items()):
            event=pending['row']
            dynamic=self.dynamic_key(event) if event['placement_id']==-1 else None
            if event['placement_id']==-1 and dynamic is None:continue
            matches=[c for ck,c in self.contacts.items() if ck[:3]==key[:3]
                     and c['record']==pending['record']
                     and c['row']['type']==event['type']
                     and c['row']['placement_id']==event['placement_id']]
            if not matches:continue
            # Two candidate contacts with the same bytes have no proven unique
            # cause; do not invent an attack number from their ordering.
            if len(matches)!=1:
                self.ambiguous+=1
            else:
                contact=matches[0]
                results.append(dict(evidence_kind='matched_sword_hit',room=list(event['room']),
                    target=[event['type'],event['placement_id'],
                            *(round(v*10000) for v in event['initial_position'])],
                    profile='standard_sword',native_serial=event['serial'],native_frame=event['frame'],
                    contact_serial=contact['row']['contact_serial'],
                    victim_position=list(contact['row']['current_position']),
                    vectors=contact['geometry'],native_damage=event['damage'],
                    hp_before=event['hp_before'],max_hp=event['max_hp']))
                if dynamic:results[-1]['dynamic_key']=dynamic
            del self.damage[key];self.finished[key]=True
        # A slow frame can deliver several complete pairs in one native batch.
        # Expire only unmatched evidence afterwards; dropping older complete
        # pairs here would silently lose legitimate consumed strikes.
        self._trim()
        return results
