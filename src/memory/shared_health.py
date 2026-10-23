"""Convert local health changes into events, without echoing applied network values."""


class SharedHealthSynchronizer:
    def __init__(self):
        self.epoch = None
        self.baseline = None
        self.room = None

    def step(self, snapshot, network, memory):
        enabled, epoch, _, shared = network.get_health_state()
        room = (snapshot.course, snapshot.stage, snapshot.max_health)
        if not enabled or not snapshot.in_game or shared is None:
            self.epoch = self.baseline = self.room = None
            return
        if epoch == self.epoch and room == self.room and self.baseline is not None:
            delta = snapshot.health - self.baseline
            if delta:
                network.send_health_delta(delta, epoch=epoch)
        self.epoch, self.room, self.baseline = epoch, room, snapshot.health
        target = network.get_projected_health()
        if target is None:
            self.epoch = self.baseline = self.room = None
            return
        target = max(0, min(snapshot.max_health, target))
        if target != snapshot.health and memory.set_current_health(target):
            self.baseline = snapshot.health = target
