"""Bounded, room-scoped reliable events over the lobby's lossy transport.

The host orders native observations; local addresses never cross the wire.
This journal is intentionally not an API for arbitrary memory or save writes.
"""
from copy import deepcopy
import secrets
from src.mass_families import mass_profile,actor_prop

MAX_BATCH=8
MAX_EVENTS=2048
MAX_PENDING=256


def integer(value,low,high):return type(value) is int and low<=value<=high


def room_key(room):
    if not isinstance(room,(list,tuple)) or len(room)!=2:return None
    if not integer(room[0],0,23) or not integer(room[1],0,65535):return None
    return f'{room[0]}:{room[1]}'


def event_payload(value):
    if not isinstance(value,dict):return None
    if set(value)!={'kind','target','effect'}:return None
    target=value['target']
    if value['kind']=='prop_state':
        # Actor prop: [type, placement, x, y, z]; effect = from<<4 | to.
        if not isinstance(target,list) or len(target)!=5:return None
        prop=actor_prop(target[0]);effect=value['effect']
        if prop is None or not integer(target[1],1,32767) or not integer(effect,0,0xFF):return None
        # The source may be 0: the idle state every sign starts in.
        if effect&15 not in prop.states:return None
        if any(not integer(v,-10**10,10**10) for v in target[2:]):return None
        return deepcopy(value)
    if value['kind']!='mass_cut' or not integer(value['effect'],0,4):return None
    # type, batch placement, batch initial xyz, instance index, instance xyz.
    if not isinstance(target,list) or len(target)!=9:return None
    if mass_profile(target[0]) is None or not integer(target[1],1,32767) or not integer(target[5],0,4095):return None
    if any(not integer(target[i],-10**10,10**10) for i in (2,3,4,6,7,8)):return None
    return deepcopy(value)


class HostWorldJournal:
    def __init__(self):self.rooms={}

    def update_rooms(self,players):
        active={room_key((p['course'],p['stage'])) for p in players if p['in_game']}
        self.rooms={key:value for key,value in self.rooms.items() if key in active}
        for key in active:
            self.rooms.setdefault(key,dict(epoch=secrets.token_hex(8),events=[],acked={},seen={},error=''))

    def contexts(self):
        return {key:dict(epoch=value['epoch'],revision=len(value['events']),error=value['error'])
                for key,value in self.rooms.items()}

    def accept(self,pid,room,epoch,batch):
        state=self.rooms.get(room_key(room))
        if state is None or epoch!=state['epoch'] or not isinstance(batch,list) or len(batch)>MAX_BATCH:return
        # Reject malformed batches as a unit; an invalid event cannot skip a gap.
        clean=[]
        for raw in batch:
            if not isinstance(raw,dict) or set(raw)!={'id','payload'} or not integer(raw['id'],1,2**53):return
            payload=event_payload(raw['payload'])
            if payload is None:return
            clean.append(dict(id=raw['id'],payload=payload))
        for event in clean:
            ack=state['acked'].get(pid,0)
            if event['id']<=ack:continue
            if event['id']!=ack+1:break
            payload=event['payload']
            if payload['kind']=='prop_state':
                # A prop toggles as often as it likes (a torch lit, put out, lit
                # again, 23/10): only a change to the state it is already in is
                # the other game reporting the same hit.
                prop=tuple(payload['target']);to=payload['effect']&15
                fresh=state.setdefault('props',{}).get(prop)!=to
                if fresh:state['props'][prop]=to
                semantic=(payload['kind'],*payload['target'],payload['effect'],len(state['events'])) if fresh else None
            else:semantic=('mass_cut',*payload['target'])
            if semantic is not None and semantic not in state['seen']:
                if len(state['events'])>=MAX_EVENTS:
                    state['error']='journal_full';break
                revision=len(state['events'])+1
                state['events'].append(dict(revision=revision,source=pid,source_id=event['id'],payload=payload))
                state['seen'][semantic]=revision
            state['acked'][pid]=event['id']

    def delta(self,pid,room,epoch,cursor):
        state=self.rooms.get(room_key(room))
        if state is None or state['epoch']!=epoch or not integer(cursor,0,len(state['events'])):return None
        return dict(room=list(room),epoch=epoch,acked=state['acked'].get(pid,0),
                    revision=len(state['events']),events=deepcopy(state['events'][cursor:cursor+MAX_BATCH]),error=state['error'])


class ClientWorldJournal:
    """One native scene at a time. Keep a delivery until native acknowledgement."""
    def __init__(self):self.reset()

    def reset(self):
        self._sources={}
        self._cuts={}
        self._clear_view()

    def _clear_view(self):
        self.context=None;self.cursor=0;self.sequence=0;self.pending=[];self.delivery=[];self.error=''

    def select(self,room,epoch,*,active_contexts=None):
        # Source IDs belong to a room epoch, not to the currently rendered
        # scene. The host may keep that epoch alive while this player leaves.
        # Preserve unsent input and its counter, but replay native history from
        # cursor0 on return because the game's grass has regrown on loading.
        valid=(room_key(room) is not None and isinstance(epoch,str) and len(epoch)==16)
        context=(tuple(room),epoch) if valid else None
        if self.context is not None:
            self._sources[self.context]=dict(sequence=self.sequence,pending=deepcopy(self.pending),error=self.error)
        if active_contexts is not None:
            self._sources={k:v for k,v in self._sources.items()
                           if active_contexts.get(room_key(k[0]),{}).get('epoch')==k[1]}
            self._cuts={k:v for k,v in self._cuts.items()
                        if active_contexts.get(room_key(k[0]),{}).get('epoch')==k[1]}
        if context==self.context:return
        self._clear_view()
        if context is None:return
        self._sources={k:v for k,v in self._sources.items() if k[0]!=context[0] or k[1]==context[1]}
        self._cuts={k:v for k,v in self._cuts.items() if k[0]!=context[0] or k[1]==context[1]}
        if context not in self._sources and len(self._sources)>=64:
            self.error='room_cache_full';return
        saved=self._sources.get(context,{})
        self.context=context
        self.sequence=saved.get('sequence',0)
        self.pending=deepcopy(saved.get('pending',[]))
        self.error=saved.get('error','')

    def observe(self,payload):
        clean=event_payload(payload)
        if self.context is None or clean is None:return False
        if len(self.pending)>=MAX_PENDING:self.error='pending_full';return False
        self.sequence+=1;self.pending.append(dict(id=self.sequence,payload=clean))
        self._remember_cut(clean)
        return True

    def _remember_cut(self,payload):
        if payload['kind']=='mass_cut' and self.context is not None:
            self._cuts.setdefault(self.context,{})[tuple(payload['target'])]=True

    def mass_manifest(self):
        """Known terminal states, including rooms outside the current view.

        This cache is independent of delivery cursors: a native initializer
        needs the state before an arrival can consume its ordered journal.
        """
        return [(room,target) for (room,epoch),cuts in self._cuts.items() for target in cuts]

    def outgoing(self):
        if self.context is None:return None
        room,epoch=self.context
        return dict(room=list(room),epoch=epoch,cursor=self.cursor,events=deepcopy(self.pending[:MAX_BATCH]))

    def receive(self,message):
        if self.context is None or not isinstance(message,dict):return False
        room,epoch=self.context
        if message.get('room')!=list(room) or message.get('epoch')!=epoch:return False
        ack=message.get('acked');revision=message.get('revision');events=message.get('events')
        if not integer(ack,0,self.sequence) or not integer(revision,self.cursor,MAX_EVENTS):return False
        if not isinstance(events,list) or len(events)>MAX_BATCH:return False
        clean=[];expected=self.cursor+1
        for raw in events:
            if not isinstance(raw,dict) or set(raw)!={'revision','source','source_id','payload'}:return False
            if not integer(raw['revision'],1,revision) or not integer(raw['source_id'],1,2**53):return False
            if not isinstance(raw['source'],str) or not 1<=len(raw['source'])<=64:return False
            payload=event_payload(raw['payload'])
            if payload is None:return False
            if raw['revision']<=self.cursor:continue
            if raw['revision']!=expected:return False
            expected+=1;clean.append(dict(raw,payload=payload))
        if message.get('error','') not in ('','journal_full'):return False
        # Never append retries to an unacknowledged native delivery.
        overlap=min(len(self.delivery),len(clean))
        if self.delivery[:overlap]!=clean[:overlap]:return False
        if len(clean)>len(self.delivery):self.delivery=clean
        self.pending=[event for event in self.pending if event['id']>ack]
        for event in clean:self._remember_cut(event['payload'])
        self.error=message.get('error','');return True

    def next_event(self):return deepcopy(self.delivery[0]) if self.delivery else None

    def acknowledge(self,revision):
        if not self.delivery or self.delivery[0]['revision']!=revision:return False
        self.cursor=revision;self.delivery.pop(0);return True
