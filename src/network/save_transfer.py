"""Petit transfert découpé : session du salon, acquittements et SHA-256."""
import base64
import hashlib
import math
import re
import secrets
import threading
import time

CHUNK=768
MAX_SIZE=16384
RETRY=.45
TIMEOUT=45


def ident(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{32}',value) is not None
def integer(value,a,b):return type(value) is int and a<=value<=b


class SaveTransfer:
    def __init__(self,node):
        self.node=node;self.provider=None;self.incoming=None;self.outgoing={};self.jobs={}
        self.completed=None;self.status=dict(state='idle',percent=0,error='')

    def clear(self):
        self.incoming=None;self.outgoing.clear();self.jobs.clear();self.completed=None
        self.status=dict(state='idle',percent=0,error='')

    def request(self):
        if self.node.mode!='client' or self.node.connection_state!='connected':return False
        self.completed=None;self.incoming=dict(id=secrets.token_hex(16),started=time.monotonic(),next=0,parts={})
        self.status=dict(state='receiving',percent=0,error='');return True

    def send(self,kind,endpoint,**fields):
        self.node._send_packet(dict(type=kind,session=self.node.session_id,**fields),endpoint)

    def fail(self,text):
        self.incoming=None;self.status=dict(state='error',percent=0,error=text)

    def handle(self,msg,endpoint):
        kind=msg['type'];rid=msg.get('id');now=time.monotonic()
        if not ident(rid):return
        if self.node.mode=='host':
            if kind=='SAVE_REQUEST':
                previous=self.outgoing.get(endpoint)
                if previous and previous['id']==rid:previous['next']=0;return
                if endpoint in self.jobs:return
                if self.provider is None:
                    self.send('SAVE_ERROR',endpoint,id=rid,error='L’hôte doit charger puis enregistrer sa partie.');return
                session=self.node.session_id;job=object();self.jobs[endpoint]=job
                def prepare():
                    try:
                        data=self.provider()
                        if not isinstance(data,bytes) or not 1<=len(data)<=MAX_SIZE:raise ValueError('Sauvegarde de l’hôte invalide.')
                        value=dict(id=rid,data=data,sha256=hashlib.sha256(data).hexdigest(),acked=set(),meta=False,
                                   next=0,started=time.monotonic())
                    except Exception as exc:value=dict(error=' '.join(str(exc).split())[:180] or 'Lecture de la sauvegarde indisponible.')
                    with self.node._lock:
                        if self.jobs.get(endpoint) is not job or self.node.session_id!=session:return
                        self.jobs.pop(endpoint,None)
                        if 'error' in value:self.send('SAVE_ERROR',endpoint,id=rid,error=value['error'])
                        else:self.outgoing[endpoint]=value
                threading.Thread(target=prepare,daemon=True).start()
            elif kind=='SAVE_ACK':
                row=self.outgoing.get(endpoint);index=msg.get('index')
                if not row or row['id']!=rid or not integer(index,-1,math.ceil(len(row['data'])/CHUNK)-1):return
                if index==-1:row['meta']=True
                else:row['acked'].add(index)
            elif kind=='SAVE_DONE':
                row=self.outgoing.get(endpoint)
                if row and row['id']==rid and msg.get('sha256')==row['sha256']:self.outgoing.pop(endpoint,None)
            return
        row=self.incoming
        if not row or row['id']!=rid:return
        if kind=='SAVE_ERROR':
            text=msg.get('error')
            if isinstance(text,str) and len(text)<=180:self.fail(text)
        elif kind=='SAVE_META':
            size,digest=msg.get('size'),msg.get('sha256')
            if not integer(size,1,MAX_SIZE) or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):return
            if 'size' in row and (row['size'],row['sha256'])!=(size,digest):return
            row.update(size=size,sha256=digest)
            self.send('SAVE_ACK',endpoint,id=rid,index=-1)
        elif kind=='SAVE_CHUNK' and 'size' in row:
            index=msg.get('index');encoded=msg.get('data')
            if not integer(index,0,math.ceil(row['size']/CHUNK)-1) or not isinstance(encoded,str) or len(encoded)>1024:return
            try:data=base64.b64decode(encoded,validate=True)
            except ValueError:return
            expected=min(CHUNK,row['size']-index*CHUNK)
            if len(data)!=expected:return
            if index in row['parts'] and row['parts'][index]!=data:return
            row['parts'][index]=data;self.send('SAVE_ACK',endpoint,id=rid,index=index)
            self.status['percent']=round(100*sum(map(len,row['parts'].values()))/row['size'])
            if len(row['parts'])==math.ceil(row['size']/CHUNK):
                data=b''.join(row['parts'][i] for i in range(len(row['parts'])))
                if hashlib.sha256(data).hexdigest()!=row['sha256']:self.fail('Le transfert est endommagé. Réessayez.');return
                self.completed=data;self.status=dict(state='complete',percent=100,error='')
                self.send('SAVE_DONE',endpoint,id=rid,sha256=row['sha256'])

    def tick(self,now):
        row=self.incoming
        if row and self.status['state']=='receiving':
            if now-row['started']>TIMEOUT:self.fail('Transfert interrompu. Reconnectez-vous puis réessayez.')
            elif 'size' not in row and now>=row['next']:
                row['next']=now+RETRY;self.send('SAVE_REQUEST',self.node.host_target,id=row['id'])
        if self.node.mode!='host':return
        for endpoint,row in list(self.outgoing.items()):
            if now-row['started']>TIMEOUT or endpoint not in self.node._members:self.outgoing.pop(endpoint,None);continue
            if now<row['next']:continue
            row['next']=now+RETRY
            if not row['meta']:
                self.send('SAVE_META',endpoint,id=row['id'],size=len(row['data']),sha256=row['sha256']);continue
            missing=[i for i in range(math.ceil(len(row['data'])/CHUNK)) if i not in row['acked']]
            # La dernière trame redemande aussi la confirmation finale perdue.
            for index in (missing[:4] or [math.ceil(len(row['data'])/CHUNK)-1]):
                data=row['data'][index*CHUNK:(index+1)*CHUNK]
                self.send('SAVE_CHUNK',endpoint,id=row['id'],index=index,data=base64.b64encode(data).decode())
