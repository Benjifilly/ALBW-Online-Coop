"""Shared progress: native game flags and item grants, replayed in the other games.

One long-lived reservation (pointer 7AFF74), independent of the room. Three
verified call sites feed its capture ring: setFlag's changed-value branch
(4ADAF4), the single item grant call (28EE18) and the direct event flag
writer (4CDF18, changed bits only). The frame callback replays
remote changes through the same native routines (setFlag 4AD9C8, grant
344810). Rule (Benji): everything is shared except small keys.
"""
from datetime import datetime
import json
from pathlib import Path
import struct
import time
from src.memory.native_jobs import NativeRenderJobs,RenderJob,NativeJobRejected
from src.memory.remote_model import branch,MAILBOX
from src.memory.world_layout import slot

POINTER=slot('progress_pointer')
MAGIC=0x50524731
LEASE=120
CAPTURE=0x4000
CAPTURE_SLOTS=256
REPLAY=0x8000
REPLAY_SLOTS=64
IN_FLIGHT=16
# (hook, trampoline, entry offset, verified bytes; the first word is replaced)
HOOKS=((0x4ADAF4,slot('progress_flag_trampoline'),4,'1c009fe50630a0e10820a0e10710a0e1'),
       (0x28EE18,slot('progress_grant_trampoline'),8,'7cd602ebf008d4e1910050e396005013'),
       (0x4CDF18,slot('progress_event_trampoline'),12,'70402de90050a0e10100a0e10240a0e1'))
COURSE_BLOCK=0x44
COURSE_SIZE=0x16C
SMALL_KEY=1
# Boss key and compass belong to the course they were found in.
COURSE_ITEMS=(2,3)
OP_FLAG,OP_ITEM,OP_SPEND,OP_WALLET=1,2,3,4
WALLET_MAX=9999
APPLIED,REFUSED,NOT_NOW=1,2,3
NOISY_CHANGES=6
NOISY_SECONDS=3
# A failed step (lost RPC answer...) stops sharing this long, then resumes.
RECOVERY_SECONDS=5
FOLDER=Path(__file__).resolve().parents[2]/'.local/research'


def course_of(offset):
    """Course index of a flag manager block offset (+44 + course*16C), or None."""
    if type(offset) is not int or offset<COURSE_BLOCK:return None
    course,rest=divmod(offset-COURSE_BLOCK,COURSE_SIZE)
    return course if not rest and course<64 else None


def decode(raw):
    serial,kind,a,b,c,block,room,caller=struct.unpack('<8I',raw)
    where=None if room==0xFFFFFFFF else [room&0xFF,room>>8]
    row=dict(serial=serial,course=course_of(block),room=where)
    if kind==1:row.update(type='flag',flag=[a,b,c],caller=caller)
    elif kind==2:row.update(type='item',item=a,tag=b-0x100000000 if b>=0x80000000 else b,loot=bool(c))
    elif kind==3:row.update(type='spend',amount=a)
    else:row.update(type='unknown')
    return row


class NativeSharedProgress:
    def __init__(self,trial):
        self.trial=trial;self.bridge=trial.bridge;self.jobs=NativeRenderJobs(trial)
        self.allocation=0;self.closed=False;self.uncertain=False;self.published=False
        self.hooked=[];self.trampolines=[];self.read=0;self.requested=0;self.completed=0
        self.final_results=[]

    def start(self):
        b=self.bridge
        if (b.read_u32(POINTER) or any(b.read_memory(h,16)!=bytes.fromhex(raw) for h,e,o,raw in HOOKS)
                or any(b.read_memory(e,8)!=bytes(8) for h,e,o,raw in HOOKS)):
            raise ValueError('La progression partagée ne correspond pas au jeu vérifié.')
        try:result,address=self.jobs.call(RenderJob.RESERVE,timeout=3)
        except NativeJobRejected:raise
        except RuntimeError:self.uncertain=True;raise
        if result or not 0x14000000<=address<=0x1BE00000:raise RuntimeError('Réserve de la progression indisponible.')
        self.allocation=address
        code=Path(__file__).with_suffix('.bin').read_bytes()
        if not (b.write_memory(address,bytes(0x100)) and b.write_memory(address+CAPTURE,bytes(CAPTURE_SLOTS*32))
                and b.write_memory(address+REPLAY,bytes(REPLAY_SLOTS*32))
                and b.write_memory(address+0x1000,code)
                and b.read_memory(address+0x1000,len(code))==code
                and b.write_memory(address,struct.pack('<3I',MAGIC,1,LEASE))):
            raise RuntimeError('Préparation de la progression partagée non confirmée.')
        self.published=True
        if not b.write_u32(POINTER,address):raise RuntimeError('Publication de la progression incertaine.')
        for hook,entry,offset,raw in HOOKS:
            trampoline=struct.pack('<2I',0xE51FF004,address+0x1000+offset)
            self.trampolines.append(entry)
            if not b.write_memory(entry,trampoline) or b.read_memory(entry,8)!=trampoline:
                raise RuntimeError('Entrée native de la progression non confirmée.')
            self.hooked.append((hook,entry,bytes.fromhex(raw)[:4]))
            if not b.write_memory(hook,branch(hook,entry)) or b.read_memory(hook,4)!=branch(hook,entry):
                raise RuntimeError('Observation native de la progression non confirmée.')

    def renew(self,enabled=True):
        if self.closed or self.uncertain:return False
        a=self.allocation;b=self.bridge
        return b.write_u32(a+8,LEASE) and b.write_u32(a+4,int(enabled))

    def captured(self):
        b=self.bridge;a=self.allocation
        end=b.read_u32(a+0x10)
        if type(end) is not int or not self.read<=end<=self.read+CAPTURE_SLOTS:
            raise RuntimeError('Journal natif de la progression incohérent.')
        if end==self.read:return []
        ring=b.read_memory(a+CAPTURE,CAPTURE_SLOTS*32)
        if ring is None or len(ring)!=CAPTURE_SLOTS*32:raise RuntimeError('Progression native incomplète.')
        rows=[]
        for serial in range(self.read+1,end+1):
            at=((serial-1)%CAPTURE_SLOTS)*32
            row=decode(ring[at:at+32])
            if row['serial']!=serial:raise RuntimeError('Progression native remplacée pendant sa lecture.')
            rows.append(row)
        return rows

    def acknowledge(self,count):
        if type(count) is not int or not 0<=count<=CAPTURE_SLOTS:return False
        if not count:return True
        self.read+=count
        return self.bridge.write_u32(self.allocation+0x14,self.read)

    def dropped(self):
        value=self.bridge.read_u32(self.allocation+0x18)
        return value if type(value) is int else 0

    def room_for(self):return IN_FLIGHT-(self.requested-self.completed)

    def submit(self,op,a,b_=0,c=0,d=0):
        if self.closed or self.uncertain or self.room_for()<=0:return None
        if op not in (OP_FLAG,OP_ITEM,OP_SPEND,OP_WALLET) or any(type(v) is not int or not 0<=v<=0xFFFF for v in (a,b_,c,d)):
            raise ValueError('Commande de progression hors du contrat natif.')
        b=self.bridge;serial=self.requested+1
        at=self.allocation+REPLAY+((serial-1)%REPLAY_SLOTS)*32
        if not b.write_memory(at,struct.pack('<8I',serial,op,a,b_,c,d,0,0)):
            raise RuntimeError('Préparation de la progression impossible.')
        if not b.write_u32(self.allocation+0x20,serial):
            self.uncertain=True;raise RuntimeError('Progression publiée sans confirmation.')
        self.requested=serial
        return serial

    def results(self):
        """(serial, result) of every replay the game thread completed since the last call."""
        if self.closed:
            final,self.final_results=self.final_results,[];return final
        b=self.bridge;a=self.allocation
        done=b.read_u32(a+0x24)
        if type(done) is not int or not self.completed<=done<=self.requested:
            raise RuntimeError('Réponses natives de la progression incohérentes.')
        out=[]
        for serial in range(self.completed+1,done+1):
            raw=b.read_memory(a+REPLAY+((serial-1)%REPLAY_SLOTS)*32,32)
            if raw is None or len(raw)!=32 or struct.unpack_from('<I',raw)[0]!=serial:
                raise RuntimeError('Réponse de progression incomplète.')
            result=struct.unpack_from('<I',raw,24)[0]
            if result not in (APPLIED,REFUSED,NOT_NOW):raise RuntimeError('Réponse de progression invalide.')
            out.append((serial,result))
        self.completed=done
        return out

    def _next_frame(self,deadline):
        frame=self.bridge.read_u32(MAILBOX+0x50)
        if type(frame) is not int:return False
        while self.bridge.read_u32(MAILBOX+0x50)==frame:
            if time.monotonic()>=deadline:return False
            time.sleep(.01)
        return True

    def close(self):
        if self.closed:return True
        if self.uncertain:return False
        if not self.allocation:self.closed=True;return True
        b=self.bridge;a=self.allocation
        if not self.trial.installed or b.attached_pid!=self.trial.pid:return False
        if not b.write_u32(a+4,0):return False
        for hook,entry,raw in list(self.hooked):
            if b.read_memory(hook,4) not in (branch(hook,entry),raw):return False
            if not b.write_memory(hook,raw) or b.read_memory(hook,4)!=raw:return False
            self.hooked.remove((hook,entry,raw))
        deadline=time.monotonic()+2
        # A later frame proves no observer or replay is still running.
        if not self._next_frame(deadline):return False
        try:self.final_results=self.results()
        except RuntimeError:return False
        if self.published and not b.write_u32(POINTER,0):return False
        for entry in list(self.trampolines):
            if not b.write_memory(entry,bytes(8)):return False
            self.trampolines.remove(entry)
        if not self._next_frame(deadline):return False
        try:result,_=self.jobs.call(RenderJob.RELEASE,a,timeout=2)
        except RuntimeError:return False
        if result:return False
        self.closed=True;self.allocation=0;return True


class SharedProgressSynchronizer:
    """Local changes to the journal; the others' changes into this game."""
    def __init__(self,on_log=lambda message:None):
        self.log=on_log;self.session=None;self.native=None;self.error='';self.player=None
        self.observed=self.applied=self.refused=0;self._failed_at=0;self._reset()
        self.flags_applied=0;self._flags_logged=0
        # The collective return debits every wallet itself (party_life): never shared.
        self.suppress_spend=lambda:False;self._wallet_roster=None

    def _reset(self):
        self.cursor=0;self.incoming=[];self.sent={};self.recent={};self.muted=set();self.seen=set()
        self.applied_items=set();self.last_dropped=0

    def status(self):
        return dict(shared_progress_status='error' if self.error else ('active' if self.native else 'waiting_game'),
                    shared_progress_observed=self.observed,shared_progress_applied=self.applied,
                    shared_progress_waiting=len(self.incoming),shared_progress_refused=self.refused,
                    shared_progress_error=self.error)

    def _evidence(self,row):
        try:
            FOLDER.mkdir(parents=True,exist_ok=True)
            with (FOLDER/f'progress-{self.player}.jsonl').open('a',encoding='utf-8') as output:
                output.write(json.dumps(dict(at=datetime.now().isoformat(timespec='seconds'),**row))+'\n')
        except (OSError,TypeError,ValueError):pass

    def _ledger(self):return FOLDER/f'progress-applied-{self.player}.json'

    def _load_items(self,session):
        try:data=json.loads(self._ledger().read_text(encoding='utf-8'))
        except (OSError,ValueError):return set()
        values=data.get(session) if isinstance(data,dict) else None
        return {v for v in values if type(v) is int} if isinstance(values,list) else set()

    def _save_items(self):
        # A restarted companion in the same session must never grant an item twice.
        try:
            FOLDER.mkdir(parents=True,exist_ok=True)
            self._ledger().write_text(json.dumps({self.session:sorted(self.applied_items)}),encoding='utf-8')
        except (OSError,TypeError,ValueError):pass

    def _noisy(self,key,now):
        stamps=[t for t in self.recent.get(key,[]) if now-t<NOISY_SECONDS]+[now]
        self.recent[key]=stamps
        if len(stamps)>NOISY_CHANGES and key not in self.muted:
            self.muted.add(key)
            self.log(f'Drapeau {key} trop changeant : il reste local.')
            self._evidence(dict(event='muted',key=list(key)))
        return key in self.muted

    def _payload(self,row,now):
        """Journal payload of one native observation, or None when it stays local."""
        if row['type']=='flag':
            kind,index,value=row['flag'];key=('flag',kind,index)
            if key not in self.seen:
                self.seen.add(key);self._evidence(dict(event='observed',**row))
            if kind==0 or self._noisy(key,now) or row['room'] is None:return None
            course=row['course'] if row['course'] is not None else row['room'][0]
            if kind in (1,2,3) and row['course'] is None:return None
            return dict(kind='flag',flag=[kind,index,value],course=course,room=list(row['room']))
        if row['type']=='spend':
            if self.suppress_spend():self._evidence(dict(event='spend_kept',**row));return None
            return dict(kind='spend',amount=min(row['amount'],WALLET_MAX))
        if row['type']=='item':
            self._evidence(dict(event='observed',**row))
            if row['loot'] or row['item']<=SMALL_KEY or row['item']>=0x61:return None
            course=row['course'] if row['course'] is not None else (row['room'] or [0])[0]
            return dict(kind='item',item=row['item'],course=course)
        self._evidence(dict(event='unknown',**row))
        return None

    def _publish(self,net,now):
        rows=self.native.captured();done=0
        for row in rows:
            p=self._payload(row,now)
            if p is not None:
                if net.queue_progress_observation(p) is None:break
                self.observed+=1
                if p['kind']=='item':self.log(f'Objet partagé avec les autres (n° {p["item"]:#04x}).')
            done+=1
        if not self.native.acknowledge(done):raise RuntimeError('Accusé de lecture de la progression impossible.')
        dropped=self.native.dropped()
        if dropped!=self.last_dropped:
            self.log(f'Progression : {dropped-self.last_dropped} changement(s) natif(s) perdu(s).')
            self._evidence(dict(event='dropped',count=dropped-self.last_dropped));self.last_dropped=dropped

    def _receive(self,net):
        for e in net.get_progress_events(self.cursor):
            self.cursor=e['revision']
            if e['source']==net.player_id:continue
            # Never twice after a restart: grants and wallet changes are not idempotent.
            if e['payload']['kind'] in ('item','spend','wallet') and e['revision'] in self.applied_items:continue
            self.incoming.append(dict(e,retry=0))

    def _ready(self,event,room,now):
        """'apply', 'wait' (another course) or 'drop' (a room flag of another room)."""
        p=event['payload']
        if now<event['retry']:return 'wait'
        if p['kind'] in ('spend','wallet'):return 'apply'
        if p['kind']=='item':
            return 'wait' if p['item'] in COURSE_ITEMS and p['course']!=room[0] else 'apply'
        kind=p['flag'][0]
        if kind==1:return 'apply' if tuple(p['room'])==room else 'drop'
        if kind in (2,3) and p['course']!=room[0]:return 'wait'
        return 'apply'

    def _apply(self,room,now):
        for event in list(self.incoming):
            if self.native.room_for()<=0:return
            verdict=self._ready(event,room,now)
            if verdict=='wait':continue
            self.incoming.remove(event)
            if verdict=='drop':
                self._evidence(dict(event='stale_room_flag',payload=event['payload']));continue
            p=event['payload']
            if p['kind']=='flag':serial=self.native.submit(OP_FLAG,p['flag'][0],p['flag'][1],p['flag'][2],p['course'])
            elif p['kind']=='spend':serial=self.native.submit(OP_SPEND,p['amount'])
            elif p['kind']=='wallet':serial=self.native.submit(OP_WALLET,p['value'])
            else:serial=self.native.submit(OP_ITEM,p['item'],p['course'])
            if serial is None:self.incoming.insert(0,event);return
            self.sent[serial]=event

    def _results(self,now):
        for serial,result in self.native.results():
            event=self.sent.pop(serial,None)
            if event is None:continue
            p=event['payload']
            if result==APPLIED:
                self.applied+=1
                if p['kind'] in ('item','spend','wallet'):
                    self.applied_items.add(event['revision']);self._save_items()
                if p['kind']=='item':self.log(f'Objet reçu d’un autre joueur (n° {p["item"]:#04x}).')
                elif p['kind']=='spend':self.log(f'Bourse commune : {p["amount"]} rubis dépensés par un autre joueur.')
                elif p['kind']=='wallet':self.log(f'Bourse commune alignée sur l’hôte : {p["value"]} rubis.')
                else:self.flags_applied+=1
            elif result==NOT_NOW:
                self.incoming.insert(0,dict(event,retry=now+.5))
            else:
                self.refused+=1;self._evidence(dict(event='refused',payload=p))

    def _publish_wallet(self,net,snapshot,peers):
        """The host's wallet, which every game aligns to: once per session and
        again whenever the players present change (a newcomer missed earlier gains)."""
        roster=(net.session_id,tuple(sorted(p.get('player_id','') for p in peers if p.get('in_game'))))
        rupees=getattr(snapshot,'rupees',None)
        if net.mode!='host' or roster==self._wallet_roster or type(rupees) is not int or not 0<=rupees<=WALLET_MAX:return
        if net.queue_progress_observation(dict(kind='wallet',value=rupees)) is not None:
            self._wallet_roster=roster

    def step(self,memory,snapshot,net,appearance,peers=()):
        native=getattr(appearance,'progress_service',None)
        if not hasattr(net,'queue_progress_observation'):return
        if native is not self.native:
            if self.native is not None and self.native.closed:
                # Replays the closed service never ran return to the queue.
                try:self._results(time.monotonic())
                except RuntimeError:pass
            self.incoming=[dict(e,retry=0) for e in self.sent.values()]+self.incoming;self.sent={}
            self.native=native if native is not None and not native.closed else None
        if self.native is None:return
        online=bool(net.session_id) and (net.mode=='host' or net.connection_state=='connected')
        self.player=net.player_id
        if self.error and time.monotonic()-self._failed_at>=RECOVERY_SECONDS:
            self.log('Partage de la progression relancé après : '+self.error);self.error=''
        try:
            if self.session!=net.session_id:
                self._reset();self.session=net.session_id
                if self.session:self.applied_items=self._load_items(self.session)
            if not self.native.renew(online and not self.error):
                raise RuntimeError('La progression partagée ne répond plus.')
            if self.error or not online or not snapshot.in_game:return
            now=time.monotonic()
            self._publish(net,now)
            self._publish_wallet(net,snapshot,peers)
            self._receive(net)
            self._results(now)
            self._apply((snapshot.course,snapshot.stage),now)
            # One line for a burst of flags (a door, a chest, a cutscene...).
            if self.flags_applied and now-self._flags_logged>=2:
                self.log(f'Progression d’un autre joueur appliquée ({self.flags_applied} drapeau(x)).')
                self.flags_applied=0;self._flags_logged=now
        except (RuntimeError,ValueError,OSError,TypeError,KeyError,struct.error) as exc:
            error=str(exc) or type(exc).__name__;self._failed_at=time.monotonic()
            self.native.renew(False)
            # Once per failure: a closed Azahar fails every step (25/10: hundreds of lines).
            if error!=self.error:self.log('Partage de la progression interrompu : '+error)
            self.error=error
