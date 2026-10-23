"""Pertes de sondes acquittées, sans compter celles encore en vol."""
from collections import defaultdict,deque


class LinkQuality:
    def __init__(self):self.samples=defaultdict(lambda:deque(maxlen=32));self.latencies={}
    def record(self,endpoint,ok,ms=None):
        self.samples[endpoint].append(bool(ok))
        if ms is not None:self.latencies[endpoint]=ms
    def forget(self,endpoint):self.samples.pop(endpoint,None);self.latencies.pop(endpoint,None)
    def summary(self):
        samples=[v for v in self.samples.values() if v]
        loss=max((round(100*(1-sum(v)/len(v)),1) for v in samples),default=None)
        latency=max(self.latencies.values(),default=None)
        text='Mesure en cours' if loss is None else 'Bonne' if loss<5 else 'Moyenne' if loss<15 else 'Instable'
        return dict(loss_percent=loss,quality=text,quality_samples=min(map(len,samples),default=0),
                    latency_ms=latency)
