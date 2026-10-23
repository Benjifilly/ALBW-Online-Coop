"""Frame-thread enemy movement, within the existing owned world reservation."""
from collections import deque
from pathlib import Path
import math
import struct
import time
from src.memory.remote_model import branch
from src.memory.world_layout import WORLD_POINTER,slot,reserve
from src.memory.enemy_sync import EnemySynchronizer

HOOK=0x4F48C8
FINGERPRINT=bytes.fromhex('30402de90040a0e1100590e5011b84e2')
ORIGINAL=FINGERPRINT[:4]
ENTRY=slot('pose_entry')
OFFSET=reserve('pose')
BUFFERS=(reserve('pose_buffers'),reserve('pose_buffers')+0x2000)
DYING=0x80000000


class EnemyMotionBuffer:
    """Continuous motion of the enemies another player's game simulates.

    Samples arrive irregularly (the owner reads its enemies at its own loop
    rate, then 30 Hz on the wire). Holding the newest one made a follower's
    soldier lunge, then stand still while its local AI kept running: it looked
    stuck. Present each enemy slightly in the past instead, between the two
    samples around that time, extending a late one briefly.

    A sample stamped with the owner's game frame ('t') is placed on that
    game's own clock, so the owner's read and network jitter disappear: the
    clock offset follows the fastest delivery, and drifts up slowly.
    """
    DELAY=.1
    EXTEND=.12
    FPS=60
    DRIFT=.02

    def __init__(self):self.samples={};self.owners={};self.offsets={}

    def sample(self,key,state,now):
        pos=tuple(state['pos']);owner=state.get('owner')
        if self.owners.get(key)!=owner:
            # Another game's clock and position: never blend across owners.
            self.samples.pop(key,None);self.owners[key]=owner
        history=self.samples.setdefault(key,deque(maxlen=8))
        target=now-self.DELAY
        if type(state.get('t')) is int:
            stamp=state['t']/self.FPS
            if history and stamp<history[-1][0]-1:
                # The owner's frame counter restarted (new world reservation).
                history.clear();self.offsets.pop(owner,None)
            known=self.offsets.get(owner)
            if not history or stamp>history[-1][0]:
                history.append((stamp,pos,state['yaw']));offset=now-stamp
                if known is None or offset<known:known=offset
                else:known+=(offset-known)*self.DRIFT
                self.offsets[owner]=known
            target-=now-stamp if known is None else known
        elif not history or history[-1][1]!=pos:history.append((now,pos,state['yaw']))
        if len(history)<2:return history[-1][1],history[-1][2]
        if target<=history[0][0]:return history[0][1],history[0][2]
        for (ta,a,_),(tb,b,yaw) in zip(history,list(history)[1:]):
            if target<=tb:
                t=(target-ta)/(tb-ta) if tb>ta else 1
                return tuple(x+(y-x)*t for x,y in zip(a,b)),yaw
        (ta,a,_),(tb,b,yaw)=history[-2],history[-1]
        if tb<=ta or target-tb>self.EXTEND or sum((y-x)**2 for x,y in zip(a,b))>16:return b,yaw
        t=(target-ta)/(tb-ta)
        return tuple(x+(y-x)*t for x,y in zip(a,b)),yaw

    def forget(self,keys):
        for key in list(self.samples):
            if key not in keys:del self.samples[key];self.owners.pop(key,None)

class NativeEnemyPose:
    def __init__(self,world):
        self.world=world;self.bridge=world.bridge
        self.hooked=False;self.closed=False;self.sequence=0;self.buffer=1
        self.last_frame=None;self.jump=branch(HOOK,ENTRY)
        self.last_dying={}
        self.motion=EnemyMotionBuffer()

    def start(self):
        b=self.bridge;w=self.world
        if (not w.allocation or w.closed or not w.trial.installed
                or b.read_u32(WORLD_POINTER)!=w.allocation
                or b.read_u32(w.allocation+4)!=1
                or b.read_memory(HOOK,len(FINGERPRINT))!=FINGERPRINT):
            raise ValueError('Intégration des ennemis incompatible avec le profil vérifié.')
        code=Path(__file__).with_name('enemy_pose.bin').read_bytes()
        if len(code)>0x1000:raise ValueError('Code de déplacement trop grand.')
        if not (b.write_memory(w.allocation+OFFSET,code)
                and b.read_memory(w.allocation+OFFSET,len(code))==code
                and b.write_u32(w.allocation+0x98,0)
                and b.write_u32(w.allocation+0x9C,w.allocation+OFFSET)):
            raise RuntimeError('Préparation du déplacement natif non confirmée.')
        self.hooked=True
        if not b.write_memory(HOOK,self.jump) or b.read_memory(HOOK,4)!=self.jump:
            raise RuntimeError('Installation du déplacement natif non confirmée.')

    def publish(self,sync,states):
        b=self.bridge;w=self.world
        if (self.closed or not self.hooked or w.closed or b.read_u32(WORLD_POINTER)!=w.allocation
                or b.read_u32(w.allocation+4)!=1):return False
        frame=b.read_u32(w.allocation+0x74)
        if type(frame) is not int:return False
        rows=[]
        dying={}
        for key,info in sync.local_enemies.items():
            state=states.get(key)
            if not state:continue
            if state['dead']:
                # Keep following the host while the native death animation is
                # still drawing this actor. A maxHP0 tombstone has no position:
                # retain only an earlier exact dying pose for this local owner.
                if info['hp']>0:continue
                identity=EnemySynchronizer._identity(info)
                if state['max_hp']==0:
                    previous=self.last_dying.get(key)
                    if previous is None or previous[0]!=identity:continue
                    state=previous[1]
                dying[key]=(identity,dict(state))
            elif info['hp']<=0:continue
            # This is a candidate list, not a write lease. The native callback
            # revalidates every identity at the moment it changes an actor.
            fresh=info
            start=info['pos'];target,yaw=self.motion.sample(key,state,time.monotonic())
            state=dict(state,yaw=yaw)
            if not start or not all(math.isfinite(v) and abs(v)<=1e6 for v in (*start,*target)):continue
            rows.append(struct.pack('<6I6fI3f',fresh['handle'],fresh['address'],fresh['vtable'],
                                    fresh['type'],fresh['placement_id']&0xFFFFFFFF,fresh['max_hp']|(DYING if state['dead'] else 0),
                                    *start,*target,state['yaw'],*fresh['initial_position']))
        self.motion.forget(states.keys())
        if len(rows)>64:raise RuntimeError('Trop d’ennemis pour le déplacement partagé.')
        self.last_dying=dying
        duration=max(1,min(6,(frame-self.last_frame)&0xFFFFFFFF)) if self.last_frame is not None else 2
        self.buffer=1-self.buffer;address=w.allocation+BUFFERS[self.buffer];self.sequence+=2
        block=struct.pack('<4I',self.sequence|1,frame,len(rows),duration).ljust(0x20,b'\0')+b''.join(rows)
        if not (b.write_u32(address,self.sequence|1) and b.write_memory(address,block)
                and b.write_u32(address,self.sequence) and b.write_u32(w.allocation+0x98,address)):
            raise RuntimeError('Publication du déplacement natif non confirmée.')
        self.last_frame=frame;return True

    def close(self):
        if self.closed:return True
        b=self.bridge;w=self.world
        if not b.write_u32(w.allocation+0x9C,0):return False
        if self.hooked:
            if b.read_memory(HOOK,4) not in (self.jump,ORIGINAL):return False
            if not b.write_memory(HOOK,ORIGINAL) or b.read_memory(HOOK,4)!=ORIGINAL:return False
        self.hooked=False;self.closed=True;return True
