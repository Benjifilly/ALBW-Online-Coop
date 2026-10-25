"""Shared Mass props (grass, lawn, shrubs), resolved drops and combat evidence.

Coverage stays explicit: the closed families of src/mass_families.py and the
closed loot descriptors. Boss phases, global story flags and arbitrary actors
are not replicated here. Enemy authority owns native HP/pose commands; this
service only hands it the matched native hit receipts (CombatLog).

One step: follow the local scene (attach/detach the native world), start the
native services, publish local cuts to the room journal, then apply at most one
remote cut and wait for its native result before acknowledging it.
"""
import json
import time
from datetime import datetime
from src.memory.mass_state import MassStateReader
from src.mass_families import family,grass_profile,actor_prop
from src.memory.world_native import NativeWorldEvents
from src.memory.native_jobs import NativeJobRejected
from src.memory.combat_log import CombatLog,FOLDER
from src.memory.remote_model import MAILBOX
from src.network.world_journal import event_payload
from src.memory.carried_props import CarriedMirror,read_local_carried,validate_carried

# A remote cut whose target stays absent from a fully read scene is skipped
# after this delay instead of blocking every later cut of the room.
MISSING_TARGET_SECONDS=5
# ...but a target whose whole batch is absent from this copy cannot appear:
# it belongs to another room (e.g. filed during an outdoor scroll).
MISSING_BATCH_SECONDS=1
# Outdoor scrolls keep the departing room's batches alive while the room label
# already names the next room. Wait this long after the scroll before reading.
SCROLL_SETTLE_SECONDS=.5
# A failed scene is closed natively, then reopened after this delay (doubled
# while failures repeat, up to RECOVERY_MAX) instead of stopping every service.
RECOVERY_SECONDS=3
RECOVERY_MAX=30


class WorldSynchronizer:
    def __init__(self,bridge,on_log=lambda message:None):
        self.bridge=bridge;self.reader=MassStateReader(bridge);self.log=on_log
        self.native=None;self.instances={};self.context=None;self.network_context=None
        self.next_scan=0;self.pending_event=None;self.error='';self.state='waiting_game'
        self.applied=0;self.observed=0;self.seeded=0;self.skipped=0;self._scene=None
        self._renderer_gate=None;self.waiting_target=None;self._waiting_since=None
        self._seed_cuts=[];self._restore_targets=set()
        self.carried=None;self._last_carried=None;self._broken_seen={}
        # Carried actor -> lifted instance, kept across scenes (see read_local_carried).
        self._carry_bindings={}
        from src.memory.room_scroll import RoomScrollReader
        self._scroll=RoomScrollReader(bridge);self._settled_at=0
        self.recoveries=0;self.dropped=0;self._retry_at=0;self._failed_at=-1e9;self._backoff=RECOVERY_SECONDS
        self.combat=CombatLog()
        from src.memory.shared_loot import SharedLootSynchronizer
        self.loot=SharedLootSynchronizer(on_log)
        from src.memory.shared_progress import SharedProgressSynchronizer
        self.progress=SharedProgressSynchronizer(on_log);self._progress_unavailable=False

    @property
    def combat_matched(self):return self.combat.matched

    def drain_combat_receipts(self):return self.combat.drain()

    def status(self):
        from src.mass_families import FAMILIES,ACTOR_PROPS
        return dict(self.loot.status(),**self.progress.status(),world_sync_status=self.state,
                    world_sync_coverage=[f.name for f in FAMILIES.values()]+[p.name for p in ACTOR_PROPS.values()],
                    world_events_observed=self.observed,world_events_applied=self.applied,
                    world_events_seeded=self.seeded,world_events_skipped=self.skipped,
                    world_events_dropped=self.dropped,world_sync_recoveries=self.recoveries,
                    world_combat_observed=self.combat.observed,
                    world_combat_matched=self.combat.matched,
                    world_waiting_target=self.waiting_target,
                    world_sync_error=self.error)

    def _forget_scene(self):
        self.native=None;self.instances={};self.context=None;self.pending_event=None;self.carried=None
        self._carried_visible=0;self._last_carried=None
        self.combat.detach();self._waiting_since=None;self.waiting_target=None

    def _detach(self,appearance):
        if self.native and not self.native.close():
            self.state='retiring';return False
        if getattr(appearance,'world_extension',None) is self.native:appearance.world_extension=None
        self._forget_scene()
        self._renderer_gate=None;self._seed_cuts=[]
        return True

    def _renderer_ready(self,trial,room,now):
        """A title presentation can expose a hero but stop its game callback.

        Require sustained native updates before submitting the first resource
        request. A couple of startup callbacks are not a playable-frame lease.
        """
        frame=self.bridge.read_u32(MAILBOX+0x50)
        if type(frame) is not int or frame==0:
            self._renderer_gate=None;return False
        gate=self._renderer_gate
        if gate is None or gate['trial'] is not trial or gate['room']!=room:
            self._renderer_gate=dict(trial=trial,room=room,last=frame,advanced=0,progress=now)
            return False
        if now-gate['progress']>.2:
            gate.update(last=frame,advanced=0,progress=now);return False
        delta=(frame-gate['last'])&0xFFFFFFFF
        if not 0<delta<0x80000000:return False
        gate.update(last=frame,advanced=min(8,gate['advanced']+delta),progress=now)
        return gate['advanced']>=8

    def _acknowledge(self,net,event):
        if not net.acknowledge_world_event(event['revision'],self.network_context):
            self._context_moved(net)
            raise RuntimeError('La génération du monde a changé pendant une interaction.')

    def _queue(self,net,payload):
        """Queue into the attached room journal only; a room switch reattaches."""
        if net.queue_world_observation(payload,self.network_context):return True
        self._context_moved(net)
        return False

    def _context_moved(self,net):
        # The network thread selected another room/epoch meanwhile: this scene
        # is over. Detach and follow the new room instead of misfiling events.
        if net.get_world_context()!=self.network_context:
            raise NativeJobRejected('La zone réseau a changé pendant le partage.')

    def step(self,memory,snapshot,net,appearance,peers=()):
        online=net.mode=='host' or net.connection_state=='connected'
        network_context=net.get_world_context() if online else None
        room=(snapshot.course,snapshot.stage)
        if self.native and self.native.closed:self._forget_scene()
        if self.native and getattr(self.native.trial,'lost',False):
            # Its game restarted inside Azahar (appearance._forget_native): the
            # new process has none of it. Marked closed for enemies and life,
            # never closed through the new process's memory.
            self.native.closed=True;self._forget_scene()
            self._renderer_gate=None;self._seed_cuts=[];self.state='changing_room'
        self.loot.step(memory,snapshot,net,appearance)
        self.progress.step(memory,snapshot,net,appearance,peers)
        if self.error and not self._recover(appearance):return
        if getattr(appearance,'state',None) in ('error','restore_pending','restoring'):
            if self.native:self._detach(appearance)
            self._renderer_gate=None;self.state='waiting_renderer';return
        service=getattr(appearance,'grass_restore',None)
        if service and not service.closed:
            if not service.publish(net.get_mass_manifest() if online else []):
                self._fail('La restauration des herbes ne répond plus.');return
        if (not snapshot.in_game or network_context is None or network_context[0]!=room
                or not appearance.trial or not appearance.trial.installed):
            if self.native:self._detach(appearance)
            self.state='waiting_game' if not snapshot.in_game else 'waiting_session'
            return
        if getattr(appearance.trial,'room',None)!=room:
            if self.native:self._detach(appearance)
            self.state='changing_room';return
        now=time.monotonic()
        # Never read, seed or publish through a scroll: the departing room's
        # objects would be filed under the next room and block its journal.
        if self._scroll.active(memory.player_ptr):self._settled_at=now+SCROLL_SETTLE_SECONDS
        if now<self._settled_at:
            if self.native:self._detach(appearance)
            self.state='changing_room';return
        try:
            if self.native and (self.native.trial is not appearance.trial or self.network_context!=network_context):
                if not self._detach(appearance):return
            if self.native is None and not self._renderer_ready(appearance.trial,room,now):
                self.state='waiting_game';return
            if self.native is None or now>=self.next_scan:
                if not self._attach_scene(memory,net,appearance,room,network_context,now):return
            self._start_services(peers)
            self.combat.step(self.native,net.player_id)
            self._publish_local_cuts(net,room)
            self._seed_history(net)
            self.state=self._apply_remote_cut(net,now)
            self._carried_props(peers)
        except NativeJobRejected:
            # A scene transition can be accepted between the RPC survey and
            # the native allocator. The acknowledged refusal did no work and
            # is safe to retry after the renderer follows the new room.
            self._detach(appearance);self.next_scan=0;self.state='changing_room'
        except (ValueError,RuntimeError,OSError,TypeError,KeyError,IndexError) as exc:
            self._fail(str(exc) or type(exc).__name__)

    def _fail(self,message):
        now=time.monotonic()
        if now-self._failed_at>60:self._backoff=RECOVERY_SECONDS
        self._failed_at=now;self._retry_at=now+self._backoff
        self.error=message;self.state='error'
        # Retain uncertain allocations/entry ownership for graceful close.
        if self.native and self.native.allocation:self.bridge.write_u32(self.native.allocation+4,0)
        self.log('Partage du monde interrompu : '+self.error)

    def _recover(self,appearance):
        """Close the failed scene natively; the same step then reattaches.

        An uncertain allocation refuses to close and keeps its owner: the
        service then stays in error rather than reopen over unknown memory.
        """
        now=time.monotonic()
        if now<self._retry_at:return False
        if self.native and not self._detach(appearance):
            self._retry_at=now+self._backoff;return False
        self.log('Partage du monde relancé après : '+self.error)
        self.error='';self.recoveries+=1;self.next_scan=0;self.state='changing_room'
        self._backoff=min(RECOVERY_MAX,self._backoff*2)
        return True

    def _attach_scene(self,memory,net,appearance,room,network_context,now):
        """Rescan the room (once per second) and own a native world for it."""
        self.next_scan=now+1
        context,instances=self.reader.read(room)
        if context is None:
            if self.native:self._detach(appearance)
            self.state='changing_room';return False
        scene=(context,tuple(sorted({(i.handle,i.records) for i in instances.values()})))
        if self.native and (context!=self.context or scene!=self._scene):
            if not self._detach(appearance):return False
        if self.native is None:
            if getattr(appearance,'grass_restore',None) is None:
                from src.memory.grass_restore import NativeGrassRestore
                appearance.grass_restore=NativeGrassRestore(appearance.trial)
                appearance.grass_restore.start()
                appearance.grass_restore.publish(net.get_mass_manifest())
            if getattr(appearance,'loot_service',None) is None:
                from src.memory.shared_loot import NativeSharedLoot
                appearance.loot_service=NativeSharedLoot(appearance.trial)
                appearance.loot_service.start()
            # Only a presentation that owns a progress slot (the real one) gets the service.
            if (hasattr(appearance,'progress_service') and appearance.progress_service is None
                    and not self._progress_unavailable):
                from src.memory.shared_progress import NativeSharedProgress
                appearance.progress_service=NativeSharedProgress(appearance.trial)
                try:appearance.progress_service.start()
                except ValueError as exc:
                    # Refused before any write: the rest of the world still works.
                    appearance.progress_service=None;self._progress_unavailable=True
                    self.log('Progression partagée indisponible : '+str(exc))
            self._restore_targets={tuple(t) for r,t in net.get_mass_manifest()
                                   if tuple(r)==room and grass_profile(t[0])}
            self.native=NativeWorldEvents(appearance.trial,context,memory.player_ptr)
            appearance.world_extension=self.native
            self.native.start()
            self.combat.attach(self.native.player_handle,self._dynamic_hit_key)
            # Capture props already in their terminal state when joining or
            # reloading. Otherwise only FUTURE callbacks enter the journal,
            # leaving pre-existing differences between the two games. Never
            # infer burning, freezing, lifting or intermediate states; a
            # terminal shrub (8) is replayed as a silent cut.
            self._seed_cuts=[dict(kind='mass_cut',target=list(i.target),effect=0)
                             for i in instances.values()
                             if len(i.target)==9 and i.state==family(i.target[0]).terminal_state]
            # A new native scene must consume the room's history again.
            net.rewind_world_scene()
            self.network_context=network_context;self._scene=scene
            self.log('Partage des herbes, buissons et panneaux actif dans cette zone.')
        self.context=context;self.instances=instances
        return True

    def _start_services(self,peers):
        if not self.native.renew():raise RuntimeError('Le journal natif du monde ne répond plus.')
        if self.native.foliage is None:
            from src.memory.foliage_motion import NativeFoliageMotion
            self.native.foliage=NativeFoliageMotion(self.native)
            self.native.foliage.start()
        if self.native.captures is None:
            from src.memory.capture_hooks import NativeCaptureHooks
            self.native.captures=NativeCaptureHooks(self.native)
            self.native.captures.start()
        if not self.native.foliage.publish(peers):
            raise RuntimeError('Le feuillage attend son propriétaire natif.')
        if self.native.mirror is None:
            self.native.mirror=CarriedMirror(self.native)
            self.native.mirror.start()
        self._item_models(peers)

    def _item_models(self,peers):
        """Tools and bombs of the others. Cosmetic: a refusal never stops the world."""
        held={};bombs={}
        for peer in peers:
            if peer.get('in_game') is not True or (peer.get('course'),peer.get('stage'))!=self.context.room:continue
            pid=peer.get('player_id');render=peer.get('render_state',{})
            if render.get('items') and not render.get('merged'):held[pid]=render['items']
            if render.get('bombs'):bombs[pid]=render['bombs']
        try:
            items=getattr(self.native,'items',None)
            if items is None and (held or bombs):
                from src.memory.item_visuals import PrivateItemModels
                self.native.items=items=PrivateItemModels(self.native)
                items.start()
            if items is None:return
            items.set_targets(held,projectiles=bombs)
            # A write refused during a scene change is retried next step.
            items.place(held,create=False,projectiles=bombs)
            notes=items.drain_notes()
        except (RuntimeError,ValueError) as exc:
            notes=[f'Objets du compagnon non affichés : {exc}']
        for note in notes if isinstance(notes,list) else ():
            if note!=getattr(self,'_item_note',None):self.log(note);self._item_note=note

    def _carried_props(self,peers):
        """Publish what this Link carries; show what the others carry here."""
        if type(self.native.allocation) is not int or not self.native.allocation or self.native.mirror is None:return
        resolve=lambda handle:self.reader.actors._resolve_actor_address(handle,self.context)
        carried=read_local_carried(self.bridge,self.native.allocation,self.instances,resolve,self._carry_bindings)
        now=time.monotonic()
        if carried and not carried.get('broken'):self._last_carried=(carried,now)
        elif carried is None and self._last_carried:
            # The thrown actor is gone: it broke where it was last seen. Keep
            # saying so briefly, so the other games replay the break once.
            last,stamp=self._last_carried
            if now-stamp<=.5:carried=dict(last,broken=True)
            else:self._last_carried=None
        if bool(carried)!=bool(self.carried):
            self.log('Objet porté partagé.' if carried else 'Objet porté terminé.')
        self.carried=carried
        # Which instance each other player carries; its position is placed by
        # the companion presentation, from the same interpolated sample.
        targets={}
        for peer in peers:
            if peer.get('in_game') is not True or (peer.get('course'),peer.get('stage'))!=self.context.room:continue
            carried=validate_carried(peer.get('render_state',{}).get('carried'))
            if not carried:continue
            instance=self.instances.get(tuple(carried['target']))
            if instance is None and not carried.get('broken'):
                # Lifted in another room: draw the same kind of object found here.
                instance=next((v for v in self.instances.values()
                               if len(v.target)==9 and v.target[0]==carried['target'][0]),None)
            if instance is None:continue
            if carried.get('broken'):
                key=(peer.get('player_id'),tuple(carried['target']))
                if key not in self._broken_seen and self.native.mirror.request_break(instance,carried['pos']):
                    self._broken_seen[key]=now
                continue
            targets[peer.get('player_id')]=instance
        self._broken_seen={k:v for k,v in self._broken_seen.items() if now-v<10}
        self.native.mirror.set_targets(targets)
        if not targets and not self.native.mirror.place({}):
            raise RuntimeError('Les objets portés ne répondent plus.')
        rows=self.native.mirror.rows;notes=self.native.mirror.drain_notes()
        visible=len(rows) if isinstance(rows,dict) else 0
        if visible!=getattr(self,'_carried_visible',0):
            self.log(f'Objets portés par les autres affichés : {visible} (demandés : {len(targets)}).')
            self._carried_visible=visible
        for note in notes if isinstance(notes,list) else ():self.log(note)

    def _publish_local_cuts(self,net,room):
        """Turn native ring events into journal observations, then acknowledge them."""
        lookup={(v.handle,v.index):v for v in self.instances.values()}
        for event in self.native.captured():
            instance=lookup.get((event['handle'],event['index']))
            if instance is None:
                fresh_context,fresh=self.reader.read(room)
                if fresh_context!=self.context:
                    raise NativeJobRejected('La zone a changé pendant le relevé des herbes.')
                self.instances.update(fresh)
                lookup={(v.handle,v.index):v for v in self.instances.values()}
                instance=lookup.get((event['handle'],event['index']))
            payload=None
            if instance is not None:
                kind='prop_state' if actor_prop(instance.target[0]) and len(instance.target)==5 else 'mass_cut'
                payload=event_payload(dict(kind=kind,target=list(instance.target),effect=event['effect']))
            if payload is None:
                # One unrecognised observation is set aside with its evidence;
                # it must never stop the room (nor the enemies behind it).
                self._evidence('dropped-captures',net,dict(event=event,instance=instance and list(instance.target)))
                self.log('Interaction locale non reconnue, ignorée (voir dropped-captures).')
                self.dropped+=1
            elif not self._queue(net,payload):
                # Outgoing queue full: keep the native event and retry later.
                return
            else:self.observed+=1
            if not self.native.acknowledge_capture(1):raise RuntimeError('Accusé de lecture natif impossible.')

    def _apply_remote_cut(self,net,now):
        """Apply at most one journal event; returns the resulting state."""
        if self.pending_event:
            result=self.native.poll()
            if result is None:return 'applying'
            if result not in (1,2):
                self.next_scan=0;self.pending_event=None;return 'changing_room'
            self._acknowledge(net,self.pending_event)
            self.applied+=int(result==1);self.pending_event=None
        event=net.next_world_event(self.network_context)
        if not event:
            self.waiting_target=None;self._waiting_since=None;return 'active'
        target=tuple(event['payload']['target'])
        instance=self.instances.get(target)
        if instance is None:
            if self.waiting_target!=list(target) or self._waiting_since is None:
                self.waiting_target=list(target);self._waiting_since=now
            batch_known=len(target)==9 and any(len(k)==9 and k[:5]==target[:5] for k in self.instances)
            delay=MISSING_TARGET_SECONDS if batch_known or len(target)!=9 else MISSING_BATCH_SECONDS
            if now-self._waiting_since<delay:return 'waiting_object'
            self._skip_missing(net,event)
            return 'active'
        self.waiting_target=None;self._waiting_since=None
        effect=5 if instance.target in self._restore_targets else event['payload']['effect']
        if self.native.submit(instance,effect):self.pending_event=event
        return 'active'

    def _skip_missing(self,net,event):
        # The scene has been rescanned every second meanwhile: this target does
        # not exist here. Keep evidence, then let the rest of the room through.
        self._acknowledge(net,event);self.skipped+=1
        self._evidence('skipped-cuts',net,dict(event=event,
            local=sorted(list(k) for k in self.instances if k[0]==event['payload']['target'][0])[:64]))
        self.log('Interaction ignorée : objet absent de cette copie (voir skipped-cuts).')
        self.waiting_target=None;self._waiting_since=None

    def _evidence(self,name,net,row):
        try:
            FOLDER.mkdir(parents=True,exist_ok=True)
            with (FOLDER/f'{name}-{net.player_id}.jsonl').open('a',encoding='utf-8') as output:
                output.write(json.dumps(dict(at=datetime.now().isoformat(timespec='seconds'),
                    room=list(self.context.room) if self.context else None,**row))+'\n')
        except (OSError,TypeError,ValueError):pass

    def _seed_history(self,net):
        # Keep room-start input bounded so live cuts retain queue capacity.
        # Host semantic deduplication makes seeds from both games idempotent.
        for _ in range(min(4,len(self._seed_cuts))):
            if not self._queue(net,self._seed_cuts[0]):return
            self._seed_cuts.pop(0);self.seeded+=1

    def _dynamic_hit_key(self,row):
        from src.dynamic_enemies import binding_for
        binding=binding_for(self.bridge,row.get('handle'),row.get('room'),row.get('type'),
                            row.get('placement_id'),row.get('initial_position'))
        return binding['key'] if binding else None
