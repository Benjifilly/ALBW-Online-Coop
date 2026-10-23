"""Reprise locale par instance Azahar, sans toucher aux sauvegardes du jeu."""
import json
import os
from pathlib import Path
import secrets
import tempfile
from src.memory.life_session import LoadedGameStamp

ROOT=Path(__file__).resolve().parents[2]/'.local/coop-lab'
STATES=('alive','dead','ghost')


def fresh(stamp):
    return dict(version=1,stamp=stamp,life_id=secrets.token_hex(16),life='alive',
                revive_serial=0,release_serial=0,continued=False,target=None,seen_serials={},
                seen_releases={},expected=[],vane_using=False,wiped=False)


def valid(record):
    if not isinstance(record,dict) or type(record.get('version')) is not int or record['version'] not in (1,2):return False
    for key,size in (('stamp',16),('life_id',32)):
        value=record.get(key)
        if not isinstance(value,str) or len(value)!=size or any(c not in '0123456789abcdef' for c in value):return False
    if record.get('life') not in STATES:return False
    for key in ('revive_serial','release_serial'):
        if type(record.get(key)) is not int or not 0<=record[key]<=2**31:return False
    for key in ('continued','vane_using','wiped'):
        if type(record.get(key)) is not bool:return False
    for key in ('seen_serials','seen_releases'):
        rows=record.get(key)
        if not isinstance(rows,dict) or len(rows)>128:return False
        if any(not isinstance(k,str) or not 1<=len(k)<=64 or type(v) is not int or not 0<=v<=2**31 for k,v in rows.items()):return False
    if record['version']==2:
        from src.memory.party_life import valid_dump
        if not valid_dump(record.get('party')):return False
    expected=record.get('expected')
    return (isinstance(expected,list) and len(expected)<=128
            and all(isinstance(k,str) and 1<=len(k)<=64 for k in expected)
            and 'target' in record
            and (record['target'] is None or isinstance(record['target'],str) and len(record['target'])<=64))


class LifeCheckpoint:
    def __init__(self,bridge,emulator_pid=0,*,directory=ROOT,stamp=None):
        self.directory=Path(directory)
        self.path=self.directory/f'life-{emulator_pid}.json' if emulator_pid else None
        self.stamp=stamp or LoadedGameStamp(bridge)
        self.record=None;self._saved=None
        self._load()

    def _load(self):
        if self.path is not None and self.path.exists():
            data=self.path.read_bytes()
            try:record=json.loads(data)
            except (ValueError,UnicodeError) as exc:raise ValueError('Fichier de reprise du spectateur illisible.') from exc
            if not valid(record):raise ValueError('Fichier de reprise du spectateur invalide.')
            self.record=record;self._saved=data

    def read(self):
        stamp=self.stamp.read()
        if stamp is None:return None
        if self.path is None:
            self.path=self.directory/f'life-game-{stamp[:8]}.json';self._load()
        if self.record is None or self.record['stamp']!=stamp:
            old=self.record;self.record=fresh(stamp)
            if old:self.record['previous']=[old['life_id'],*old.get('party',{}).get('previous',[])][:8]
        return self.record

    def save(self,record):
        if not valid(record):raise ValueError('État de reprise du spectateur invalide.')
        data=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        if data==self._saved:return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix=self.path.name+'.',suffix='.tmp',delete=False) as output:
                temporary=Path(output.name);output.write(data);output.flush();os.fsync(output.fileno())
            os.replace(temporary,self.path)
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()
        self.record=record;self._saved=data
