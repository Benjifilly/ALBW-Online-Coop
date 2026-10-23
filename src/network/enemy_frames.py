"""Bounded enemy snapshots. Native addresses never cross the network."""
from copy import deepcopy
import math
import time
from src.combat_profiles import sword_target,ATTACK_PROFILES

LIMIT=64

def integer(value,low,high):return type(value) is int and low<=value<=high

def profile_ok(value):
    """Optional attack profile: an index of the closed local catalogue (0 = sword)."""
    return integer(value,1,len(ATTACK_PROFILES)-1)

def valid_key(key,room):
    from src.dynamic_enemies import parse_key
    if parse_key(key,room) is not None:return True
    if not isinstance(key,str) or len(key)>120:return False
    try:parts=[int(v) for v in key.split(':')]
    except ValueError:return False
    return (len(parts)==7 and parts[:2]==list(room) and ':'.join(map(str,parts))==key
            and sword_target(parts[2]) is not None and 1<=parts[3]<=32767
            and all(abs(v)<=10**10 for v in parts[4:]))

def states_for_wire(states,room):
    if not isinstance(states,dict) or len(states)>LIMIT:return None
    clean={}
    for key,state in states.items():
        if not valid_key(key,room) or not isinstance(state,dict):return None
        hp,maximum,dead=state.get('hp'),state.get('max_hp'),state.get('dead')
        pos,yaw=state.get('pos'),state.get('yaw',0)
        if (not integer(maximum,0,250) or not integer(hp,0,maximum)
                or type(dead) is not bool or dead!=(hp==0)
                or (maximum==0 and not dead) or not integer(yaw,0,0xFFFFFFFF)
                or not isinstance(pos,list) or len(pos)!=3
                or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in pos)):
            return None
        clean[key]=dict(hp=hp,max_hp=maximum,dead=dead,pos=list(pos),yaw=yaw)
        if 'profile' in state:
            if not profile_ok(state['profile']):return None
            clean[key]['profile']=state['profile']
        # Which player's game simulates it, and that game's frame of the pose.
        if 'owner' in state:
            if not player_ok(state['owner']):return None
            clean[key]['owner']=state['owner']
        if 't' in state:
            if not integer(state['t'],0,0xFFFFFFFF):return None
            clean[key]['t']=state['t']
    return clean

def player_ok(value):return isinstance(value,str) and 1<=len(value)<=64

def report_for_wire(report,room):
    """One game's view of its room: every enemy it reads, and its own losses."""
    if not isinstance(report,dict):return None
    enemies,frame=report.get('enemies'),report.get('frame')
    if frame is not None and not integer(frame,0,0xFFFFFFFF):return None
    if not isinstance(enemies,dict) or len(enemies)>LIMIT:return None
    clean={}
    for key,seen in enemies.items():
        if not valid_key(key,room) or not isinstance(seen,dict):return None
        hp,maximum,pos,yaw,active=(seen.get(k) for k in ('hp','max_hp','pos','yaw','active'))
        if (not integer(maximum,1,250) or not integer(hp,0,maximum) or type(active) is not bool
                or not integer(yaw,0,0xFFFFFFFF) or not isinstance(pos,list) or len(pos)!=3
                or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in pos)):
            return None
        clean[key]=dict(hp=hp,max_hp=maximum,pos=list(pos),yaw=yaw,active=active)
    losses=attacks_for_wire(report.get('losses',[]),room)
    complete=report.get('complete',False)
    if losses is None or type(complete) is not bool:return None
    # complete: this game read its whole actor table (an absence means dead).
    return dict(frame=frame,enemies=clean,losses=losses,complete=complete)

def context_for_wire(context):
    if (not isinstance(context,(tuple,list)) or len(context)!=2
            or not isinstance(context[0],(tuple,list)) or len(context[0])!=2
            or not integer(context[0][0],0,23) or not integer(context[0][1],0,65535)
            or not isinstance(context[1],str) or len(context[1])!=16):return None
    return (tuple(context[0]),context[1])

def attacks_for_wire(attacks,room):
    if not isinstance(attacks,list) or len(attacks)>LIMIT:return None
    clean=[];last=0
    for attack in attacks:
        if (not isinstance(attack,dict) or not integer(attack.get('id'),last+1,2**53)
                or not valid_key(attack.get('key'),room) or not integer(attack.get('damage'),1,250)
                or 'profile' in attack and not profile_ok(attack['profile'])):
            return None
        clean.append({k:deepcopy(attack[k]) for k in ('id','key','damage','profile') if k in attack});last=attack['id']
    return clean


class EnemyFrames:
    """This player's side of its room: the latest shared states from the host's
    ledger, and its own native HP losses until the ledger acknowledges them."""
    def __init__(self):
        self.context=None;self.serial=0
        self.clear()

    def clear(self):
        self.states=None;self.received=-1;self.updated=0
        self.pending=[]

    def select(self,context):
        context=context_for_wire(context)
        if context!=self.context:self.clear();self.context=context
        return context

    def queue(self,attacks):
        if self.context is None or len(self.pending)+len(attacks)>LIMIT:return False
        candidate=[dict(id=self.serial+i+1,key=a.get('key'),damage=a.get('damage'),
                        **({'profile':a['profile']} if 'profile' in a else {}))
                   for i,a in enumerate(attacks)]
        clean=attacks_for_wire(candidate,self.context[0])
        if clean is None:return False
        self.pending.extend(clean);self.serial+=len(clean);return True

    def report(self,report):
        """Wire report of this game's room with the losses still unacknowledged."""
        if self.context is None:return None
        return report_for_wire(dict(report,losses=self.pending),self.context[0])

    def receive(self,payload,player_id):
        if self.context is None or context_for_wire(payload.get('context'))!=self.context:return False
        revision=payload.get('revision');acked=payload.get('acked')
        clean=states_for_wire(payload.get('states'),self.context[0])
        if (not integer(revision,0,2**53) or revision<=self.received or clean is None
                or not isinstance(acked,dict) or len(acked)>4
                or not all(isinstance(k,str) and integer(v,0,2**53) for k,v in acked.items())):return False
        self.states=clean;self.received=revision;self.updated=time.monotonic()
        self.pending=[a for a in self.pending if a['id']>acked.get(player_id,0)]
        return True
