"""Single in-flight native HP reconciliation; never an RPC actor-HP writer.

One command is claimed in the game's normal enemy update, committed through
the native impact pipeline with the attack profile that caused the loss (or
one the enemy is sensitive to), then acknowledged after its native subtraction.
The game retains its resistance/cooldown/reaction and death processing.
"""
import time
from src.memory.enemy_impact_replay import NativeEnemyImpactReplay
from src.memory.enemy_sync import EnemySynchronizer


class NativeEnemyHealth:
    def __init__(self,world,player):
        self.world=world;self.player=player;self.pending=None
        self.completed={};self.credits={};self.retry_after=0
        if world.impact is not None:
            raise ValueError('Un autre propriétaire utilise déjà les impacts natifs.')
        world.impact=NativeEnemyImpactReplay(world)
        world.impact.start(reconciliation=True)
        self.replay=world.impact

    def poll(self):
        if self.pending is None:return
        result=self.replay.poll()
        if result is None:return
        item=self.pending;self.pending=None
        if result['outcome']=='retained':
            native=result['native']
            if item['token'][1]=='client':
                key=item['key'];self.credits[key]=self.credits.get(key,0)+native['applied_damage']
            else:self.completed[item['token']]=(item['identity'],native['applied_hp'])
            while len(self.completed)>128:self.completed.pop(next(iter(self.completed)))
        elif result['outcome']=='deferred':self.retry_after=time.monotonic()+.1
        else:raise RuntimeError('La réaction native a refusé le coup partagé.')

    def held_keys(self):
        return {self.pending['key']} if self.pending else set()

    def take_credits(self):
        result=self.credits;self.credits={};return result

    def write(self,info,target,command,epoch,profile=0):
        token=(epoch,*command);identity=EnemySynchronizer._identity(info)
        if token in self.completed:
            old,hp=self.completed[token]
            if old==identity:return min(info['hp'],hp)
            raise RuntimeError('La cible d’un impact confirmé a changé d’identité.')
        if self.pending is not None or time.monotonic()<self.retry_after:return None
        if target>=info['hp']:return info['hp']
        # Reconstruct only closed finite geometry from THIS world's actor.
        # No foreign pointer, opaque HitInfo or damage function crosses the wire.
        x,y,z=info['pos'];source=(x,y,z+1.0)
        vectors=[(0.0,0.0,-1.0),(x,y,z),source]
        # The weapon that caused the loss when this enemy reacts to it; else
        # any captured profile it takes damage from (a sword-proof enemy too).
        chosen=self.replay.usable_profile(info['address'],profile)
        if chosen is None:
            self.retry_after=time.monotonic()+.5;return None
        if not self.replay.submit(info,self.player,vectors,target_hp=target,profile=chosen):return None
        self.pending=dict(token=token,key=info['key'],identity=identity)
        return None
