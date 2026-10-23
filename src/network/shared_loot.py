"""Session-scoped resolved drops and exactly-once shared pickup claims.

Break outcomes include an empty list: a first outcome that drops nothing must
also win a simultaneous break. Claims survive room changes and reach players
outside the originating room. No native pointers are accepted on this wire.
"""
from copy import deepcopy
import math
from src.network.world_journal import integer,room_key,event_payload
from src.dynamic_enemies import KINDS as ENEMY_TYPES

MAX_BATCH=2
MAX_EVENTS=8192
MAX_PENDING=256
DROP_PARAMS={45:{(0,1,0,0)},63:{(1,n,0,0) for n in range(4)},
             107:{(1,4,0,0)},106:{(1,5,0,0)},64:{(1,0,0,0)},
             66:{(0,4,0,-1)},585:{(0,n,0,0) for n in range(2)},
             129:{(1,0,0,0)},131:{(1,1,0,0)},130:{(1,2,0,0)},
             526:{(0,-1,2,1)},529:{(0,-1,2,1)},531:{(0,-1,2,1)}}
REWARD_TYPES={45,63,64,66,106,107,129,130,131}

def vector(value):
    return (isinstance(value,list) and len(value)==3
            and all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in value))

def spawn_payload(value):
    if not isinstance(value,dict) or set(value)!={'type','position','auxiliary','yaw','params'}:return None
    kind=value['type'];params=value['params']
    if not integer(kind,0,639) or kind not in DROP_PARAMS:return None
    if (not isinstance(params,list) or len(params)!=4 or any(type(v) is not int for v in params)
            or tuple(params) not in DROP_PARAMS[kind]):return None
    if not vector(value['position']) or not vector(value['auxiliary']) or not integer(value['yaw'],0,0xFFFFFFFF):return None
    return deepcopy(value)

def payload(value):
    if not isinstance(value,dict):return None
    if value.get('kind') in ('pickup','enemy_dead'):
        if set(value)!={'kind','drop'}:return None
        drop=value['drop']
        if not isinstance(drop,list) or len(drop)!=2 or not integer(drop[0],1,MAX_EVENTS) or not integer(drop[1],0,7):return None
        return deepcopy(value)
    if set(value)!={'kind','room','epoch','cause_kind','target','drops'} or value['kind']!='break':return None
    if room_key(value['room']) is None:return None
    e=value['epoch']
    if not isinstance(e,str) or len(e)!=16 or any(c not in '0123456789abcdef' for c in e):return None
    target=value['target']
    if value['cause_kind']=='mass':
        if event_payload(dict(kind='mass_cut',target=target,effect=0)) is None:return None
    elif value['cause_kind']=='actor':
        if (not isinstance(target,list) or len(target)!=5 or not integer(target[0],0,639)
                or not integer(target[1],1,32767) or any(not integer(v,-10**10,10**10) for v in target[2:])):return None
    elif value['cause_kind']=='enemy':
        if (not isinstance(target,list) or len(target)!=3 or not integer(target[0],1,MAX_EVENTS)
                or not integer(target[1],0,7) or type(target[2]) is not int or target[2] not in ENEMY_TYPES):return None
    else:return None
    drops=value['drops']
    if not isinstance(drops,list) or len(drops)>8 or any(spawn_payload(d) is None for d in drops):return None
    return deepcopy(value)

def break_key(p):return (tuple(p['room']),p['epoch'],p['cause_kind'],tuple(p['target']))

class HostLootJournal:
    def __init__(self):self.events=[];self.acked={};self.breaks={};self.claims={};self.defeats={};self.error=''
    def _origin(self,drop):
        origin=self.events[drop[0]-1] if drop[0]<=len(self.events) else None
        if (origin is None or origin['payload']['kind']!='break'
                or drop[1]>=len(origin['payload']['drops'])):return None
        return origin['payload'],origin['payload']['drops'][drop[1]]
    def accept(self,pid,batch,contexts):
        if not isinstance(batch,list) or len(batch)>MAX_BATCH:return
        clean=[]
        for raw in batch:
            if not isinstance(raw,dict) or set(raw)!={'id','payload'} or not integer(raw['id'],1,2**53):return
            p=payload(raw['payload'])
            if p is None:return
            clean.append(dict(id=raw['id'],payload=p))
        for raw in clean:
            ack=self.acked.get(pid,0)
            if raw['id']<=ack:continue
            if raw['id']!=ack+1:break
            p=raw['payload'];keep=False
            if p['kind']=='break':
                # Reject expired rooms without allowing a stale event to block
                # this client's later valid input or create a new incarnation.
                live=contexts.get(room_key(p['room']),{}).get('epoch')==p['epoch']
                keep=live and break_key(p) not in self.breaks
                if p['cause_kind']=='enemy':
                    parent=self._origin(p['target'][:2])
                    keep=keep and parent is not None and parent[0]['room']==p['room'] and parent[0]['epoch']==p['epoch'] and parent[1]['type']==p['target'][2]
            else:
                origin=self._origin(p['drop'])
                allowed,ledger=(REWARD_TYPES,self.claims) if p['kind']=='pickup' else (ENEMY_TYPES,self.defeats)
                keep=(origin is not None and origin[1]['type'] in allowed and tuple(p['drop']) not in ledger)
            if keep:
                if len(self.events)>=MAX_EVENTS:self.error='loot_journal_full';break
                event=dict(revision=len(self.events)+1,source=pid,source_id=raw['id'],payload=p)
                self.events.append(event)
                if p['kind']=='break':self.breaks[break_key(p)]=event['revision']
                elif p['kind']=='pickup':self.claims[tuple(p['drop'])]=event['revision']
                else:self.defeats[tuple(p['drop'])]=event['revision']
            self.acked[pid]=raw['id']
    def delta(self,pid,cursor):
        if not integer(cursor,0,len(self.events)):return None
        return dict(acked=self.acked.get(pid,0),revision=len(self.events),
                    events=deepcopy(self.events[cursor:cursor+MAX_BATCH]),error=self.error)

class ClientLootJournal:
    def __init__(self):self.reset()
    def reset(self):
        self.sequence=0;self.cursor=0;self.pending=[];self.events=[];self.error=''
    def observe(self,p):
        p=payload(p)
        if p is None:return None
        if len(self.pending)>=MAX_PENDING:self.error='loot_pending_full';return None
        self.sequence+=1;self.pending.append(dict(id=self.sequence,payload=p));return self.sequence
    def outgoing(self):return dict(cursor=self.cursor,events=deepcopy(self.pending[:MAX_BATCH]))
    def receive(self,msg):
        if not isinstance(msg,dict):return False
        ack=msg.get('acked');revision=msg.get('revision');events=msg.get('events')
        if not integer(ack,0,self.sequence) or not integer(revision,self.cursor,MAX_EVENTS):return False
        if not isinstance(events,list) or len(events)>MAX_BATCH or msg.get('error','') not in ('','loot_journal_full'):return False
        clean=[];expected=self.cursor+1
        for e in events:
            if (not isinstance(e,dict) or set(e)!={'revision','source','source_id','payload'}
                    or e['revision']!=expected or not integer(e['revision'],1,revision)
                    or not integer(e['source_id'],1,2**53) or not isinstance(e['source'],str)
                    or not 1<=len(e['source'])<=64):return False
            p=payload(e['payload'])
            if p is None:return False
            clean.append(dict(e,payload=p));expected+=1
        # Receipt caches resolved state, not native completion. The game-side
        # ledger has its own nonce/commit barrier before granting any reward.
        self.events.extend(clean);self.cursor+=len(clean)
        self.pending=[e for e in self.pending if e['id']>ack]
        self.error=msg.get('error','');return True
