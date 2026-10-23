"""Session-scoped shared progress: game flags, item grants and the wallet, in host order.

Flags carry the native setFlag arguments (kind 1..4, index, value) and the
course whose block holds them; kind 1 is also bound to its room. Items carry
the native grant id and the course it was received in. Small keys (item 1)
stay with the player who found them and never enter this journal. No native
pointer is accepted on this wire.
"""
from copy import deepcopy
from src.network.world_journal import integer,room_key

MAX_BATCH=8
MAX_EVENTS=8192
MAX_PENDING=512
SMALL_KEY=1
ITEM_LIMIT=0x61
WALLET_MAX=9999


def payload(value):
    if not isinstance(value,dict):return None
    if value.get('kind')=='flag':
        if set(value)!={'kind','flag','course','room'}:return None
        flag=value['flag']
        if (not isinstance(flag,list) or len(flag)!=3 or not integer(flag[0],1,4)
                or not integer(flag[1],1,0xFFFF) or not integer(flag[2],0,1)):return None
        if not integer(value['course'],0,63) or room_key(value['room']) is None:return None
        return deepcopy(value)
    if value.get('kind')=='item':
        if set(value)!={'kind','item','course'}:return None
        if not integer(value['item'],SMALL_KEY+1,ITEM_LIMIT-1) or not integer(value['course'],0,63):return None
        return deepcopy(value)
    # One wallet for everyone: rupees spent in one game, and the host's wallet
    # that every game aligns to once per session. Gains are already shared
    # (loot rights, item grants).
    if value.get('kind')=='spend':
        if set(value)!={'kind','amount'} or not integer(value['amount'],1,WALLET_MAX):return None
        return deepcopy(value)
    if value.get('kind')=='wallet':
        if set(value)!={'kind','value'} or not integer(value['value'],0,WALLET_MAX):return None
        return deepcopy(value)
    return None


def flag_key(p):
    """Where a flag lives: kind 4 is global, 2/3 per course, 1 per room."""
    kind,index,_=p['flag']
    if kind==4:return (4,index)
    if kind==1:return (1,index,*p['room'])
    return (kind,index,p['course'])


class HostProgressJournal:
    def __init__(self):self.events=[];self.acked={};self.flags={};self.error=''

    def accept(self,pid,batch):
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
            p=raw['payload'];keep=True
            if p['kind']=='flag':
                # Both games may report the same change: only a new shared value is news.
                key=flag_key(p);keep=self.flags.get(key)!=p['flag'][2]
                if keep:self.flags[key]=p['flag'][2]
            if keep:
                if len(self.events)>=MAX_EVENTS:self.error='progress_journal_full';break
                self.events.append(dict(revision=len(self.events)+1,source=pid,source_id=raw['id'],payload=p))
            self.acked[pid]=raw['id']

    def delta(self,pid,cursor):
        if not integer(cursor,0,len(self.events)):return None
        return dict(acked=self.acked.get(pid,0),revision=len(self.events),
                    events=deepcopy(self.events[cursor:cursor+MAX_BATCH]),error=self.error)


class ClientProgressJournal:
    def __init__(self):self.reset()

    def reset(self):
        self.sequence=0;self.cursor=0;self.pending=[];self.events=[];self.error=''

    def observe(self,p):
        p=payload(p)
        if p is None:return None
        if len(self.pending)>=MAX_PENDING:self.error='progress_pending_full';return None
        self.sequence+=1;self.pending.append(dict(id=self.sequence,payload=p));return self.sequence

    def outgoing(self):return dict(cursor=self.cursor,events=deepcopy(self.pending[:MAX_BATCH]))

    def receive(self,msg):
        if not isinstance(msg,dict):return False
        ack=msg.get('acked');revision=msg.get('revision');events=msg.get('events')
        if not integer(ack,0,self.sequence) or not integer(revision,self.cursor,MAX_EVENTS):return False
        if not isinstance(events,list) or len(events)>MAX_BATCH or msg.get('error','') not in ('','progress_journal_full'):return False
        clean=[];expected=self.cursor+1
        for e in events:
            if (not isinstance(e,dict) or set(e)!={'revision','source','source_id','payload'}
                    or e['revision']!=expected or not integer(e['revision'],1,revision)
                    or not integer(e['source_id'],1,2**53) or not isinstance(e['source'],str)
                    or not 1<=len(e['source'])<=64):return False
            p=payload(e['payload'])
            if p is None:return False
            clean.append(dict(e,payload=p));expected+=1
        self.events.extend(clean);self.cursor+=len(clean)
        self.pending=[e for e in self.pending if e['id']>ack]
        self.error=msg.get('error','');return True
