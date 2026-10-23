"""Shared enemies of every room, kept by the host's node for all players.

A game only updates the enemies within its calc range (~20 units) of its own
Link: the others stand frozen. So no single game can be the authority of a
room. Instead, per enemy:

- its owner is the nearest player whose game updates it. That game's native
  AI runs untouched (it chases and hits that player for real); every other
  game follows its reported pose;
- its HP is a ledger: the first reported value minus every player's native
  losses, each acknowledged once. Each game reconciles down to it natively;
- an enemy that every living player's game lacks is dead: a game remembers
  the enemies it killed earlier, while one that reloaded the room (a fallen
  Link coming back) brings them back to life (23/10).

Rooms are identified by the world journal epoch, so a room left empty starts
again with the game's own respawned enemies. Nothing here touches memory.
"""
import math
from src.network.enemy_frames import report_for_wire,states_for_wire,LIMIT

STALE=1.0      # seconds after which a player's report no longer counts
HANDOVER=3.0   # how much nearer another player must be to take an enemy over
HOLD=.5        # seconds an owner keeps an enemy before it can change again
ENEMIES=4*LIMIT
SETTLE=2.0     # seconds a player reports a room before its absences count
ABSENT=1.0     # seconds an enemy must be missing from every living game


class RoomEnemies:
    def __init__(self):
        self.reports={};self.enemies={};self.acked={};self.revision=0

    def accept(self,pid,report,now):
        last=self.acked.get(pid,0)
        fresh=[loss for loss in report['losses'] if loss['id']>last]
        for key,seen in report['enemies'].items():
            entry=self.enemies.get(key)
            if entry is None:
                if len(self.enemies)>=ENEMIES:continue
                # A loss in this very report already lowered the value read.
                base=seen['hp']+sum(l['damage'] for l in fresh if l['key']==key)
                entry=self.enemies[key]=dict(base=min(base,seen['max_hp']),max_hp=seen['max_hp'],damage=0,
                                             profile=0,owner=pid,since=now,pos=seen['pos'],yaw=seen['yaw'],
                                             frame=report['frame'])
            if entry['owner']==pid and entry['max_hp']==seen['max_hp']:
                entry.update(pos=seen['pos'],yaw=seen['yaw'],frame=report['frame'])
        for loss in fresh:
            entry=self.enemies.get(loss['key'])
            if entry is not None:
                entry['damage']+=loss['damage']
                if loss.get('profile'):entry['profile']=loss['profile']
            last=loss['id']
        self.acked[pid]=last
        # Back in the room after a gap: its first scans may again be partial.
        old=self.reports.get(pid);since=old[3] if old and now-old[0]<=STALE else now
        self.reports[pid]=(now,report['enemies'],report.get('complete') is True,since)

    def assign(self,now,positions):
        """Hand each enemy to the nearest player whose game updates it, and
        kill the ones no living player's game has any more."""
        fresh={pid:r for pid,r in self.reports.items() if pid in positions and now-r[0]<=STALE}
        judges=[pid for pid,r in fresh.items() if r[2] and now-r[3]>=SETTLE]
        everyone=bool(positions) and len(judges)==len(positions)
        for key,entry in self.enemies.items():
            holders={pid for pid,r in fresh.items() if key in r[1]}
            if everyone and not holders and entry['base']>entry['damage']:
                entry.setdefault('absent',now)
                if now-entry['absent']>=ABSENT:entry['damage']=entry['base']
            else:entry.pop('absent',None)
            x,_,z=entry['pos']
            near={pid:math.hypot(p[0]-x,p[1]-z) for pid,p in positions.items()}
            active={pid:d for pid,d in near.items()
                    if pid in self.reports and now-self.reports[pid][0]<=STALE
                    and self.reports[pid][1].get(key,{}).get('active')}
            owner=entry['owner']
            if not active:
                # Frozen everywhere: only replace an owner that left the room,
                # preferably by a player whose game has this enemy at all.
                if (owner not in near or owner not in holders) and near:
                    keep={pid:d for pid,d in near.items() if pid in holders} or near
                    entry.update(owner=min(keep,key=keep.get),since=now)
                continue
            best=min(active,key=active.get)
            if (owner not in active or best!=owner and now-entry['since']>=HOLD
                    and active[best]<active[owner]-HANDOVER):
                entry.update(owner=best,since=now)

    def states(self):
        result={}
        for key,entry in self.enemies.items():
            hp=max(0,entry['base']-entry['damage'])
            state=dict(hp=hp,max_hp=entry['max_hp'],dead=hp==0,pos=list(entry['pos']),
                       yaw=entry['yaw'],owner=entry['owner'])
            if entry['profile']:state['profile']=entry['profile']
            if entry['frame'] is not None:state['t']=entry['frame']
            result[key]=state
        return result


class EnemyHub:
    def __init__(self):self.rooms={}

    def accept(self,pid,context,report,now):
        clean=report_for_wire(report,context[0])
        if clean is None:return False
        room=self.rooms.setdefault(context,RoomEnemies())
        room.accept(pid,clean,now);return True

    def frame(self,pid,context,positions,now):
        """The shared states of `context` for one of its players, or None."""
        room=self.rooms.get(context)
        if room is None:return None
        room.assign(now,positions)
        states=room.states()
        # Above the datagram budget, keep the enemies nearest this player.
        if len(states)>LIMIT and pid in positions:
            x,z=positions[pid]
            states=dict(sorted(states.items(),key=lambda kv:math.hypot(kv[1]['pos'][0]-x,kv[1]['pos'][2]-z))[:LIMIT])
        clean=states_for_wire(states,context[0])
        if clean is None:return None
        room.revision+=1
        return dict(context=context,revision=room.revision,states=clean,acked={pid:room.acked.get(pid,0)})

    def retain(self,contexts):
        """Forget the rooms whose world epoch ended (everybody left)."""
        for context in list(self.rooms):
            if context not in contexts:del self.rooms[context]

    def forget(self,pid):
        """A player rejoined with a fresh channel: its loss serials restart."""
        for room in self.rooms.values():
            room.acked.pop(pid,None);room.reports.pop(pid,None)
