"""One native companion per owner; UI requests are serviced on the memory thread."""
import json
import math
import os
from pathlib import Path
import threading
import time

from src.memory.model_colors import COLOR_FRAMES
from src.memory.model_palette import ModelPalette
from src.memory.remote_model import MAILBOX, RemoteModelTrial
from src.memory.native_jobs import NativeJobRejected
from src.memory.pose_buffer import PoseBuffer
from src.rpc.azahar_bridge import TITLE_ID_EUR


class InGameAppearance:
    def __init__(self,bridge,cache_path,emulator_pid=0,on_log=lambda message:None,marker_path=None):
        self.bridge=bridge
        self.cache_path=Path(cache_path)
        self.marker_path=Path(marker_path) if marker_path else self.cache_path.with_suffix('.patch-owner')
        self.emulator_pid=emulator_pid
        self.log=on_log
        self.requested=False
        self.trial=None
        self.palette=None
        self.equipment=None
        self.painting=None
        self.painting_enabled=True
        self.owner=None
        self.cache={}
        self.state='off'
        self.visible=False
        self.colour_applied=False
        self._next_status=0
        self._native_status=None
        self._palette_attempted=False
        self._failure=''
        self._allocation_blocked=False
        self._last_wall=None
        self._poses=PoseBuffer()
        self._local_painting=None
        self._local_painting_attempted=False
        self.effects=None
        self.world_extension=None
        self.grass_restore=None
        self.loot_service=None
        self.progress_service=None
        self._effects_supported=True
        from src.memory.room_scroll import RoomScrollReader
        self._scroll=RoomScrollReader(bridge)
        self._presented_peer_id=None
        self._presented_frame=None
        self._player=0;self._prewarm_retry=0
        # step() and the presentation thread never interleave renderer writes.
        self.present_lock=threading.RLock()

    def present_fast(self,peers):
        """Re-present the companion once per new game frame between heavy steps.

        The full step runs about every 25 ms; the game draws every 16.7 ms.
        Writing the interpolated pose (body, held items, carried object) as
        soon as the renderer has consumed a frame removes the 1-2 frame steps.
        Only already prepared outputs are refreshed here; creation, rooms,
        walls, native jobs and failures stay with step(). Safe to call from
        the presentation thread: it never waits behind a running step().
        """
        if not self.present_lock.acquire(blocking=False):return
        try:self._present_fast(peers)
        finally:self.present_lock.release()

    def _present_fast(self,peers):
        if (not self.trial or self.state!='visible' or self._failure or not self._presented_peer_id
                or self._allocation_blocked):return
        frame=self.bridge.read_u32(MAILBOX+0x50)
        if type(frame) is not int or frame==self._presented_frame:return
        room=self.trial.room
        peer=next((p for p in peers if p.get('player_id')==self._presented_peer_id and p.get('in_game')
                   and p.get('pose_verified') and (p.get('course'),p.get('stage'))==room),None)
        if peer is None:return
        try:
            sample=self._poses.sample(peer,time.monotonic())
            render=sample.get('render_state',{})
            if not self.trial.update(sample,COLOR_FRAMES.get(sample.get('tunic_color'),0.0)):return
            if render.get('merged'):
                # Painting: only a variant step() already built (no creation here).
                wall=render.get('wall')
                if (self.painting and isinstance(wall,dict) and wall.get('surface')
                        and wall.get('variant') in self.painting.models):
                    self._publish_painting(sample,self._player,room)
            else:
                gear=render.get('equipment')
                if gear and self.equipment and self.equipment.prepared(gear):self.equipment.publish(gear)
                self._present_carried(sample,create=False)
            self._present_items(sample)
            if self.effects and self.effects.room==tuple(room) and not self.effects.retired():
                self.effects.publish(render.get('effects',[]),sample.get('player_id'))
            self._presented_frame=frame
        except (NativeJobRejected,OSError,ValueError,RuntimeError) as exc:
            # Never close from the presentation thread: the sync thread may be
            # rebuilding the scene right now. step() owns real failures.
            message='Présentation sautée : '+str(exc)
            if message!=getattr(self,'_present_note',None):
                self._present_note=message;self.log(message)

    def _present_carried(self,sample,create=True):
        """Place what this peer carries, from the same interpolated sample as its body."""
        mirror=getattr(self.world_extension,'mirror',None)
        if mirror is None or mirror.closed:return
        carried=sample.get('render_state',{}).get('carried')
        positions={sample.get('player_id'):tuple(carried['pos'])} if carried and not carried.get('broken') else {}
        # A write refused during a scene change is retried next frame.
        mirror.place(positions,create=create)

    def _present_items(self,sample):
        items=getattr(self.world_extension,'items',None)
        if items is None or items.closed:return
        render=sample.get('render_state',{});pid=sample.get('player_id')
        held={pid:render.get('items',[])} if not render.get('merged') else {}
        # A bomb already thrown belongs to the world while its owner is painted.
        items.place(held,create=False,projectiles={pid:render.get('bombs',[])},presenter=True)

    def status(self):
        return dict(appearance_enabled=self.requested,appearance_status=self.state,
                    companion_visible=self.visible,colour_applied=self.colour_applied)

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.cache_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.cache),encoding='utf-8')
        temporary.replace(self.cache_path)

    def _start(self,memory,snapshot,key):
        self.marker_path.parent.mkdir(parents=True,exist_ok=True)
        if self.marker_path.exists():
            try:previous=json.loads(self.marker_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):raise RuntimeError('Un précédent rendu attend sa restauration.')
            if previous.get('emulator_pid')==self.emulator_pid and previous.get('companion_pid')!=os.getpid():
                raise RuntimeError('Un autre compagnon possède encore le rendu de cette copie du jeu.')
        try:self.cache=json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.cache={}
        same=self.cache.get('owner')==key
        reuse=self.cache.get('model',0) if same else 0
        palette=self.cache.get('palette',0) if same else 0
        if palette and (type(palette) is not int or not 0x14000000<=palette<=0x1C000000-0xB000
                or self.bridge.read_memory(palette+0x180,8)!=b'COOPPAL1'):
            palette=0
        self.cache=dict(owner=key,model=reuse,palette=palette,
                        attempted=self.cache.get('attempted',bool(reuse)) if same else False)
        self._allocation_blocked=bool(self.cache['attempted'] and not reuse)
        self._save_cache()
        self.marker_path.write_text(json.dumps(dict(companion_pid=os.getpid(),emulator_pid=self.emulator_pid)),encoding='utf-8')
        self.trial=RemoteModelTrial(self.bridge)
        self.palette=ModelPalette(self.bridge)
        self.owner=key
        self._native_status=None
        self._palette_attempted=False
        self._effects_supported=True
        self._next_status=0
        if not self.trial.install(memory.player_ptr,snapshot.course,snapshot.stage,reuse,palette,False):
            raise RuntimeError('Version du programme ou emplacement du rendu non reconnu.')

    def step(self,memory,snapshot,peers):
        with self.present_lock:self._step(memory,snapshot,peers)

    def _step(self,memory,snapshot,peers):
        if not self.requested:
            if self.trial and not self.close():
                return
            self.state='off'
            return
        if not getattr(self.bridge,'fast_memory',False):
            self.visible=self.colour_applied=False
            self.state='needs_launcher'
            return
        if self.bridge.attached_title_id!=TITLE_ID_EUR:
            self.visible=self.colour_applied=False
            self.state='unsupported' if self.bridge.attached_title_id else 'waiting_game'
            return
        if self._failure:
            self.state='error'
            return
        if not snapshot.in_game or not memory.player_ptr:
            if self.trial:
                self.trial.hide()
                self.bridge.write_u32(MAILBOX+0x5C,0)
                if self.equipment:self.equipment.hide()
                if self.painting:self.painting.hide()
                if self.effects:self.effects.suspend()
            self.visible=self.colour_applied=False
            self.state='waiting_game'
            return
        key=[self.emulator_pid,memory.player_ptr,self.bridge.read_u32(memory.player_ptr+0x2CC),
             self.bridge.read_u32(memory.player_ptr+0x600)]
        if key[2] is None or not key[3]:
            self.state='waiting_game'
            return
        try:
            if self.trial and key!=self.owner:
                if not self.close():
                    return
            if self.trial is None:
                if not snapshot.pose_verified:
                    self.visible=self.colour_applied=False
                    self.state='waiting_game'
                    return
                self._start(memory,snapshot,key)
            if self._hold_during_scroll(memory,snapshot,peers):return
            if not self.trial.set_room(snapshot.course,snapshot.stage):
                raise RuntimeError('Le héros a changé pendant la lecture de sa zone.')
            now=time.monotonic()
            if now>=self._next_status:
                self._next_status=now+.1
                self._native_status=self.trial.status()
                if not self._native_status:
                    raise RuntimeError('Le rendu ne répond plus.')
                status=self._native_status
                if status['state']==3:self._allocation_blocked=True
                if status['state']==4:
                    # The next snapshot will supply the new actor identity.
                    self.state='changing_room'
                    self.visible=False
                    return
                if status['model'] and status['model']!=self.cache['model']:
                    self.cache.update(model=status['model'],attempted=True)
                    self._save_cache()
                if status['palette'] and not self.palette.buffer:
                    self.cache['palette']=status['palette']
                    self._save_cache()
                    if not self.palette.prepare(status['palette'],key[3]) or not self.trial.bind_palette(self.palette.original):
                        raise RuntimeError('La texture violette de cette version n’est pas reconnue.')
            eligible=[p for p in peers if p.get('pose_verified') and p.get('in_game')
                      and (p.get('course'),p.get('stage'))==(snapshot.course,snapshot.stage)]
            # Keep a stable identity when the order of network packets changes.
            peer=min(eligible,key=lambda p:p.get('player_id','')) if eligible else None
            need_violet=snapshot.tunic_color=='purple' or (peer and peer.get('tunic_color')=='purple')
            need_palette=need_violet or snapshot.tunic_color!='green'
            if need_palette and not self.palette.buffer and not self._palette_attempted:
                self._palette_attempted=True
                if not self.bridge.write_u32(MAILBOX+0x60,1):
                    raise RuntimeError('La palette ne peut pas être préparée.')
            ready=not need_violet or bool(self.palette.buffer)
            if ready:
                if not self.trial.set_local_colour(snapshot.tunic_color):
                    raise RuntimeError('La couleur du héros ne répond plus.')
                self.colour_applied=True
                if self.palette.buffer and not self._local_painting_attempted:
                    self._local_painting_attempted=True
                    from src.memory.local_painting import LocalPaintingPalette
                    try:self._local_painting=LocalPaintingPalette(self.bridge,self.palette.buffer,memory.player_ptr)
                    except ValueError as exc:self.log('Couleur de fresque locale indisponible : '+str(exc))
                if self._local_painting:
                    try:self._local_painting.publish(snapshot.tunic_color)
                    except ValueError as exc:
                        # An unsupported local pigment must not take down the
                        # independent companion, equipment and network renderer.
                        self.log('Couleur de fresque locale indisponible : '+str(exc))
                        self._local_painting=None
            if peer and ready:
                peer=self._poses.sample(peer,now)
                if self._allocation_blocked:
                    self.trial.hide()
                    if self.painting:self.painting.hide()
                    if self.equipment:self.equipment.hide()
                    self.state='model_unavailable'
                    self.visible=False
                    return
                if not self.cache['attempted']:
                    # A constructor is never retried on the same owning heap.
                    self.cache['attempted']=True
                    self._save_cache()
                if not self.trial.update(peer,COLOR_FRAMES.get(peer.get('tunic_color'),0.0)):
                    raise RuntimeError('Le compagnon ne peut pas recevoir sa position.')
                self._present_carried(peer)
                self._present_items(peer)
                self.visible=bool(self._native_status and self._native_status['state']==1)
                self.state='visible' if self.visible else 'preparing'
                self._player=memory.player_ptr
                painting_active=self._publish_painting(peer,memory.player_ptr,(snapshot.course,snapshot.stage))
                if painting_active:self.visible=True;self.state='visible'
                elif self.painting_enabled:self._prewarm_painting(memory.player_ptr,(snapshot.course,snapshot.stage))
                gear=peer.get('render_state',{}).get('equipment')
                # Merging owns this decision even before a surface is ready.
                # A failed surface read is not an instruction to display gear.
                merged=peer.get('render_state',{}).get('merged',False)
                if gear and not merged:
                    if self.equipment is None:
                        from src.memory.equipment_model import PrivateEquipmentModel
                        self.equipment=PrivateEquipmentModel(self.trial,memory.player_ptr,(snapshot.course,snapshot.stage))
                    self.equipment.room=(snapshot.course,snapshot.stage)
                    self.equipment.publish(gear)
                elif self.equipment and not self.equipment.hide():
                    raise RuntimeError('L’équipement du compagnon ne peut pas être masqué.')
                self._publish_effects(peer,memory.player_ptr,(snapshot.course,snapshot.stage))
                self._presented_peer_id=peer.get('player_id')
            else:
                if not self.trial.hide():
                    raise RuntimeError('Le compagnon ne peut pas être masqué.')
                self.visible=False
                self._presented_peer_id=None
                self._last_wall=None
                self._poses.clear()
                if self.equipment and not self.equipment.hide():
                    raise RuntimeError('L’équipement du compagnon ne peut pas être masqué.')
                if self.painting and not self.painting.hide():
                    raise RuntimeError('La peinture du compagnon ne peut pas être masquée.')
                if self.effects and not self.effects.suspend():
                    raise RuntimeError('Les particules du compagnon ne peuvent pas être retirées.')
                self.state='other_room' if peers else 'waiting_peer'
                if not ready:self.state='preparing'
        except NativeJobRejected:
            # A request refused before entering native code is a normal scene
            # race, not a permanent appearance failure. Cleanup is still gated.
            if self.close():
                self.state='changing_room'
            else:
                self.state='restore_pending'
        except (OSError,ValueError,RuntimeError) as exc:
            self._failure=str(exc)
            self.log('Rendu en jeu interrompu : '+str(exc))
            self.close()
            self.state='error'

    def _hold_during_scroll(self,memory,snapshot,peers):
        if not self.visible or not self._presented_peer_id:return False
        room=(snapshot.course,snapshot.stage)
        old_room=self.trial.room
        if room==old_room or room[0]!=old_room[0]:return False
        # Preserve only the player we were actually drawing in the departing
        # room. Disconnects, unknown poses and unrelated rooms never get a hold.
        peer=next((p for p in peers if p.get('player_id')==self._presented_peer_id
                   and p.get('in_game') and p.get('pose_verified')
                   and (p.get('course'),p.get('stage'))==old_room),None)
        if peer is None or not self._scroll.active(memory.player_ptr):return False
        if not self.trial.keep_visible():return False
        for renderer in (self.equipment,self.painting):
            if renderer and not renderer.keep_visible():raise RuntimeError('Le compagnon ne peut pas terminer son défilement.')
        if self.effects:
            if not self.effects.suspend():raise RuntimeError('Les particules ne peuvent pas être retirées du défilement.')
            # Resolve existing asynchronous work, never create in the old room.
            self.effects._finish_job(closing=True)
        if self.colour_applied and not self.trial.set_local_colour(snapshot.tunic_color):
            raise RuntimeError('La couleur ne peut pas être conservée pendant le défilement.')
        self.state='visible'
        return True

    def _publish_painting(self,peer,player,room):
        render=peer.get('render_state',{})
        state=render.get('wall')
        key=(player,tuple(room),peer.get('player_id'))
        now=time.monotonic()
        merged=self.painting_enabled and render.get('merged')
        active=(merged and isinstance(state,dict)
                and type(state.get('variant')) is int and state['variant'] in (0,1)
                and bool(state.get('surface')))
        position=tuple(peer.get(axis) for axis in ('x','y','z'))
        if active:
            self._last_wall=(key,now,position,state)
        elif merged and self._last_wall:
            previous,stamp,origin,old=self._last_wall
            # Keep a coherent surface through a missed double-buffer read.
            # Never stretch it to a new location, peer, actor or room.
            near=all(type(a) in (int,float) and type(b) in (int,float)
                     and math.isfinite(a) and math.isfinite(b) for a,b in zip(position,origin))
            near=near and sum((a-b)**2 for a,b in zip(position,origin))<=.25**2
            if previous==key and now-stamp<=.25 and near and (
                    not isinstance(state,dict) or state.get('variant')==old['variant']):
                state=old
                active=True
        if not merged:self._last_wall=None
        if not active:
            if self.painting and not self.painting.hide():
                raise RuntimeError('La peinture du compagnon ne peut pas être masquée.')
            return False
        if self.painting is None:
            from src.memory.wall_model import PrivateWallModel
            self.painting=PrivateWallModel(self.trial,player,room)
        self.painting.room=tuple(room)
        self.painting.publish(state,colour=peer.get('tunic_color','green'))
        return True

    def _prewarm_painting(self,player,room):
        """Build the painted companion before it is needed (one variant per step).

        Building it on the first merge took a long run of native jobs: the
        companion vanished for about half a second, then faded in.
        """
        now=time.monotonic()
        if now<self._prewarm_retry:return
        if self.painting is None:
            from src.memory.wall_model import PrivateWallModel
            self.painting=PrivateWallModel(self.trial,player,room)
        self.painting.room=tuple(room)
        for variant in (0,1):
            if variant in self.painting.models:continue
            try:
                count=self.painting.source(variant)['count']
                identity=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.]
                self.painting.prepare(dict(variant=variant,matrix=identity,scale=[1.,1.,1.],
                                           meshes=[True]*count,animations=[None]*3))
                self.log(f'Peinture du compagnon préparée à l’avance (forme {variant}).')
            except (RuntimeError,ValueError,NativeJobRejected) as exc:
                # Local wall resources load on the first local merge; retry later.
                if not self._prewarm_retry:self.log('Peinture du compagnon pas encore préparable : '+str(exc))
                self._prewarm_retry=now+10
            return

    def _publish_effects(self,peer,player,room):
        if not self._effects_supported:return
        if self.effects and (self.effects.room!=tuple(room) or self.effects.retired()):
            if not self.effects.close():raise RuntimeError('Les effets précédents attendent leur retrait.')
            self.effects=None
        state=peer.get('render_state',{}).get('effects',[])
        mirror=getattr(self.world_extension,'mirror',None)
        bursts=mirror.drain_effects() if mirror is not None and not mirror.closed else []
        if not isinstance(bursts,list):bursts=[]
        if not state and not bursts and self.effects is None:return
        try:
            if self.effects is None:
                from src.memory.effect_model import PrivateAttackEffects
                self.effects=PrivateAttackEffects(self.trial,player,room)
            if bursts:self.effects.queue_one_shots(bursts)
            self.effects.publish(state,peer.get('player_id'))
        except ValueError as exc:
            # An unknown optional asset must not remove a working body/fresco.
            if self.effects and not self.effects.close():raise RuntimeError('Les particules attendent leur retrait.') from exc
            self.effects=None;self._effects_supported=False
            self.log('Effets d’épée indisponibles : '+str(exc))

    def close(self):
        with self.present_lock:return self._close()

    def _close(self):
        if self.world_extension:
            try:cleaned=self.world_extension.close()
            except (RuntimeError,ValueError,OSError):cleaned=False
            if not cleaned:
                self.state='restoring';return False
            self.world_extension=None
        if self.loot_service:
            if not self.loot_service.close():
                self.state='restoring';return False
            self.loot_service=None
        if self.progress_service:
            if not self.progress_service.close():
                self.state='restoring';return False
            self.progress_service=None
        if self.grass_restore:
            if not self.grass_restore.close():
                self.state='restoring';return False
            self.grass_restore=None
        self.visible=self.colour_applied=False
        if self.trial is None:
            return True
        if self.effects:
            try:cleaned=self.effects.close()
            except (OSError,ValueError,RuntimeError) as exc:
                cleaned=False;self.log('Effets d’épée en attente de retrait : '+str(exc))
            if not cleaned:self.state='restore_pending';return False
            self.effects=None
        if self.painting:
            try:cleaned=self.painting.close()
            except (OSError,ValueError,RuntimeError) as exc:
                cleaned=False
                self.log('Peinture en attente de retrait : '+str(exc))
            if not cleaned:
                self.state='restore_pending'
                return False
            self.painting=None
        if self.equipment:
            try:cleaned=self.equipment.close()
            except (OSError,ValueError,RuntimeError) as exc:
                cleaned=False
                self.log('Équipement en attente de retrait : '+str(exc))
            if not cleaned:
                self.state='restore_pending'
                return False
            self.equipment=None
        if not self.trial.restore():
            self.state='restore_pending'
            return False
        # The game-thread removal barrier supplies the final model pointer,
        # including a constructor completed between the last poll and hiding.
        # A zero pointer certifies that this hook owns no reusable body.
        self.cache['model']=self.trial.final_model
        self.cache['attempted']=bool(self.trial.final_model)
        self.cache['palette']=0
        try:self._save_cache()
        except OSError as exc:
            self.log('Le rendu est restauré, mais son état local n’a pas pu être enregistré : '+str(exc))
        try:self.marker_path.unlink(missing_ok=True)
        except OSError as exc:
            self.log('Le rendu est restauré, mais son marqueur local n’a pas pu être retiré : '+str(exc))
        self.trial=None
        self.palette=None
        self.owner=None
        self._native_status=None
        self._last_wall=None
        self._poses.clear()
        self._local_painting=None
        self._local_painting_attempted=False
        self._presented_peer_id=None
        return True

    def set_enabled(self,enabled):
        self.requested=bool(enabled)
        # A new explicit request can retry a transport error; heap attempts stay cached.
        if enabled:self._failure=''


def appearance_cache(profile):
    root=Path(__file__).resolve().parents[2]
    # Reuse the single model allocated by the earlier opted-in player-2 trials.
    if profile=='local-p2':return root/'.local/coop-lab/render-model-cache.json'
    return root/'.local/profiles'/((profile or 'default')+'.render.json')
