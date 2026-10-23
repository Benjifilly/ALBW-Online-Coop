"""Shared death: a fallen Link spectates until a companion activates a weathervane.

life (in this player's snapshot, so every companion sees it):
- 'alive';
- 'dead': his game over is on screen (action 152/153 with no HP left). A fairy
  (action 154) brings him straight back;
- 'ghost': he chose Continue (action 155) while a companion was alive. Once
  the game has loaded his restart point, he becomes a hidden spectator riding
  that companion (player_life.arm), warped after him from room to room.

A living Link that activates a weathervane (its state machine entering Save)
increments revive_serial; a ghost seeing a companion's serial grow comes back,
in that companion's room, where he stands. With nobody left alive, the group
waits for Continue and returns together with the rupee penalty (party_life).
"""
import math
import struct
import time
import secrets
from src.memory.player_life import NativePlayerLife
from src.memory.party_life import PartyDefeat

DYING={152,153}
FAIRY=154
CONTINUE=155
# Standing or walking: the only actions a spectator is frozen in (player_life.arm).
IDLE=(0,1)
VANE_TYPE=280
VANE_VTABLE=0x6CFC28
VANE_STATE=0x78C
VANE_SAVE=2
VANE_REACH=2.5
EXAMINE=149
WARP_RETRY=3.0
# Farther than this from his companion for STUCK seconds: reload at his entrance.
FAR=4.0
STUCK=1.5
SURVEY_EVERY=3.0


def life_notice(life,peers):
    """The co-op menu's reminder of who waits for a weathervane, or ''."""
    if life=='ghost':
        return 'Vous êtes tombé. Un compagnon doit activer une girouette pour vous ranimer.'
    if life=='dead':
        return 'Vous êtes tombé. Choisissez Continuer pour suivre vos compagnons en attendant une girouette.'
    fallen=[p.get('name','Un compagnon') for p in peers if p.get('in_game') and p.get('life') in ('dead','ghost')]
    if not fallen:return ''
    who=fallen[0]+' est tombé' if len(fallen)==1 else ', '.join(fallen[:-1])+' et '+fallen[-1]+' sont tombés'
    return f'{who}. Activez une girouette pour ranimer vos compagnons.'


class LifeSynchronizer:
    def __init__(self,bridge,log=lambda message:None,*,checkpoint=None):
        self.bridge=bridge;self.log=log
        self.life='alive';self.revive_serial=0;self.error=''
        self.native=None;self.continued=False;self.target=None;self.far_since=self.far_entrance=None
        self.seen_serials={};self.peer_lives={};self.warp=None;self.warp_after=0
        self.vanes=[];self.vane_room=None;self.vane_using=False;self.survey_after=0;self.vane_entrance=None
        self.checkpoint=checkpoint;self.stamp=None;self.life_id=secrets.token_hex(16);self.release_serial=0
        self.seen_releases={};self.expected=set();self.wiped=False
        self._borrow_life=False
        self.party=PartyDefeat(self)
        if checkpoint and checkpoint.record:self._restore(checkpoint.record)

    def _restore(self,record):
        for key in ('life','life_id','revive_serial','release_serial','continued','target','vane_using','wiped'):
            setattr(self,key,record[key])
        self.seen_serials=dict(record['seen_serials']);self.seen_releases=dict(record['seen_releases'])
        self.expected=set(record['expected']);self.warp=None;self.warp_after=0
        self.vanes=[];self.vane_room=None;self.survey_after=0;self.peer_lives={}
        self.party.restore(record.get('party'),self.life in ('dead','ghost'),record.get('previous',()))

    def _resume(self,world):
        if self.checkpoint is None:return True
        record=self.checkpoint.read()
        if record is None:return False
        if self.stamp!=record['stamp']:
            previous=self.native
            was_ghost=self.life=='ghost';self._restore(record);self.stamp=record['stamp']
            self._borrow_life=self.life=='ghost'
            if was_ghost and self.life!='ghost' and previous and previous._ready():previous.spectate(False)
            if was_ghost or self.life=='ghost':
                native=self._native(world)
                if native:native.spectate(self.life=='ghost')
            if self.life=='ghost':self.log('Spectateur retrouvé : en attente du groupe et d’une girouette.')
            # Le snapshot peut précéder ce chargement de quelques millisecondes.
            self._save();return False
        return True

    def _save(self):
        if self.checkpoint is None or self.stamp is None:return
        record=dict(version=2,stamp=self.stamp,expected=sorted(self.expected),party=self.party.dump(),
                    seen_serials=dict(self.seen_serials),seen_releases=dict(self.seen_releases))
        for key in ('life','life_id','revive_serial','release_serial','continued','target','vane_using','wiped'):
            record[key]=getattr(self,key)
        self.checkpoint.save(record)

    @staticmethod
    def _key(peer):return peer.get('life_id') or peer['player_id']

    def _no_survivor(self,peers,ready,*,last_death=False):
        if not ready or self._alive_companions(peers):return False
        present={self._key(p) for p in peers if p.get('in_game')}
        # Un joueur absent n'est pas une preuve de mort, surtout à la reconnexion.
        return (bool(present) or last_death) and self.expected<=present

    def _remember_wipe(self):
        if not self.wiped:self.release_serial+=1;self.wiped=True

    # -- game reads --------------------------------------------------------
    def _action(self,player):
        b=self.bridge;action=b.read_u32(player+0x614)
        return b.read_u16(action+0x42) if action and 0x08000000<=action<0x1F000000 else None

    def _event_running(self):
        b=self.bridge;events=b.read_u32(0x70B728)
        return bool(events and b.read_u32(events+0x44))

    def _vane_activated(self,snapshot,player):
        """This Link just activated a weathervane near him: it entered its Save
        state, or he started using it (action 149, its save dialog, seen 23/10).
        One activation counts once, however long the dialog lasts."""
        using=self._vane_in_use(snapshot,player)
        started=using and not self.vane_using;self.vane_using=using
        return started

    def _vane_in_use(self,snapshot,player):
        room=(snapshot.course,snapshot.stage);now=time.monotonic()
        if room!=self.vane_room or now>=self.survey_after:
            from src.memory.world_actors import ActorSurvey
            try:
                table=self.bridge.read_u32(self.bridge.read_u32(0x706E20)+0x3DBC0)
                found=[a for a in ActorSurvey(self.bridge).read(room)['actors'] if a['type']==VANE_TYPE]
                self.vanes=[self.bridge.read_u32(table+(a['handle']>>22)*4) for a in found]
            except (ValueError,TypeError,OSError):self.vanes=[]
            self.vane_room=room;self.survey_after=now+SURVEY_EVERY
        link=(snapshot.x,snapshot.z);examining=self._action(player)==EXAMINE
        for vane in self.vanes:
            raw=self.bridge.read_memory(vane,4)
            if not raw or struct.unpack('<I',raw)[0]!=VANE_VTABLE:continue
            state=struct.unpack('<b',self.bridge.read_memory(vane+VANE_STATE,1))[0]
            x,_,z=struct.unpack('<3f',self.bridge.read_memory(vane+0x3D0,12))
            if (x-link[0])**2+(z-link[1])**2<=VANE_REACH**2 and (state==VANE_SAVE or examining):
                self.vane_entrance=self.bridge.read_u32(vane+0x7DC)
                return True
        return False

    # -- decisions ---------------------------------------------------------
    def _alive_companions(self,peers):
        return [p for p in peers if p.get('in_game') and p.get('life','alive')=='alive' and p.get('health',0)>0]

    def _set(self,life,message=None):
        if life!=self.life:
            self.life=life
            if message:self.log(message)

    def step(self,snapshot,peers,world,player,*,roster_ready=True):
        """One sync-loop step; returns the snapshot fields to publish."""
        try:
            if not self._resume(world):
                if self.life=='ghost' and snapshot.in_game and player:
                    native=self._native(world)
                    if native:native.spectate(True)
                    else:self.bridge.write_u8(player+0x6BC,0)
                return self._fields()
            self._step(snapshot,peers,world,player,roster_ready)
            if self.life=='dead' and self._no_survivor(peers,roster_ready,last_death=True):self._remember_wipe()
            if self.life=='alive':self.wiped=False
            self._save()
            self.error=''
        except (OSError,TypeError,ValueError,struct.error) as exc:
            error=f'{type(exc).__name__}: {exc}'
            if error!=self.error:self.log(f'Reprise du spectateur indisponible : {error}')
            self.error=error
        return self._fields()

    def _fields(self):
        return dict(life=self.life,revive_serial=self.revive_serial,
                    life_id=self.life_id,release_serial=self.release_serial,life_party=self.party.wire())

    def _native(self,world):
        if world is None or world.closed:
            if self._borrow_life:
                if self.native is None or not self.native._ready():self.native=NativePlayerLife.recover(self.bridge)
                return self.native
            self.native=None;return None
        self._borrow_life=False
        if self.native is None or self.native.world is not world:self.native=NativePlayerLife(world)
        return self.native

    def _step(self,snapshot,peers,world,player,roster_ready):
        native=self._native(world)
        alive=self._alive_companions(peers)
        if self.life=='alive' and roster_ready:
            for peer in peers:
                if not peer.get('in_game'):continue
                key=self._key(peer)
                if peer in alive:self.expected.add(key)
                else:self.expected.discard(key)
        for peer in peers:
            life=peer.get('life','alive');key=self._key(peer);before=self.peer_lives.get(key,'alive')
            self.peer_lives[key]=life
            if life=='dead' and before=='alive':
                self.log(f"{peer.get('name','Un compagnon')} est tombé. Activez une girouette pour le ranimer.")
            elif life=='alive' and before=='ghost':
                self.log(f"{peer.get('name','Un compagnon')} est de retour.")
        for peer in peers:
            key=self._key(peer)
            if roster_ready and self.life in ('dead','ghost') and peer.get('release_serial',0)>self.seen_releases.get(key,peer.get('release_serial',0)):
                self._remember_wipe()
            # Identité de partie, pas celle du programme compagnon redémarré.
            if self.life=='alive' or key not in self.seen_serials:
                self.seen_serials[key]=peer.get('revive_serial',0)
            if self.life=='alive' or key not in self.seen_releases:
                self.seen_releases[key]=peer.get('release_serial',0)
        if not snapshot.in_game or not player:
            return
        action=self._action(player)
        if self.party.step(snapshot,peers,native,player,action,roster_ready):return
        if alive:self.wiped=False
        if self.life=='alive':
            if action in DYING and snapshot.health==0:
                self.party.fall+=1
                self.expected.update(self._key(p) for p in alive)
                self._set('dead','Vous êtes tombé. Un compagnon doit activer une girouette pour vous ranimer.'
                          if alive else None)
            elif native and self._vane_activated(snapshot,player):
                self.revive_serial+=1
                self.party.vane(snapshot,self.vane_entrance)
                if any(p.get('life') in ('dead','ghost') for p in peers):
                    self.log('Girouette activée : vos compagnons tombés reviennent à vos côtés.')
            return
        if self.life=='dead':
            if action==FAIRY and not self.continued:
                self._set('alive');return
            # Continue does not always show its action 155 (23/10): any HP back
            # outside a fairy is the restart point being loaded.
            if action==CONTINUE or (snapshot.health>0 and action not in DYING):self.continued=True
            if not self.continued:return
            if action in IDLE and not self._event_running():
                # Up again at the restart point: spectate a living companion.
                if not self.party.group and not alive and not self.expected and roster_ready:
                    self.continued=False;self._set('alive');return
                self.expected.update(self._key(p) for p in alive)
                self.target=self._key(alive[0]) if alive else None;self.continued=False
                self._set('ghost',f"En attente d’une girouette : vous suivez {alive[0]['name']}."
                          if alive else 'Spectateur : en attente de la reconnexion du groupe.')
                if native:native.spectate(True)
            return
        # Spectator.
        if native:native.spectate(True)
        else:self.bridge.write_u8(player+0x6BC,0)
        if not roster_ready:return
        if self.wiped or self._no_survivor(peers,roster_ready):
            self._remember_wipe()
            # Le dernier mort publiera le retour collectif après Continuer.
            return
        if not alive:return
        self.expected.update(self._key(p) for p in alive)
        # La girouette compte aussi si son utilisateur est retombé depuis.
        reviver=next((p for p in peers if p.get('in_game')
                     and p.get('revive_serial',0)>self.seen_serials.get(self._key(p),0)),None)
        follow=next((p for p in alive if self._key(p)==self.target),alive[0])
        if reviver in alive:follow=reviver
        self.target=self._key(follow)
        if native is None:
            # Between scenes the hook is not installed yet: stay hidden.
            self.bridge.write_u8(player+0x6BC,0);return
        native.spectate(True)
        here=(snapshot.course,snapshot.stage)==(follow.get('course'),follow.get('stage'))
        if not here or self._out_of_reach(snapshot,follow):
            self._follow(native,follow);return
        self.warp=None
        if reviver is not None:
            for p in alive:self.seen_serials[self._key(p)]=p.get('revive_serial',0)
            native.spectate(False);self.target=None
            self._set('alive',f"{reviver['name']} vous a ranimé à la girouette.")

    def _out_of_reach(self,snapshot,follow):
        """Same room, yet he has not caught up with his companion for STUCK
        seconds: a scene reloaded in place (the Lost Woods loop back into the
        same room, 23/10) or walls between them, as his glide is a straight line
        resolved by his own collision. He then reloads the room at the
        companion's entrance, once per entrance until he catches up."""
        try:gap=math.hypot(snapshot.x-follow['x'],snapshot.z-follow['z'])
        except (KeyError,TypeError):return False
        if gap<=FAR:self.far_since=self.far_entrance=None;return False
        now=time.monotonic();entrance=follow.get('entrance')
        if self.far_since is None:self.far_since=now
        if now-self.far_since<STUCK or entrance==self.far_entrance:return False
        self.far_entrance=entrance;self.far_since=None
        return True

    def _follow(self,native,follow):
        """Warp after the followed companion when his room differs."""
        now=time.monotonic()
        if self.warp is not None:
            result=native.warp_result(self.warp)
            if result is None and now<self.warp_after:return
            self.warp=None
        if now<self.warp_after or self._event_running():return
        entrance=follow.get('entrance')
        if type(entrance) is not int:return
        self.warp=native.warp(follow['course'],follow['stage'],entrance)
        self.warp_after=now+WARP_RETRY
