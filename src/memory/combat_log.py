"""Native contact/damage evidence of one world scene, for enemy authority receipts.

Raw records and matched hits are appended to local JSONL files under
.local/research; matches become receipts that enemy authority drains.
"""
from datetime import datetime
from pathlib import Path
import json
from src.memory.combat_evidence import CombatEvidence

FOLDER=Path(__file__).resolve().parents[2]/'.local/research'


class CombatLog:
    def __init__(self):
        self.matcher=None;self.observed=0;self.matched=0
        self.evidence=None;self.matches_file=None;self.receipts=[]

    def attach(self,player_handle,key_for):self.matcher=CombatEvidence(player_handle,key_for)
    def detach(self):self.matcher=None

    def drain(self):
        receipts=self.receipts;self.receipts=[];return receipts

    def _append(self,attribute,prefix,player_id,rows):
        path=getattr(self,attribute)
        if path is None:
            FOLDER.mkdir(parents=True,exist_ok=True)
            path=FOLDER/f'{prefix}-{player_id}-{datetime.now():%Y%m%d-%H%M%S}.jsonl'
            setattr(self,attribute,path)
        with path.open('a',encoding='utf-8') as output:
            for row in rows:output.write(json.dumps(row,allow_nan=False)+'\n')

    def step(self,native,player_id):
        contacts=native.contacts_captured();damages=native.combat_captured()
        if contacts or damages:
            self._append('evidence','native-hits',player_id,contacts+damages)
            self.observed+=len(contacts)+len(damages)
        if self.matcher:
            matches=self.matcher.ingest(contacts,damages)
            if matches:
                self.receipts=(self.receipts+matches)[-128:]
                self._append('matches_file','matched-hits',player_id,matches)
                self.matched+=len(matches)
