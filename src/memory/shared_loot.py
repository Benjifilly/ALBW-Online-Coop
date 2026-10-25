"""Native resolved drops and shared rewards, independent of the current room."""
from pathlib import Path
import struct
import time
from src.memory.native_jobs import NativeRenderJobs,RenderJob,NativeJobRejected
from src.memory.remote_model import branch,MAILBOX
from src.network.shared_loot import spawn_payload,break_key
from src.memory.world_layout import slot

POINTER=slot('loot_pointer')
MAGIC=0x4C4F5431
# A transient failure (RPC hiccup, scene change) is retried after this delay,
# never one the native service marked uncertain (a reward may be pending).
RECOVERY_SECONDS=5
CAUSES=0x10000
DROPS=0xA0000
LIMIT=4096
# A transient failure (RPC hiccup, scene change) is retried after this delay,
# never one the native service marked uncertain (a reward may be pending).=5
HOOKS=(
    (0x4C4530,0x7AFED0,4,'c00285e5'),(0x4C4A7C,0x7AFED8,8,'0900a0e1'),
    (0x14EC00,0x7AFEE0,12,'3f402de9'),(0x1D7238,0x7AFEE8,16,'30402de9'),
    (0x4E8838,0x7AFEF0,20,'10402de9'),(0x1CD2E4,0x7AFEF8,24,'30402de9'),
    (0x2882DC,0x7AFF00,28,'30402de9'),(0x344810,0x7AFF08,32,'70402de9'),
    (0x361728,0x7AFF10,36,'08402de9'),(0x1DD484,0x7AFF18,40,'100090e5'),
    (0x4E7578,0x7AFF48,44,'10402de9'),(0x4F4974,0x7AFF50,72,'30402de9'))

class NativeSharedLoot:
    def __init__(self,trial):
        self.trial=trial;self.bridge=trial.bridge;self.jobs=NativeRenderJobs(trial)
        self.allocation=0;self.closed=False;self.uncertain=False;self.published=False
        self.hooked=[];self.sequence=0;self.pending=None
        self.final_rows={};self.final_causes=[]

    def start(self):
        b=self.bridge
        if (b.read_u32(POINTER) or any(b.read_memory(h,4)!=bytes.fromhex(raw) for h,e,o,raw in HOOKS)
                or any(b.read_memory(e,8)!=bytes(8) for h,e,o,raw in HOOKS)):
            raise ValueError('Les apparitions et récompenses ne correspondent pas au jeu vérifié.')
        try:result,address=self.jobs.call(RenderJob.RESERVE,timeout=3)
        except NativeJobRejected:raise
        except RuntimeError:self.uncertain=True;raise
        if result or not 0x14000000<=address<=0x1BE00000:raise RuntimeError('Réserve du butin indisponible.')
        self.allocation=address
        code=Path(__file__).with_suffix('.bin').read_bytes()
        # The native reservation is initially zeroed by the kernel. Explicitly
        # initialize the header, both bounded ledgers and descriptor scratch.
        if not (b.write_memory(address,bytes(0x200)) and b.write_memory(address+CAUSES,bytes(LIMIT*128))
                and b.write_memory(address+DROPS,bytes(LIMIT*128))
                and b.write_memory(address+0x1000,code)
                and b.read_memory(address+0x1000,len(code))==code
                and b.write_memory(address,struct.pack('<3I',MAGIC,1,120))):
            raise RuntimeError('Préparation du partage des récompenses non confirmée.')
        self.published=True
        if not b.write_u32(POINTER,address):raise RuntimeError('Publication du butin incertaine.')
        for hook,entry,offset,raw in HOOKS:
            trampoline=struct.pack('<2I',0xE51FF004,address+0x1000+offset)
            if not b.write_memory(entry,trampoline) or b.read_memory(entry,8)!=trampoline:
                raise RuntimeError('Entrée native du butin non confirmée.')
            self.hooked.append((hook,entry,bytes.fromhex(raw)))
            if not b.write_memory(hook,branch(hook,entry)) or b.read_memory(hook,4)!=branch(hook,entry):
                raise RuntimeError('Observation native du butin non confirmée.')

    def renew(self,enabled=True,enemy_authority=True,context=None):
        if self.closed or self.uncertain:return False
        a=self.allocation;b=self.bridge
        if context:
            room,epoch=context
            if not b.write_memory(a+0xC4,struct.pack('<2I',*room)+bytes.fromhex(epoch)):return False
        else:
            if not b.write_memory(a+0xCC,bytes(8)):return False
        return (b.write_u32(a+8,120) and b.write_u32(a+0x28,int(enemy_authority))
                and b.write_u32(a+4,int(enabled)))

    def causes(self,after):
        if self.closed:return [c for c in self.final_causes if c['serial']>after]
        b=self.bridge;a=self.allocation
        if b.read_u32(a+0x18):raise RuntimeError('Le journal natif du butin est plein.')
        end=b.read_u32(a+0x10)
        if type(end) is not int or not after<=end<=LIMIT:raise RuntimeError('Journal des cassages invalide.')
        out=[]
        for serial in range(after+1,end+1):
            raw=b.read_memory(a+CAUSES+(serial-1)*128,128)
            if raw is None or len(raw)!=128 or struct.unpack_from('<I',raw)[0]!=serial:
                raise RuntimeError('Relevé de cassage incomplet.')
            if struct.unpack_from('<I',raw,0x2C)[0]!=1:break
            course,stage,kind,handle,index,type_id,placement=struct.unpack_from('<7I',raw,4)
            placement=placement if placement<0x80000000 else placement-0x100000000
            xyz=struct.unpack_from('<3f',raw,0x20)
            target=[type_id,placement,*(round(v*10000) for v in xyz)]
            if kind==1:
                xyz=struct.unpack_from('<3f',raw,0x58)
                target.extend([index,*(round(v*10000) for v in xyz)])
            count=struct.unpack_from('<I',raw,0x30)[0]
            if count>8:raise RuntimeError('Trop d’apparitions dans un même cassage.')
            ids=list(struct.unpack_from('<8I',raw,0x34)[:count])
            if kind not in (1,2,3):raise RuntimeError('Origine native de butin inconnue.')
            out.append(dict(serial=serial,room=(course,stage),cause_kind={1:'mass',2:'actor',3:'enemy'}[kind],
                            parent=struct.unpack_from('<I',raw,0x70)[0],
                            target=target,drops=ids,epoch=raw[0x68:0x70].hex()))
        return out

    def rows(self):
        if self.closed:return self.final_rows
        b=self.bridge;a=self.allocation;n=b.read_u32(a+0x20)
        if type(n) is not int or not 0<=n<=LIMIT:raise RuntimeError('Journal des apparitions invalide.')
        if not n:return {}
        raw=b.read_memory(a+DROPS,n*128)
        if raw is None or len(raw)!=n*128:raise RuntimeError('Apparitions natives incomplètes.')
        out={}
        for i in range(n):
            block=raw[i*128:(i+1)*128];serial=struct.unpack_from('<I',block)[0]
            if serial!=i+1:raise RuntimeError('Une apparition a changé pendant le relevé.')
            spawn=dict(type=struct.unpack_from('<I',block,0x14)[0],
                       position=list(struct.unpack_from('<3f',block,0x18)),
                       auxiliary=list(struct.unpack_from('<3f',block,0x24)),
                       yaw=struct.unpack_from('<I',block,0x30)[0],params=list(struct.unpack_from('<4h',block,0x34)))
            actor,handle,accepted,grant=struct.unpack_from('<4I',block,0x40)
            out[serial]=dict(spawn=spawn,room=struct.unpack_from('<2I',block,4),
                             actor=actor,handle=handle,accepted=accepted,grant=grant,
                             queued=struct.unpack_from('<I',block,0x3C)[0],
                             birth_hp=struct.unpack_from('<h',block,0x60)[0])
        return out

    def actor_live(self,row,room):
        if tuple(row['room'])!=tuple(room) or not row['actor'] or not row['handle']:return False
        b=self.bridge;owner=b.read_u32(0x706E20)
        if not owner:return False
        table=b.read_u32(owner+0x3DBC0);end=b.read_u32(owner+0x3DBC8)
        if not table or not end:return False
        slot=table+(row['handle']>>22)*4
        if not table<=slot<end or b.read_u32(slot)!=row['actor']:return False
        at=row['actor']
        return b.read_u32(at+0x2CC)==row['handle'] and b.read_u8(at+0x2D7)==0

    def submit(self,op,record=0,*,room=(0,0),spawn=None):
        if self.pending is not None or self.closed or self.uncertain:return False
        if op not in (1,2,3,4,5,6) or type(record) is not int or not 0<=record<=LIMIT:
            raise ValueError('Commande de butin hors du contrat natif.')
        if op in (1,6) and spawn_payload(spawn) is None:raise ValueError('Apparition non vérifiée.')
        b=self.bridge;a=self.allocation
        if spawn is not None:
            block=struct.pack('<I6fI4h',spawn['type'],*spawn['position'],*spawn['auxiliary'],spawn['yaw'],*spawn['params'])
            if not b.write_memory(a+0x100,block):raise RuntimeError('Préparation du butin impossible.')
        if not (b.write_memory(a+0x38,struct.pack('<2I',op,record))
                and b.write_memory(a+0x44,struct.pack('<2I',*room)) and b.write_u32(a+0xBC,1)):
            raise RuntimeError('Préparation de récompense impossible.')
        frame=b.read_u32(a+12)
        self.sequence+=1;self.pending=dict(nonce=self.sequence,op=op,record=record,frame=frame)
        if not b.write_u32(a+0x30,self.sequence):
            self.uncertain=True;raise RuntimeError('Récompense publiée sans confirmation.')
        return True

    def poll(self):
        if self.pending is None:return None
        p=self.pending;a=self.allocation;b=self.bridge
        if b.read_u32(a+0x34)!=p['nonce']:
            frame=b.read_u32(a+12)
            if type(frame) is int and type(p['frame']) is int and 120<((frame-p['frame'])&0xFFFFFFFF)<0x80000000:
                self.uncertain=True;raise RuntimeError('Une récompense attend sa confirmation ; aucune relance en double.')
            return None
        if b.read_u32(a+0xBC)!=3:raise RuntimeError('Confirmation native de récompense incomplète.')
        result=dict(p,success=b.read_u32(a+0x40)==1,record=b.read_u32(a+0x80))
        self.pending=None;return result

    def close(self):
        if self.closed:return True
        if self.uncertain:return False
        if not self.allocation:self.closed=True;return True
        b=self.bridge;a=self.allocation
        if not self.trial.installed or b.attached_pid!=self.trial.pid:return False
        if self.pending is not None and self.poll() is None:return False
        if not b.write_u32(a+4,0):return False
        for hook,entry,raw in self.hooked:
            if b.read_memory(hook,4) not in (branch(hook,entry),raw):return False
            if not b.write_memory(hook,raw) or b.read_memory(hook,4)!=raw:return False
        deadline=time.monotonic()+2;frame=b.read_u32(MAILBOX+0x50)
        while b.read_u32(MAILBOX+0x50)==frame:
            if time.monotonic()>=deadline:return False
            time.sleep(.01)
        self.final_rows=self.rows();self.final_causes=self.causes(0)
        if self.published and not b.write_u32(POINTER,0):return False
        for hook,entry,offset,raw in HOOKS:
            if not b.write_memory(entry,bytes(8)):return False
        frame=b.read_u32(MAILBOX+0x50)
        while b.read_u32(MAILBOX+0x50)==frame:
            if time.monotonic()>=deadline:return False
            time.sleep(.01)
        try:result,_=self.jobs.call(RenderJob.RELEASE,a,timeout=2)
        except RuntimeError:return False
        if result:return False
        self.closed=True;self.allocation=0;return True

class SharedLootSynchronizer:
    def __init__(self,on_log=lambda message:None):
        self.log=on_log;self.session=None;self.native=None;self.error='';self._failed_at=0;self._reset()

    def _reset(self):
        self.cause_cursor=0;self.event_cursor=0;self.local_causes={};self.winners={};self.claims=set()
        self.bindings={};self.granted=set();self.claim_sent=set();self.todo=[];self.pending=None
        self.rows={};self.next_scan=0;self.retry_after={};self.unsupported=0
        self.retired_receipts={}
        self.visit=0;self.last_room=None;self.spawn_visits={}
        self.defeats=set();self.death_sent=set()

    def _clear_dynamic(self):
        if self.native:
            self.native.bridge.shared_dynamic_enemies={}
            self.native.bridge.shared_dynamic_defeated_keys=set()
            self.native.bridge.shared_dynamic_death_receipts=set()

    def _send_claim(self,net,drop):
        if drop in self.claim_sent:return
        if net.queue_loot_observation(dict(kind='pickup',drop=list(drop))) is None:
            raise RuntimeError('Le ramassage ne peut plus être conservé.')
        self.claim_sent.add(drop)

    def _publish_dynamic(self,net,room):
        from src.dynamic_enemies import KINDS,key_for,parse_key
        from src.combat_profiles import sword_target
        n=self.native;b=n.bridge;previous=getattr(b,'shared_dynamic_enemies',{});current={}
        for e in self.winners.values():
            p=e['payload'];origin=tuple(p['room']);context=net.get_world_room_context(origin)
            if origin!=room or context is None or context['epoch']!=p['epoch']:continue
            for ordinal,spawn in enumerate(p['drops']):
                if spawn['type'] not in KINDS:continue
                i=self.bindings.get((e['revision'],ordinal));r=self.rows.get(i)
                if (r is None or not r['actor'] or not r['handle'] or r['accepted']!=1 or r['queued']
                        or r['spawn']!=spawn or tuple(r['room'])!=origin):continue
                key=key_for(origin,p['epoch'],e['revision'],ordinal,spawn['type'])
                old=previous.get(r['handle'],{})
                entry=dict(key=key,type=spawn['type'],room=origin,pid=b.attached_pid,
                           address=r['actor'],record=i,service=n.allocation,
                           initial_key=tuple(round(v*10000) for v in spawn['position']),
                           birth_hp=r['birth_hp'] or sword_target(spawn['type']).max_hp,
                           baseline_seen=old.get('baseline_seen',False) if old.get('key')==key else False,
                           last_hp=old.get('last_hp') if old.get('key')==key else None)
                current[r['handle']]=entry
        if {(h,e['key']) for h,e in previous.items()}!={(h,e['key']) for h,e in current.items()}:
            b.shared_dynamic_generation=getattr(b,'shared_dynamic_generation',0)+1
        b.shared_dynamic_enemies=current
        b.shared_dynamic_defeated_keys={e['key'] for e in current.values()
                                       if (parts:=parse_key(e['key']))
                                       and (parts[2],parts[3]) in self.defeats}

    def _source_deaths(self,net):
        from src.dynamic_enemies import parse_key
        for key in getattr(self.native.bridge,'shared_dynamic_death_receipts',set()):
            parsed=parse_key(key)
            if parsed is None:continue
            room,epoch,revision,ordinal,kind=parsed;drop=(revision,ordinal)
            if drop in self.death_sent or drop in self.defeats:continue
            winner=next((e for e in self.winners.values() if e['revision']==revision),None)
            if (winner is None or winner['payload']['epoch']!=epoch or tuple(winner['payload']['room'])!=room
                    or ordinal>=len(winner['payload']['drops']) or winner['payload']['drops'][ordinal]['type']!=kind):continue
            if net.queue_loot_observation(dict(kind='enemy_dead',drop=list(drop))) is None:
                raise RuntimeError('Le retrait de l’ennemi ne peut plus être conservé.')
            self.death_sent.add(drop)

    def _retain_retired(self,net,room):
        """Drain native commits before replacing a hero/renderer allocation."""
        if self.native is None or not self.native.closed:return
        self.rows=self.native.final_rows
        self._source_outcomes(net,room)
        for drop,i in self.bindings.items():
            row=self.rows.get(i)
            if row and row['grant']:
                self.granted.add(drop)
                if row['grant']==1:self._send_claim(net,drop)
        for causes in self.local_causes.values():
            for cause in causes:
                self.retired_receipts[cause['sequence']]=[bool(self.rows.get(i,{}).get('grant')) for i in cause['ids']]

    def status(self):
        return dict(shared_loot_status='error' if self.error else ('active' if self.native else 'waiting_game'),
                    shared_loot_breaks=len(self.winners),shared_loot_claims=len(self.claims),
                    shared_loot_granted=len(self.granted),shared_loot_unsupported=self.unsupported,
                    shared_loot_enemy_defeats=len(self.defeats),
                    shared_loot_error=self.error)

    def _source_outcomes(self,net,room):
        n=self.native
        for cause in n.causes(self.cause_cursor):
            context=net.get_world_room_context(cause['room'])
            if context is None or context['epoch']!=cause['epoch']:
                self.cause_cursor=cause['serial'];continue
            if any(i not in self.rows for i in cause['drops']):
                self.next_scan=0;break
            spawns=[self.rows[i]['spawn'] for i in cause['drops']]
            target=cause['target']
            if cause['cause_kind']=='enemy':
                parent=cause['parent'];row=self.rows.get(parent)
                # A simultaneous losing spawn must not produce a second death
                # lottery. Await its committed birth identity, without guessing
                # from coincident coordinates or handles in another process.
                if row and row['accepted']==2:
                    self.cause_cursor=cause['serial'];continue
                birth=next((drop for drop,i in self.bindings.items() if i==parent),None)
                if birth is None:break
                target=[*birth,row['spawn']['type']]
            event=dict(kind='break',room=list(cause['room']),epoch=context['epoch'],cause_kind=cause['cause_kind'],
                       target=target,drops=spawns)
            from src.network.shared_loot import payload
            if payload(event) is None:
                self.unsupported+=1
                # Unknown scalar variants retain their ordinary local pickup
                # behavior; they never become an arbitrary network grant.
                self.todo.extend(dict(op=3,record=i) for i in cause['drops'])
            else:
                sequence=net.queue_loot_observation(event)
                if sequence is None:raise RuntimeError('Le journal réseau ne peut plus conserver le butin.')
                self.local_causes.setdefault(break_key(event),[]).append(dict(sequence=sequence,ids=cause['drops']))
            self.cause_cursor=cause['serial']

    def _receive(self,net):
        for e in net.get_loot_events(self.event_cursor):
            p=e['payload'];self.event_cursor=e['revision']
            if p['kind']=='break':
                self.winners[break_key(p)]=e
                if p['cause_kind']=='enemy':self.defeats.add(tuple(p['target'][:2]))
            elif p['kind']=='pickup':self.claims.add(tuple(p['drop']))
            else:self.defeats.add(tuple(p['drop']))
        # Settle all local lotteries before releasing any local pickup gate.
        for key,causes in self.local_causes.items():
            winner=self.winners.get(key)
            if winner is None:continue
            for cause in causes:
                if cause.get('settled'):continue
                selected=winner['source']==net.player_id and cause['sequence']==winner['source_id']
                for ordinal,i in enumerate(cause['ids']):
                    if selected:
                        self.bindings[(winner['revision'],ordinal)]=i
                        self.spawn_visits[(winner['revision'],ordinal)]=self.visit
                        self.todo.append(dict(op=3,record=i))
                    else:self.todo.append(dict(op=4,record=i))
                cause['settled']=True
        for winner in self.winners.values():
            if winner['source']!=net.player_id:continue
            flags=self.retired_receipts.get(winner['source_id'],[])
            for ordinal,committed in enumerate(flags):
                if committed:
                    drop=(winner['revision'],ordinal);self.granted.add(drop);self._send_claim(net,drop)

    def _result(self,result):
        task=self.pending;self.pending=None
        if task is None:raise RuntimeError('Réponse de butin sans propriétaire.')
        drop=task.get('drop')
        if task['op'] in (1,6) and result['record']:
            self.bindings[drop]=result['record']
        if result['success']:
            if task['op']==5:self.granted.add(drop)
            if task['op'] in (1,2):self.spawn_visits[drop]=task.get('visit',self.visit)
        elif task['op'] in (3,4):
            raise RuntimeError('L’autorisation native d’une apparition a été refusée.')
        else:self.retry_after[(drop,task['op'])]=time.monotonic()+.5
        self.next_scan=0

    def step(self,memory,snapshot,net,appearance):
        native=getattr(appearance,'loot_service',None)
        if native is None or native.closed:
            if self.native and self.native.closed:
                try:self._retain_retired(net,(snapshot.course,snapshot.stage))
                except (RuntimeError,ValueError,TypeError,KeyError) as exc:self.error=str(exc)
            self._clear_dynamic();self.native=None;return
        online=bool(net.session_id) and (net.mode=='host' or net.connection_state=='connected')
        room=(snapshot.course,snapshot.stage)
        if (self.error and not native.uncertain and native is self.native
                and time.monotonic()-self._failed_at>=RECOVERY_SECONDS):
            self.log('Partage des récompenses relancé après : '+self.error);self.error=''
        try:
            if native is not self.native:
                self._clear_dynamic()
                if self.session==net.session_id:
                    self._retain_retired(net,room)
                    saved=(self.granted,self.claim_sent,self.retired_receipts)
                else:saved=(set(),set(),{})
                self.native=native;self._reset()
                self.granted,self.claim_sent,self.retired_receipts=saved
                self.session=net.session_id
            if self.session!=net.session_id:
                # Old session sources/claims must never enter a new recipient
                # ledger. Retire the owned service before installing another.
                if native.close():
                    self._clear_dynamic();appearance.loot_service=None;self.native=None;self._reset()
                return
            if room!=self.last_room:
                self.last_room=room;self.visit+=1
            # Every game plays the shared deaths; the room's first player draws
            # their drops, whoever simulated or killed the enemy.
            owns_death=net.get_room_authority(*room)==net.player_id
            if not native.renew(online and not self.error,owns_death,net.get_world_context()):
                raise RuntimeError('Le partage natif du butin ne répond plus.')
            if self.error or not online or not snapshot.in_game:
                self._clear_dynamic();return
            result=native.poll()
            if result is not None:self._result(result)
            now=time.monotonic()
            if now>=self.next_scan:
                self.rows=native.rows();self.next_scan=now+.08
                self._source_outcomes(net,room)
            self._receive(net)
            self._publish_dynamic(net,room)
            self._source_deaths(net)
            for drop,i in self.bindings.items():
                row=self.rows.get(i)
                if row and row['grant']:
                    self.granted.add(drop)
                    if row['grant']==1 and drop not in self.claim_sent:
                        self._send_claim(net,drop)
            if self.pending is not None:return
            task=self.todo.pop(0) if self.todo else None
            if task is None:
                for e in self.winners.values():
                    p=e['payload'];origin=tuple(p['room'])
                    context=net.get_world_room_context(origin)
                    for ordinal,spawn in enumerate(p['drops']):
                        drop=(e['revision'],ordinal);i=self.bindings.get(drop);row=self.rows.get(i)
                        if drop in self.defeats:continue
                        claimed=drop in self.claims
                        if not claimed and (context is None or context['epoch']!=p['epoch']):continue
                        op=(6 if claimed or origin!=room else 1) if i is None else None
                        if i is not None and claimed and drop not in self.granted:op=5
                        elif i is not None and claimed and row and native.actor_live(row,room):op=4
                        elif (i is not None and not claimed and origin==room and row and not row['grant']
                              and self.spawn_visits.get(drop)!=self.visit
                              and not row['queued'] and not native.actor_live(row,room)):op=2
                        if op is None or now<self.retry_after.get((drop,op),0):continue
                        task=dict(op=op,record=i or 0,room=origin,spawn=spawn,drop=drop,visit=self.visit);break
                    if task:break
            if task and native.submit(task['op'],task.get('record',0),room=task.get('room',room),spawn=task.get('spawn')):
                self.pending=task
        except (RuntimeError,ValueError,OSError,TypeError,KeyError,struct.error) as exc:
            error=str(exc) or type(exc).__name__;self._failed_at=time.monotonic()
            self._clear_dynamic()
            native.renew(False)
            # Once per failure: a closed Azahar fails every step (25/10: hundreds of lines).
            if error!=self.error:self.log('Partage des récompenses interrompu : '+error)
            self.error=error
