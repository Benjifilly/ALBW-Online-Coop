"""Défaite du groupe, dernier point commun et retour confirmé par chaque jeu."""
from copy import deepcopy
from src.network.party_life import anchor,anchor_order,event,event_id,status
from src.memory.party_native import NativePartyReturn
from src.memory.party_checkpoint import SavedPointReader,valid_point
from src.network.world_journal import integer


def valid_dump(value):
    if not isinstance(value,dict):return False
    wire={k:value.get(k) for k in ('fall','anchor','event','ready','returned','done','previous')}
    if status(wire) is None:return False
    fields=set(wire)|{'members','phase','penalty_serial','loss'}
    if set(value) not in (fields,fields|{'saved_point','vane_pending'}):return False
    for key in ('saved_point','vane_pending'):
        if value.get(key) is not None and not valid_point(value[key]):return False
    if value['phase'] not in ('wait','warp','place','arrived','done'):return False
    if not integer(value['penalty_serial'],0,2**31) or not integer(value['loss'],0,1499):return False
    if value['event'] and not value['penalty_serial']:return False
    if value['phase']!='wait' and not value['ready']:return False
    members=value['members']
    if not isinstance(members,dict) or len(members)>3:return False
    for key,m in members.items():
        if not isinstance(key,str) or not 1<=len(key)<=64 or not isinstance(m,dict) or set(m)!={'life','party'}:return False
        if m['life'] not in ('alive','dead','ghost') or status(m['party']) is None:return False
    return True


class PartyDefeat:
    def __init__(self,owner):
        self.owner=owner;self.fall=0;self.anchor={};self.event={};self.members={}
        self.ready=False;self.returned=False;self.done=False
        self.phase='wait';self.penalty_serial=0;self.loss=0;self.native=None
        self.previous=[]
        self.saved_point=None;self.vane_pending=None;self.reader=SavedPointReader(owner.bridge)

    @property
    def active(self):return bool(self.event) and not self.done

    @property
    def group(self):return bool(self.members)

    def wire(self):
        return dict(fall=self.fall,anchor=deepcopy(self.anchor),event=deepcopy(self.event),
                    ready=self.ready,returned=self.returned,done=self.done,previous=list(self.previous))

    def dump(self):
        return dict(self.wire(),members=deepcopy(self.members),phase=self.phase,
                    penalty_serial=self.penalty_serial,loss=self.loss,
                    saved_point=deepcopy(self.saved_point),vane_pending=deepcopy(self.vane_pending))

    def restore(self,value,fallen=False,previous=()):
        self.__init__(self.owner)
        if not value:
            self.fall=int(fallen);self.previous=list(previous);return
        for k in ('fall','anchor','event','ready','returned','done','members','phase','penalty_serial','loss','previous'):
            setattr(self,k,deepcopy(value[k]))
        self.saved_point=deepcopy(value.get('saved_point'));self.vane_pending=deepcopy(value.get('vane_pending'))
        # Les anciens points utilisaient la pose et l'entree courante de Link.
        if 'saved_point' not in value:self.anchor={}

    def _point(self,point,clock):
        # pos/yaw restent sur le fil pour compatibilité ; l'entrée native place Link.
        return anchor(dict(clock=clock,source=self.owner.life_id,room=point[:2],
                           entrance=point[2],pos=[0.,0.,0.],yaw=0.))

    def vane(self,snapshot,entrance):
        point=[snapshot.course,snapshot.stage,entrance]
        if valid_point(point):self.vane_pending=point

    def _checkpoint(self,snapshot,action):
        o=self.owner
        if self.active or snapshot.health<=0 or action not in (0,1) or o._event_running():return
        point=self.reader.read(snapshot)
        if point is None:return
        before=self.saved_point;self.saved_point=point
        activated=point==self.vane_pending
        if activated:self.vane_pending=None
        changed=before is not None and point!=before
        seed=not self.anchor or before is None and self.anchor['source']==o.life_id
        if changed or activated or seed:
            clock=self.anchor.get('clock',0)+int(bool(self.anchor))
            self.anchor=self._point(point,clock)

    def _observe(self,snapshot,peers,ready,action):
        o=self.owner
        if ready:
            present={o._key(p) for p in peers if p.get('in_game')}
            for p in peers:
                if not p.get('in_game'):continue
                key=o._key(p);part=p.get('life_party',{})
                if status(part) is None:
                    part=dict(fall=int(p.get('life') in ('dead','ghost')),anchor={},event={},ready=False,returned=False,done=False,previous=[])
                for old in part['previous']:
                    if old not in present and old!=o.life_id:self.members.pop(old,None);o.expected.discard(old)
                self.members[key]=dict(life=p.get('life','alive'),party=deepcopy(part))
                point=part['anchor']
                if (point and valid_point([*point['room'],point['entrance']])
                        and (not self.anchor or anchor_order(point)>anchor_order(self.anchor))):
                    self.anchor=deepcopy(point)
            # Quatre joueurs au maximum ; les spectateurs absents restent connus.
            if len(self.members)>3:
                live={o._key(p) for p in peers if p.get('in_game')}
                self.members={k:v for k,v in self.members.items() if k in live or v['life'] in ('dead','ghost')}
        self._checkpoint(snapshot,action)
        if ready:
            for p in peers:
                part=p.get('life_party',{});candidate=part.get('event',{})
                if not candidate or event(candidate) is None or o.life_id not in candidate['falls']:continue
                if candidate['falls'][o.life_id]!=self.fall:continue
                if self.done and self.event.get('falls',{}).get(o.life_id)==self.fall:continue
                if not self.event or (self.done and candidate['id']!=self.event['id']):
                    self._begin(candidate)

    def _begin(self,wipe):
        self.event=deepcopy(wipe);self.ready=self.returned=self.done=False
        self.phase='wait';self.native=None;self.loss=0;self.penalty_serial+=1
        self.owner.wiped=True
        self.owner.log('Le groupe est vaincu. Retour à la dernière girouette avec une perte de 15 % des rubis.')
        self.owner._save()

    def _create(self,snapshot,peers,ready):
        o=self.owner
        if (not self.group or o.life!='dead' or not o.continued
                or not o._no_survivor(peers,ready,last_death=True)):return
        if self.active:return
        if self.event and not all(v['party'].get('done') for v in self.members.values()):return
        falls={o.life_id:self.fall}
        for key,m in self.members.items():
            if m['life'] not in ('dead','ghost') or not m['party']['fall']:return
            falls[key]=m['party']['fall']
        if not 2<=len(falls)<=4:return
        if not self.anchor:return
        wipe=dict(id=event_id(falls,self.anchor),falls=falls,anchor=deepcopy(self.anchor))
        if self.event and self.event['id']==wipe['id']:return
        self._begin(wipe)

    def _all(self,peers,field):
        o=self.owner;states={o.life_id:self.wire()}
        states.update((o._key(p),p.get('life_party',{})) for p in peers if p.get('in_game'))
        return all(key in states and states[key].get('event',{}).get('id')==self.event['id']
                   and states[key].get(field) for key in self.event['falls'])

    def step(self,snapshot,peers,native,player,action,ready):
        o=self.owner
        self._observe(snapshot,peers,ready,action)
        if o.life=='dead' and (action==155 or snapshot.health>0 and action not in (152,153,154)):
            o.continued=True
        self._create(snapshot,peers,ready)
        if not self.active:
            if (not self.anchor and self.group and o.life=='dead' and o.continued
                    and o._no_survivor(peers,ready,last_death=True)):
                if native and snapshot.health>0:native.hold()
                return True
            return False
        # Le dernier mort conserve son écran natif : Continuer gère aussi Lavio.
        if snapshot.health==0:return True
        o._set('ghost');o.continued=False
        if native is None:
            o.bridge.write_u8(player+0x6BC,0);return True
        if not native.hold():return True
        if action not in (0,1) or o._event_running():return True
        if not self.ready:self.ready=True;o._save()
        if not ready or not self._all(peers,'ready'):return True
        if self.native is None or self.native.life is not native:self.native=NativePartyReturn(native)
        if self.phase=='wait':self.phase='warp';o._save()
        if self.phase=='warp':
            if self.native.step(1,self.penalty_serial,self.event['anchor']) is None:return True
            self.phase='place';o._save()
        if self.phase=='place':
            loss=self.native.step(2,self.penalty_serial,self.event['anchor'])
            if loss is None:return True
            self.loss=loss;self.returned=True;self.phase='arrived';o._save()
        if not self._all(peers,'returned'):return True
        self.done=True;self.phase='done';o.wiped=False;o.target=None
        o._set('alive')
        try:o._save()
        except (OSError,ValueError,TypeError):
            self.done=False;self.phase='arrived';o.wiped=True;o._set('ghost');raise
        native.spectate(False)
        o.log(f'Retour du groupe à la girouette : −{self.loss} rubis (15 %).')
        return True

    def notice(self):
        if self.active:
            if not self.ready:return 'Groupe vaincu · Choisissez Continuer dans le jeu du dernier vivant. Perte de 15 % des rubis.'
            if self.phase=='wait':return 'Groupe vaincu · En attente des compagnons pour repartir ensemble (−15 % des rubis).'
            return 'Retour du groupe à la dernière girouette… (−15 % des rubis)'
        if self.owner.wiped and self.group:
            if self.owner.continued and not self.anchor:return 'Vérification du point de reprise du groupe…'
            return 'Le groupe est tombé. Le dernier joueur doit choisir Continuer.'
        return ''
