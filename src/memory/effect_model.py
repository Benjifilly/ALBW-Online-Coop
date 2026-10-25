"""Private visual particles. The EFLK unload guard retires borrowed instances.

No actor/projectile, collision, audio or damage callback is created. Source
generations are distinct from private native handles, and all jobs run on the
game thread. A native retirement flag is stable across descriptor swaps.
"""
import struct
import time
from src.memory.attack_effects import AttackEffectReader,validate_effects,EFFECT_FINGERPRINTS,BEAM_BANKS,BANK_KINDS
from src.memory.native_jobs import RenderJob as Job,NativeJobRejected
from src.memory.remote_model import MAILBOX
from src.memory.render_state import _heap
from src.memory.wall_model import PrivateWallModel
from src.memory.effect_resource import find_bank,parser_prefix

NATIVE_EFFECTS=dict(EFFECT_FINGERPRINTS)
NATIVE_EFFECTS.update({
    0x4A3940:bytes.fromhex('f8432de90050a0e10270a0e10180a0e1'),
    0x4A1AD8:bytes.fromhex('f0412de90050a0e10070a0e3480090e5'),
    0x4A1FA4:bytes.fromhex('70402de90050a0e10c0090e5c0108fe2'),
    0x4A2724:bytes.fromhex('0000a0e31eff2fe1'),
    0x58FA34:bytes.fromhex('000050e31eff2f0110402de90040a0e1'),
    0x4A1BD4:bytes.fromhex('70402de90050a0e13c0090e50160a0e1'),
    0x4A1EF8:bytes.fromhex('f0472de90050a0e10040a0e33c0090e5'),
    0x4A3760:bytes.fromhex('04402de5001090e5000051e31200000a'),
    0x4A21D0:bytes.fromhex('f04f2de91cd04de20090a0e1240090e5'),
})


class PrivateAttackEffects:
    def __init__(self,trial,player,room,*,bounded_trial=False):
        if any(trial.bridge.read_memory(at,len(code))!=code for at,code in NATIVE_EFFECTS.items()):
            raise ValueError('Routines de particules non reconnues.')
        self.pool=PrivateWallModel(trial,player,room);self.bridge=trial.bridge
        self.player=player;self.room=tuple(room);self.banks={};self.active={};self.seen={}
        self.control=0;self.peer=None;self.source_manager=0
        self._pending_job=None
        self._one_shots={};self.dropped_one_shots=0

    def retired(self):
        return bool(self.pool.allocation and self.bridge.read_u32(self.pool.allocation+0x180))

    def _prepare(self,kind):
        if kind in self.banks:return self.banks[kind]
        b=self.bridge;source=b.read_u32(self.player+0x344)
        if not _heap(source):return None
        manager=b.read_u32(source+8)
        template=None
        template_name={2:'ObjBombS',3:'MassGrass',4:'MassLawnLight',5:'ObjArrow',6:'ObjHookShot'}.get(kind)
        if template_name:
            template=find_bank(b,manager,template_name)
        elif kind==1:
            candidates=AttackEffectReader(b)._beams(manager)
            if candidates:source=candidates[0]
            else:template=find_bank(b,manager,'ObjSwordBeam')
        wrapper=b.read_u32(source+0xC);parser=b.read_u32(wrapper+0xC) if _heap(wrapper) else 0
        context=self.pool._read(wrapper+0x20,12) if _heap(wrapper) else None
        header=template['header'] if template else b.read_u32(parser) if _heap(parser) else 0
        if not _heap(header):return None
        counts=template['counts'] if template else struct.unpack('<7I',self.pool._read(header,28))
        if counts[0]!=9 or any(v>512 for v in counts[1:4]):raise ValueError('Budget EFLK inattendu.')
        reader=AttackEffectReader(b)
        name=(template_name or 'ObjSwordBeam') if template else reader._string(b.read_u32(wrapper+8))
        allowed=(template_name,) if template_name else BEAM_BANKS if kind==1 else ('PlayerObject',)
        if name not in allowed:raise ValueError('Banque source inattendue.')
        table=b.read_u32(parser+0x14)
        names=template['names'] if template else {reader._string(b.read_u32(table+i*20+4)) for i in range(counts[3])}
        if not self.pool.heap:
            self.pool.reserve();self.source_manager=manager
            clone=bytearray(self.pool._read(manager,0x40));private=self.pool.allocation+0x100
            struct.pack_into('<4I',clone,4,private+4,private+4,0,0)
            struct.pack_into('<I',clone,0x1C,0);struct.pack_into('<I',clone,0x38,0)
            self.pool._write(private,clone)
            self.pool._write(self.pool.allocation+0x200,struct.pack('<2I',self.pool.allocation+0x204,0x4A2724))
            self.pool._write(self.pool.allocation+0x220,struct.pack('<12f3f',1,0,0,0,0,1,0,0,0,0,1,0,1,1,1))
        if manager!=self.source_manager:raise RuntimeError('Le moteur de particules a changé.')
        budget=0x4000+counts[1]*8+counts[2]*16
        if template:budget+=len(template['unparsed'] or b'')
        free,_=self.pool.jobs.call(Job.FREE_SIZE,self.pool.heap,heap=self.pool.heap)
        largest,_=self.pool.jobs.call(Job.MAX_BLOCK,self.pool.heap,4,heap=self.pool.heap)
        if min(free,largest)<budget:raise RuntimeError('Réserve de particules insuffisante.')
        # Bank descriptors must not overlap the eight particle handles at400,
        # either presentation block at500/700 or the private heap at1000.
        base=self.pool.allocation+0xA00+kind*0x80
        initial_matrix=base+0x40
        initial_scale=base+0x70
        self.pool._write(initial_matrix,struct.pack('<12f3f',1,0,0,0,0,1,0,0,0,0,1,0,1,1,1))
        self.pool._write(base,struct.pack('<6I',base+0x20,self.pool.allocation+0x200,
            initial_matrix,0,initial_scale,0))
        self.pool._write(base+0x20,name.encode()+b'\0')
        self.pool.uncertain=True
        try:bank,_=self.pool.jobs.call(Job.CREATE_EFFECT_BANK,self.pool.allocation+0x100,base,self.pool.heap,heap=self.pool.heap)
        except NativeJobRejected:self.pool.uncertain=False;raise
        if not self.pool.heap<=bank<self.pool.allocation+0x200000-0x5C:raise RuntimeError('Banque hors réserve privée.')
        private_wrapper=b.read_u32(bank+0xC);private_parser=b.read_u32(private_wrapper+0xC) if _heap(private_wrapper) else 0
        for pointer in (private_wrapper,private_parser):
            if not self.pool.heap<=pointer<self.pool.allocation+0x200000-0x34:raise RuntimeError('Parseur non privé.')
        if b.read_u32(private_parser+0x20)!=private_wrapper or b.read_u32(private_parser+0x24)!=self.pool.allocation+0x100:
            raise RuntimeError('Propriétaire du parseur incorrect.')
        item=dict(bank=bank,parser=private_parser,names=names,initial_matrix=initial_matrix)
        self.banks[kind]=item
        self.pool.uncertain=False
        self.pool._write(private_wrapper+0x20,context)
        if template and template['unparsed']:
            # A first beam must work even if this local save never fired one.
            # Parse a complete private copy; the registered archive is untouched.
            raw=template['unparsed'];copy=self.pool._alloc(len(raw))
            self.pool._write(copy,raw)
            self.pool._write(private_parser,parser_prefix(copy,counts))
            self.pool.jobs.call(Job.PARSE_EFFECT,private_parser,heap=self.pool.heap)
        else:
            # Keep private owner/manager words; borrow only prepared tables.
            self.pool._write(private_parser,template['prefix'] if template else self.pool._read(parser,0x20))
        self.pool.jobs.call(Job.PREPARE_EFFECT_BANK,bank,self.pool.heap,heap=self.pool.heap)
        return item

    def _finish_job(self,*,closing=False):
        pending=self._pending_job
        if pending is None:return True
        try:result=self.pool.jobs.poll(pending['request'])
        except NativeJobRejected:
            self._pending_job=None
            if closing:return True
            raise
        if result is None:return False
        self._pending_job=None
        serial=pending['serial']
        if pending['kind']=='stop':
            if self.active.get(serial) is pending['item']:del self.active[serial]
            return True
        instance,generation=struct.unpack('<2I',self.pool._read(pending['handle'],8))
        self.seen[serial]=time.monotonic()
        if not instance:return True  # The shared native particle pool is full.
        if (not _heap(instance) or self.bridge.read_u32(instance+0x1C)!=generation
                or self.bridge.read_u32(instance+0x18)!=pending['bank']):
            raise RuntimeError('Identité de la particule incorrecte.')
        self.active[serial]=dict(slot=pending['slot'],handle=pending['handle'],
            instance=instance,generation=generation)
        return True

    def _start_job(self,kind,serial,**item):
        job=Job.CREATE_EFFECT if kind=='create' else Job.STOP_EFFECT
        args=(item['handle'],item['bank'],item['text']) if kind=='create' else (item['item']['handle'],)
        request=self.pool.jobs.submit(job,*args,heap=self.pool.heap)
        if request is None:return False
        self._pending_job=dict(request=request,kind=kind,serial=serial,**item)
        return True

    def queue_one_shots(self,effects):
        """Receiver-local bush bursts, retained until native retirement.

        The fixed matrix is local validated data. Started bursts have no time
        limit: pausing the game must not cut their tail. Only requests that
        never reached the native queue expire after two seconds, and both the
        queue and native slots remain bounded.
        """
        shots=getattr(self,'_one_shots',None)
        if shots is None:self._one_shots=shots={}
        now=time.monotonic()
        for effect in effects:
            clean=validate_effects([effect])
            if (not clean or clean[0]['bank'] not in (3,4)
                    or clean[0]['name']!='Break' or clean[0]['id']<0x80000000):
                raise ValueError('Effet de buisson local invalide.')
            row=clean[0];serial=row['id']
            if serial in shots or serial in self.seen:continue
            if len(shots)>=16:
                self.dropped_one_shots=getattr(self,'dropped_one_shots',0)+1
                continue
            shots[serial]=dict(effect=row,queued=now)

    def _one_shot_rows(self):
        shots=getattr(self,'_one_shots',{})
        now=time.monotonic();rows=[]
        for serial,shot in list(shots.items()):
            active=self.active.get(serial)
            pending=self._pending_job
            submitted=bool(pending and pending['kind']=='create' and pending['serial']==serial)
            if active:
                pointer=active['instance'];raw=self.bridge.read_memory(pointer+0x18,0x28)
                if not raw or len(raw)!=0x28:
                    raise RuntimeError('Fin de particule de buisson illisible.')
                bank,generation=struct.unpack_from('<2I',raw)
                flags=struct.unpack_from('<I',raw,0x24)[0]
                expected=self.banks[shot['effect']['bank']]['bank']
                if bank!=expected or generation!=active['generation'] or flags&8:
                    # The native bank has retired this generation (or marked
                    # it for retirement). Do not send STOP to a reused slot.
                    del self.active[serial];del shots[serial]
                    continue
                core,core_generation=struct.unpack_from('<2I',raw,8)
                if core:
                    live=self.bridge.read_memory(core,16) if _heap(core) else None
                    if not live or len(live)!=16:
                        raise RuntimeError('Cœur de particule de buisson illisible.')
                    count=struct.unpack_from('<i',live,4)[0]
                    actual_generation=struct.unpack_from('<I',live,12)[0]
                    if count<=0 or actual_generation!=core_generation:
                        # A finished core can leave a stable native wrapper in
                        # its bank. Remove it from wanted, keeping the active
                        # handle until the ordinary acknowledged STOP releases
                        # this slot. Never treat that stable wrapper as a live
                        # burst or reuse its handle while STOP is still pending.
                        del shots[serial]
                        continue
            elif not submitted:
                if serial in self.seen:
                    # Native allocation returned no handle (particle pool full).
                    del shots[serial];continue
                if now-shot['queued']>2:
                    del shots[serial]
                    self.dropped_one_shots=getattr(self,'dropped_one_shots',0)+1
                    continue
            rows.append(shot['effect'])
        return rows

    def publish(self,effects,peer):
        if self.retired():return False
        effects=validate_effects(effects)
        if effects is None:raise ValueError('Effets distants invalides.')
        self._finish_job()
        if self.peer!=peer:
            # New receiver-local events can be queued before the first peer
            # presentation. Preserve unsubmitted bursts while hide retires the
            # previous peer's generations.
            fresh={k:v for k,v in getattr(self,'_one_shots',{}).items()
                   if k not in self.active and k not in self.seen}
            if not self.hide():return False
            self.active.clear();self.seen.clear();self.peer=peer
            self._one_shots=fresh
        effects=list(effects)+self._one_shot_rows()
        wanted={item['id'] for item in effects}
        now=time.monotonic()
        for serial in wanted&self.seen.keys():self.seen[serial]=now
        self.seen={serial:stamp for serial,stamp in self.seen.items() if serial in wanted or now-stamp<=2}
        # Creation and stop requests are acknowledged on later frames. They
        # never stall the main pose publisher or overwrite a queued request.
        if self._pending_job is None:
            for serial,item in self.active.items():
                if serial not in wanted:
                    self._start_job('stop',serial,item=item)
                    break
        records=[]
        for effect in sorted(effects,key=lambda effect:(effect['bank']>=3,-effect['bank'])):
            if effect['bank'] not in self.banks and (self._pending_job or self.pool.jobs.pending):continue
            item=self._prepare(effect['bank'])
            if item is None or effect['name'] not in item['names']:continue
            bank=item['bank']
            pending=self._pending_job
            if not (pending and pending['kind']=='create' and pending['bank']==bank):
                self.pool._write(bank+0x24,struct.pack('<2I',*effect['selectors']))
            if effect['id'] not in self.seen and self._pending_job is None:
                if len(self.seen)>=256:
                    oldest=min((serial for serial in self.seen if serial not in wanted),key=self.seen.get)
                    del self.seen[oldest]
                slot=next((i for i in range(8) if i not in [a['slot'] for a in self.active.values()]),None)
                if slot is None:continue
                handle=self.pool.allocation+0x400+slot*8;text=self.pool.allocation+0x440
                if item.get('initial_matrix'):
                    # The constructor can emit before Python receives its ACK.
                    # Seed the private bank at the landing point as well, so
                    # even that first native frame cannot originate at (0,0,0).
                    self.pool._write(item['initial_matrix'],struct.pack('<12f',*effect['matrix']))
                self.pool._write(text,effect['name'].encode()+b'\0')
                self._start_job('create',effect['id'],slot=slot,handle=handle,text=text,bank=bank)
            active=self.active.get(effect['id'])
            if active:records.append(struct.pack('<2I12f',active['instance'],active['generation'],*effect['matrix']))
        if not self.pool.allocation:return
        first=self.pool.allocation+0x500
        address=first+0x200 if self.control==first else first
        payload=struct.pack(f'<{BANK_KINDS+7}I',60,self.pool.heap,
            *(self.banks.get(k,{}).get('bank',0) for k in range(BANK_KINDS)),
            *self.room,len(records),address+0x40,self.pool.allocation+0x180)
        self.pool._write(address,payload+bytes(0x40-len(payload))+b''.join(records))
        if not self.bridge.write_u32(MAILBOX+0x1FC,address):raise RuntimeError('Publication des particules incertaine.')
        self.control=address
        return True

    def hide(self):
        if self.control and not self.bridge.write_u32(self.control,0):return False
        if not self._finish_job(closing=True):return False
        if self.pool.jobs.pending or self.pool.uncertain:return False
        for item in self.banks.values():self.pool.jobs.call(Job.KILL_EFFECT_BANK,item['bank'],heap=self.pool.heap)
        self.active.clear()
        self._one_shots={}
        return True

    def suspend(self):
        return not self.control or self.bridge.write_u32(self.control,0)

    def close(self):
        if not self._finish_job(closing=True):return False
        if self.pool.jobs.pending or self.pool.uncertain:return False
        if not self.hide():return False
        if not self.bridge.write_u32(MAILBOX+0x1FC,0):return False
        for kind,item in list(self.banks.items()):
            bank=item['bank'];cache=[self.bridge.read_u32(bank+off) for off in (0x38,0x54,0x58)]
            if any(p is None for p in cache):return False
            self.pool.jobs.call(Job.DETACH_EFFECT_BANK,bank,heap=self.pool.heap)
            for pointer in (*cache,bank,item['parser']):
                if pointer:self.pool.jobs.call(Job.FREE,pointer,heap=self.pool.heap)
            del self.banks[kind]
        # Its own mailbox entry (+1FC) is cleared above; the painting's is not ours.
        return self.pool.close(painting=False)
