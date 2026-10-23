"""Contrat du retour collectif : aucune adresse ni commande libre sur le réseau."""
from copy import deepcopy
import hashlib
import json
import math
from src.network.world_journal import integer,room_key


def identity(value):return isinstance(value,str) and 1<=len(value)<=64


def anchor(value):
    if not isinstance(value,dict) or set(value)!={'clock','source','room','entrance','pos','yaw'}:return None
    if not integer(value['clock'],0,2**31) or not identity(value['source']) or room_key(value['room']) is None:return None
    if not integer(value['entrance'],0,65535) or not isinstance(value['pos'],list) or len(value['pos'])!=3:return None
    if any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1e6 for x in [*value['pos'],value['yaw']]):return None
    return deepcopy(value)


def anchor_order(value):return value['clock'],value['source']


def event_id(falls,point):
    raw=json.dumps([falls,point],sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def event(value):
    if not isinstance(value,dict) or set(value)!={'id','falls','anchor'}:return None
    falls=value['falls'];point=anchor(value['anchor'])
    if not isinstance(falls,dict) or not 2<=len(falls)<=4 or point is None:return None
    if any(not identity(k) or not integer(v,1,2**31) for k,v in falls.items()):return None
    if value['id']!=event_id(falls,point):return None
    return dict(id=value['id'],falls=dict(falls),anchor=point)


def status(value):
    if not isinstance(value,dict) or set(value)!={'fall','anchor','event','ready','returned','done','previous'}:return None
    if not isinstance(value['previous'],list) or len(value['previous'])>8 or any(not identity(v) for v in value['previous']):return None
    if not integer(value['fall'],0,2**31) or any(type(value[k]) is not bool for k in ('ready','returned','done')):return None
    if value['anchor']!={} and anchor(value['anchor']) is None:return None
    if value['event']!={} and event(value['event']) is None:return None
    if (value['returned'] and not value['ready']) or (value['done'] and not value['returned']):return None
    if not value['event'] and any(value[k] for k in ('ready','returned','done')):return None
    return deepcopy(value)


def completed_return(players):
    """Barrière complète avant de renouveler les salles et les combats."""
    members={p.get('life_id'):p for p in players if p.get('in_game') and p.get('life_id')}
    for player in members.values():
        part=player.get('life_party',{});wipe=part.get('event',{})
        if not wipe:continue
        ids=wipe['falls']
        if all(k in members and members[k].get('life_party',{}).get('event',{}).get('id')==wipe['id']
               and members[k]['life_party'].get('returned') for k in ids):
            return wipe['id'],any(not members[k]['life_party'].get('done') for k in ids)
    return None,False
