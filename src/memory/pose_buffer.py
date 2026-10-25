"""Short, local presentation buffer. Gameplay and network snapshots stay raw."""
from collections import deque
from copy import deepcopy
import math


def _quaternion(m):
    a=[m[r*4+c] for r in range(3) for c in range(3)]
    # The verified body and held-weapon transforms use proper rotation bases.
    # Preserve unusual/scaled bases discretely rather than deforming a model.
    for r in range(3):
        if abs(sum(a[r*3+c]**2 for c in range(3))-1)>.02:return None
        for s in range(r):
            if abs(sum(a[r*3+c]*a[s*3+c] for c in range(3)))>.02:return None
    det=a[0]*(a[4]*a[8]-a[5]*a[7])-a[1]*(a[3]*a[8]-a[5]*a[6])+a[2]*(a[3]*a[7]-a[4]*a[6])
    if det<.98:return None
    trace=a[0]+a[4]+a[8]
    if trace>0:
        s=math.sqrt(trace+1)*2
        return ((a[7]-a[5])/s,(a[2]-a[6])/s,(a[3]-a[1])/s,s/4)
    i=max(range(3),key=lambda i:a[i*3+i]);j=(i+1)%3;k=(i+2)%3
    s=math.sqrt(1+a[i*3+i]-a[j*3+j]-a[k*3+k])*2
    q=[0.0]*4;q[i]=s/4;q[j]=(a[j*3+i]+a[i*3+j])/s
    q[k]=(a[k*3+i]+a[i*3+k])/s;q[3]=(a[k*3+j]-a[j*3+k])/s
    return tuple(q)


def matrix_between(a,b,t):
    result=list(a)
    qa,qb=_quaternion(a),_quaternion(b)
    if qa and qb:
        dot=sum(x*y for x,y in zip(qa,qb))
        if dot<0:qb=tuple(-v for v in qb);dot=-dot
        if dot>.9995:q=[x+(y-x)*t for x,y in zip(qa,qb)]
        else:
            angle=math.acos(min(1,dot));s=math.sin(angle)
            q=[(math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/s for x,y in zip(qa,qb)]
        length=math.sqrt(sum(v*v for v in q));x,y,z,w=(v/length for v in q)
        rot=(1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),
             2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),
             2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y))
        for r in range(3):result[r*4:r*4+3]=rot[r*3:r*3+3]
    for i in (3,7,11):result[i]=a[i]+(b[i]-a[i])*t
    return result


def _frames_between(start,end,length,rate):
    """Frames played from start to end in the direction the animation runs,
    through its end when it looped (or restarted) meanwhile."""
    delta=end-start
    if length>1:
        if rate>0 and delta<-.5:delta+=length
        elif rate<0 and delta>.5:delta-=length
    return delta


def _wrap(frame,length):
    return frame%length if length>1 and not 0<=frame<=length else frame


def _phases_between(a,b,t,frames=2.0):
    """Animation phases between two samples `frames` game frames apart.

    Samples can arrive several hundred ms apart (25/10): the phase may then
    have advanced far, or looped, and must still play instead of freezing.
    """
    out=deepcopy(a)
    for i,(la,lb) in enumerate(zip(a,b)):
        if la and lb and la[0]==lb[0] and la[4]==lb[4]:
            length,rate=la[4],la[3]
            delta=_frames_between(la[1],lb[1],length,rate)
            if abs(delta)<=max(4,abs(rate)*8,abs(rate)*frames*1.5+4):
                previous=_frames_between(la[2],lb[2],length,rate)
                out[i]=[la[0],_wrap(la[1]+delta*t,length),_wrap(la[2]+previous*t,length),la[3],la[4]]
    return out


def wall_between(a,b,t,frames=2.0):
    """Blend matched triangles only on the same verified physical plane."""
    if not a or not b or a['variant']!=b['variant'] or a['meshes']!=b['meshes']:return None
    sa,sb=a.get('surface'),b.get('surface')
    if not sa or not sb or sa['triangle_count']!=sb['triangle_count']:return None
    if sa.get('uv_transform',[1.,0.])!=sb.get('uv_transform',[1.,0.]):return None
    if any(abs(x-y)>1e-6 for x,y in zip(sa['projection_matrix'],sb['projection_matrix'])):return None
    normal=sa['vectors'][:3]
    for surf in (sa,sb):
        if any(abs(sum(x*y for x,y in zip(normal,surf['vectors'][i:i+3]))-1)>.0001
               for i in range(0,len(surf['vectors']),3)):return None
    plane=sum(x*y for x,y in zip(normal,sa['positions'][:3]))
    for surf in (sa,sb):
        if any(abs(sum(x*y for x,y in zip(normal,surf['positions'][i:i+3]))-plane)>.002
               for i in range(0,len(surf['positions']),3)):return None
    # A re-triangulation can keep the same count but reorder corners. Do not
    # interpolate those unrelated vertices or drag a clipped edge through air.
    if any(abs(x-y)>.2 for x,y in zip(sa['uv'],sb['uv'])):return None
    if any(sum((x-y)**2 for x,y in zip(sa['positions'][i:i+3],sb['positions'][i:i+3]))>.25
           for i in range(0,len(sa['positions']),3)):return None
    out=deepcopy(a)
    out['matrix']=matrix_between(a['matrix'],b['matrix'],t)
    out['scale']=[x+(y-x)*t for x,y in zip(a['scale'],b['scale'])]
    out['animations']=_phases_between(a['animations'],b['animations'],t,frames)
    for key in ('positions','uv'):
        out['surface'][key]=[x+(y-x)*t for x,y in zip(sa[key],sb[key])]
    out['surface']['matrix']=matrix_between(sa['matrix'],sb['matrix'],t)
    return out


class PoseBuffer:
    """Plays a peer's samples slightly in the past, interpolating between them.

    The delay covers the gaps actually seen between this peer's samples. A
    fixed 50 ms froze the companion, then jumped it, on 25/10: a laptop's
    states arrived with 200-500 ms holes nearly every second. The delay grows
    quickly to cover a new gap, shrinks slowly, and playback never runs
    backwards. The gaps belong to the link: kept across rooms.
    """
    MIN_DELAY=.05
    MAX_DELAY=.4
    # Longer: a pause or a menu, not the link's jitter.
    IDLE=1.5
    HOLE=.15

    def __init__(self,delay=MIN_DELAY):
        self.delay=delay;self.key=None;self.samples=deque(maxlen=64)
        self.gaps=deque(maxlen=120);self._arrived=None;self._played=None;self._clock=None
        self.holes=0;self.worst=0.

    def take_holes(self):
        """(gaps over HOLE, longest gap) since the previous call, for the journal."""
        result=self.holes,self.worst;self.holes=0;self.worst=0.
        return result

    def clear(self):
        self.key=None;self.samples.clear();self._arrived=self._played=None

    def wanted_delay(self):
        if len(self.gaps)<10:return max(self.delay,self.MIN_DELAY)
        gaps=sorted(self.gaps)
        return min(self.MAX_DELAY,max(self.MIN_DELAY,gaps[int(len(gaps)*.95)]*1.1+.017))

    def _follow(self,now):
        """Move the delay toward the wanted one: playback at 65 % speed while it
        grows, 110 % while it shrinks, never a jump."""
        elapsed=min(.1,max(0.,now-self._clock)) if self._clock is not None else 0.
        self._clock=now
        want=self.wanted_delay()
        if want>self.delay:self.delay=min(want,self.delay+.35*elapsed)
        else:self.delay=max(want,self.delay-.1*elapsed)

    def sample(self,peer,now):
        render=peer.get('render_state',{})
        key=(peer.get('player_id'),peer.get('course'),peer.get('stage'),render.get('merged'))
        previous=self.samples[-1][1] if self.samples else None
        jump=previous and sum((peer[k]-previous[k])**2 for k in ('x','y','z'))>16
        if key!=self.key or jump or not render:
            self.clear();self.key=key
        if not self.samples or peer!=self.samples[-1][1]:
            gap=now-self._arrived if self._arrived is not None else None
            if gap is not None and gap<self.IDLE:
                self.gaps.append(gap)
                if gap>self.HOLE:self.holes+=1;self.worst=max(self.worst,gap)
            self._arrived=now
            self.samples.append((now,deepcopy(peer)))
        self._follow(now)
        if len(self.samples)<2:return peer
        target=now-self.delay
        if self._played is not None and target<self._played:target=self._played
        self._played=target
        while len(self.samples)>2 and self.samples[1][0]<=target:self.samples.popleft()
        ta,a=self.samples[0];tb,b=self.samples[1]
        if target>=tb:return b
        if target<=ta:return a
        t=(target-ta)/(tb-ta);out=deepcopy(a);frames=(tb-ta)*60
        for k in ('x','y','z'):out[k]=a[k]+(b[k]-a[k])*t
        out['yaw']=a['yaw']+((b['yaw']-a['yaw']+math.pi)%math.tau-math.pi)*t
        ar,br=a.get('render_state'),b.get('render_state')
        if not ar or not br:return out
        if render.get('merged'):
            wall=wall_between(ar.get('wall'),br.get('wall'),t,frames)
            if wall is None:
                if not (ar.get('wall') or {}).get('surface') and (br.get('wall') or {}).get('surface'):
                    return b
                return a
            out['render_state']['wall']=wall
            return out
        out['render_state']['matrix']=matrix_between(ar['matrix'],br['matrix'],t)
        ca,cb=ar.get('carried'),br.get('carried')
        if ca and cb and ca['target']==cb['target']:
            out['render_state']['carried']=dict(ca,pos=[x+(y-x)*t for x,y in zip(ca['pos'],cb['pos'])])
        out['render_state']['scale']=[x+(y-x)*t for x,y in zip(ar['scale'],br['scale'])]
        out['render_state']['layers']=_phases_between(ar['layers'],br['layers'],t,frames)
        effects={e['id']:e for e in br.get('effects',())}
        for effect in out['render_state'].get('effects',()):
            other=effects.get(effect['id'])
            if other and (effect['bank'],effect['name'],effect['selectors'])==(other['bank'],other['name'],other['selectors']):
                effect['matrix']=matrix_between(effect['matrix'],other['matrix'],t)
        for ea,eb,eo in zip(ar.get('equipment',()),br.get('equipment',()),out['render_state'].get('equipment',())):
            if (ea['kind'],ea['variant'],ea['visible'])==(eb['kind'],eb['variant'],eb['visible']):
                eo['matrix']=matrix_between(ea['matrix'],eb['matrix'],t)
                eo['scale']=[x+(y-x)*t for x,y in zip(ea['scale'],eb['scale'])]
        items={(e['kind'],e['variant']):e for e in br.get('items',())}
        for item in out['render_state'].get('items',()):
            other=items.get((item['kind'],item['variant']))
            if other and item['visible']==other['visible'] and item['meshes']==other['meshes']:
                item['matrix']=matrix_between(item['matrix'],other['matrix'],t)
                item['scale']=[x+(y-x)*t for x,y in zip(item['scale'],other['scale'])]
        bombs={e['id']:e for e in br.get('bombs',())}
        for bomb in out['render_state'].get('bombs',()):
            other=bombs.get(bomb['id'])
            if (other and (bomb['kind'],bomb['variant'],bomb['phase'])==
                    (other['kind'],other['variant'],other['phase'])):
                bomb['matrix']=matrix_between(bomb['matrix'],other['matrix'],t)
                bomb['pos']=[x+(y-x)*t for x,y in zip(bomb['pos'],other['pos'])]
                bomb['scale']=[x+(y-x)*t for x,y in zip(bomb['scale'],other['scale'])]
                if bomb.get('blink') and other.get('blink'):
                    bomb['blink']=_phases_between([bomb['blink']],[other['blink']],t,frames)[0]
                if bomb.get('chain') and len(bomb['chain'])==len(other.get('chain') or ()):
                    bomb['chain']=[matrix_between(x[:12],y[:12],t)+[p+(q-p)*t for p,q in zip(x[12:],y[12:])]
                                   for x,y in zip(bomb['chain'],other['chain'])]
        return out
