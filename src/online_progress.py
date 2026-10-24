"""Relie le transfert du salon à l'assistant local d'import."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from src.online_saves import archive_for,export_progress,decode_progress

ROOT=Path(__file__).resolve().parents[1]


def _command_line(pid):
    import win32com.client
    rows=win32com.client.GetObject('winmgmts:').ExecQuery(f'SELECT CommandLine FROM Win32_Process WHERE ProcessId={int(pid)}')
    return next((str(row.CommandLine or '') for row in rows),'')


def running_game(pid):
    """Le jeu passé en argument à cet Azahar ('' s'il a été ouvert depuis son menu)."""
    import pythoncom
    pythoncom.CoInitialize()
    # Les objets COM sont libérés au retour, avant CoUninitialize.
    try:line=_command_line(pid)
    finally:pythoncom.CoUninitialize()
    try:words=[w.strip('"') for w in shlex.split(line,posix=False)]
    except ValueError:return ''
    return next((w for w in reversed(words[1:]) if Path(w).suffix.lower() in ('.3ds','.cci','.cxi','.app') and Path(w).is_file()),'')


class OnlineProgress:
    def __init__(self,controller,profile,pid):
        self.controller=controller;self.profile=profile;self.pid=pid;self.offered=None;self.launch_args=[]
        self.auto=False;self.error=''
        controller.net.save_transfer.provider=self.export

    def emulator(self):
        from src.overlay.window_tracker import _process_path
        if not self.pid:raise ValueError('Ouvrez le mod avec run_coop.bat pour associer l’import au bon Azahar.')
        actual=_process_path(self.pid)
        if not actual:raise ValueError('Votre Azahar a été fermé. Relancez le mod avant cet import.')
        if Path(actual).name.lower() not in ('azahar.exe','azahar-qt.exe'):raise ValueError('Le processus Azahar a changé ; relancez le mod.')
        return str(Path(actual).resolve())

    def context(self):
        """Ce qu'il faut pour rouvrir ce jeu après l'import (côté client)."""
        from src.local_session import discover_paths,identify_game
        emulator=self.emulator()
        # Le jeu réellement ouvert ; les choix du lanceur seulement à défaut.
        try:game=running_game(self.pid)
        except Exception:game=''
        game=game or discover_paths()[1]
        if not game:raise ValueError('Jeu ALBW introuvable : choisissez-le dans « Choisir Azahar et le jeu » de run_coop.bat.')
        try:info=identify_game(game)
        except OSError:raise ValueError('Le fichier du jeu ALBW est illisible ; choisissez-le à nouveau dans run_coop.bat.') from None
        if info.title_id!=0x00040000000EC400:raise ValueError('L’import de progression nécessite ALBW EUR.')
        return dict(emulator=emulator,game=str(info.path),archive=str(archive_for(emulator)),pid=self.pid)

    def export(self):
        o=self.controller
        with o._memory_lock:
            if o.bridge.attached_title_id!=0x00040000000EC400:raise ValueError('La partie chargée par l’hôte doit être ALBW EUR.')
            if not o.memory.last_snapshot.in_game:raise ValueError('L’hôte doit charger une partie et l’enregistrer à une girouette.')
            manager=o.bridge.read_u32(0x711DE8)
            if not manager or not 0x08000000<=manager<0x1F000000:raise ValueError('La partie de l’hôte n’est pas prête.')
            slot=o.bridge.read_u32(manager+0x9BC)
            # L'hôte n'envoie que son fichier : il n'a pas besoin de connaître son jeu.
            result=export_progress(archive_for(self.emulator()),slot)
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
            from src.network.rendezvous import format_endpoint
            ticket=dict(context,profile=self.profile,bundle=str(data_path),
                        join=format_endpoint(target),args=self.launch_args)
            path=folder/(rid+'.json');path.write_text(json.dumps(ticket,ensure_ascii=False),encoding='utf-8')
            python=ROOT/'.venv/Scripts/pythonw.exe'
            subprocess.Popen([str(python if python.exists() else sys.executable),'-m','src.save_import',str(path)],cwd=ROOT,
                env=dict(os.environ,PYTHONUTF8='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.controller.add_log('Progression reçue. Choisissez le fichier de destination dans la fenêtre d’import.')
        except (OSError,ValueError,TypeError) as exc:
            self.error=str(exc);self.controller.add_log('Import de progression indisponible : '+self.error)
