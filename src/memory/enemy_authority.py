"""Shared enemies of this player's room: native losses, ledger HP and poses.

Every game runs the same step, host or not, in any room with other players:
it reports what it reads (and its own native HP losses) to the host's ledger,
reconciles its enemies down to the ledger's HP, and makes the enemies owned
by another player follow that player's game. Its own enemies keep their
untouched native AI, so they chase and hit this player for real.
"""
import time
from src.memory.enemy_sync import EnemySynchronizer
from src.memory.room_scroll import RoomScrollReader


class EnemyAuthority:
    def __init__(self,bridge,memory=None):
        self.bridge=bridge;self.sync=EnemySynchronizer(bridge,cache_scans=True)
        self.memory=memory;self.scroll=RoomScrollReader(bridge)
        self.sync.mutation_guard=self._stable_scene
        self.context=None;self.next_tick=0;self.state='waiting_game';self.error=''
        self.receipts=[]
        self.health=None

    def _stable_scene(self):
        return self.memory is None or not self.scroll.active(self.memory.player_ptr)

    def status(self):return dict(enemy_sync_status=self.state,enemy_sync_error=self.error)

    @staticmethod
    def _frame(world):
        """This game's frame counter: the time base of the poses it reports."""
        try:return world.bridge.read_u32(world.allocation+0x74)
        except (AttributeError,TypeError,OSError):return None

    def step(self,snapshot,net,receipts=(),world=None):
        self.receipts=(self.receipts+list(receipts))[-128:]
        context=net.get_enemy_context() if snapshot.in_game else None
        if context!=self.context:
            self.sync=EnemySynchronizer(self.bridge,cache_scans=True)
            self.sync.mutation_guard=self._stable_scene
            self.context=context;self.receipts=[];self.error=''
        if context is None:self.state='waiting_room';return
        if context[0]!=(snapshot.course,snapshot.stage):self.state='changing_room';return
        if not self._stable_scene():self.state='changing_room';return
        if self.memory is not None and (world is None or world.closed):
            self.state='waiting_renderer';return
        now=time.monotonic()
        if now<self.next_tick:return
        self.next_tick=now+1/30
        if self.error:self.state='error';return
        if world is not None:
            try:
                if self.health is None or self.health.world is not world:
                    from src.memory.enemy_health import NativeEnemyHealth
                    self.health=NativeEnemyHealth(world,self.memory.player_ptr)
                self.health.poll()
                self.sync.health_writer=lambda info,target,command,profile=0:self.health.write(info,target,command,context,profile)
            except (ValueError,RuntimeError,OSError) as exc:
                self.error=str(exc);self.state='error';return
        room=context[0];receipts=self.receipts;self.receipts=[]
        # Before the first ledger frame: still report and queue local losses.
        states=net.get_authoritative_enemies() or {}
        frame=self._frame(world) if world is not None else None
        held=self.health.held_keys() if self.health else set()
        credits=self.health.take_credits() if self.health else {}
        attacks=self.sync.step_client(room,states,receipts,pending_keys=net.pending_enemy_damage_keys(),
                                      allow_healing=False,hold_keys=held,loss_credits=credits)
        # Receipts for a held interval are consumed after its replay ends.
        self.receipts.extend(r for r in receipts if self.sync._receipt_key(room,r) in held)
        if attacks and not net.queue_enemy_damage(attacks):
            self.error='Le journal des dégâts ennemis est plein ; partage suspendu.'
        # complete: the scan read the whole actor table (absences mean dead).
        net.publish_enemy_report(dict(frame=frame,enemies=self.sync.report(),complete=self.sync._context is not None))
        if world is None or world.closed:
            self.state='health_only';return
        # Enemies another player's game simulates follow that game.
        followed={key:state for key,state in states.items() if state.get('owner',net.player_id)!=net.player_id}
        try:
            if world.pose is None:
                from src.memory.enemy_pose import NativeEnemyPose
                world.pose=NativeEnemyPose(world);world.pose.start()
            if not world.pose.publish(self.sync,followed):
                self.error='Le déplacement natif des ennemis attend son propriétaire.'
        except (ValueError,RuntimeError,OSError) as exc:self.error=str(exc)
        self.state='error' if self.error else 'active'
