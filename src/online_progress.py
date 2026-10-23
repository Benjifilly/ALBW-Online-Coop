"""Relie le transfert du salon à l'assistant local d'import."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from src.online_saves import archive_for,export_progress,decode_progress

ROOT=Path(__file__).resolve().parents[1]


class OnlineProgress:
    def __init__(self,controller,profile,pid):
        self.controller=controller;self.profile=profile;self.pid=pid;self.offered=None;self.launch_args=[]
        self.auto=False;self.error=''
        controller.net.save_transfer.provider=self.export

    def context(self):
        from src.local_session import discover_paths,identify_game
        from src.overlay.window_tracker import _process_path
        emulator,game=discover_paths()
        pid=self.pid
        if not pid:raise ValueError('Ouvrez le mod avec run_coop.bat pour associer l’import au bon Azahar.')
        actual=_process_path(pid) if pid else ''
        if not actual:raise ValueError('Votre Azahar a été fermé. Relancez le mod avant cet import.')
        if Path(actual).name.lower() not in ('azahar.exe','azahar-qt.exe'):raise ValueError('Le processus Azahar a changé ; relancez le mod.')
        emulator=actual
        info=identify_game(game)
        if info.title_id!=0x00040000000EC400:raise ValueError('L’import de progression nécessite ALBW EUR.')
        if not emulator or not Path(emulator).is_file():raise ValueError('L’installation Azahar de ce joueur est introuvable.')
        return dict(emulator=str(Path(emulator).resolve()),game=str(info.path),archive=str(archive_for(emulator)),pid=pid)

    def export(self):
        o=self.controller
        with o._memory_lock:
            if o.bridge.attached_title_id!=0x00040000000EC400:raise ValueError('La partie chargée par l’hôte doit être ALBW EUR.')
            if not o.memory.last_snapshot.in_game:raise ValueError('L’hôte doit charger une partie et l’enregistrer à une girouette.')
            manager=o.bridge.read_u32(0x711DE8)
            if not manager or not 0x08000000<=manager<0x1F000000:raise ValueError('La partie de l’hôte n’est pas prête.')
            slot=o.bridge.read_u32(manager+0x9BC)
            result=export_progress(self.context()['archive'],slot)
            if o.bridge.read_u32(manager+0x9BC)!=slot:raise ValueError('L’hôte a changé de fichier pendant le transfert.')
            return result

    def request(self):
        with self.controller.net._lock:
            ok=self.controller.net.save_transfer.request()
        if ok:self.error='';self.controller.add_log('Téléchargement de la dernière progression enregistrée de l’hôte…')
        return ok

    def poll(self):
        node=self.controller.net
        if self.auto and node.connection_state=='connected':self.auto=False;self.request()
        with node._lock:
            transfer=node.save_transfer;state=dict(transfer.status)
            data=transfer.completed;rid=transfer.incoming and transfer.incoming['id']
        if data is not None and rid!=self.offered:
            self.offered=rid
            threading.Thread(target=self.offer,args=(data,rid),daemon=True).start()
        if self.error:state.update(state='error',error=self.error)
        return dict(progress_transfer=state)

    def offer(self,bundle,rid):
        try:
            decode_progress(bundle);context=self.context()
            folder=ROOT/'.local/incoming-progress';folder.mkdir(parents=True,exist_ok=True)
            data_path=folder/(rid+'.bin');data_path.write_bytes(bundle)
            target=self.controller.net.host_target
            if not target:raise ValueError('Reconnectez-vous au salon avant de préparer l’import.')
            ticket=dict(context,profile=self.profile,bundle=str(data_path),
                        join=f'{target[0]}:{target[1]}',args=self.launch_args)
            path=folder/(rid+'.json');path.write_text(json.dumps(ticket,ensure_ascii=False),encoding='utf-8')
            python=ROOT/'.venv/Scripts/pythonw.exe'
            subprocess.Popen([str(python if python.exists() else sys.executable),'-m','src.save_import',str(path)],cwd=ROOT,
                env=dict(os.environ,PYTHONUTF8='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.controller.add_log('Progression reçue. Choisissez le fichier de destination dans la fenêtre d’import.')
        except (OSError,ValueError,TypeError) as exc:
            self.error=str(exc);self.controller.add_log('Import de progression indisponible : '+self.error)
