"""Fenêtre indépendante : le compagnon peut quitter avec le jeu pendant l'import."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from src.online_saves import archive_for,backup_progress,decode_progress,install_progress

ROOT=Path(__file__).resolve().parents[1]


class RestartPending(RuntimeError):
    def __init__(self,backup,error):
        super().__init__(f'Progression installée ; réouverture à terminer : {error}\nCopie de secours : {backup}')
        self.backup=backup


def _azahar_pids():
    import win32com.client
    rows=win32com.client.GetObject('winmgmts:').ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='azahar.exe' OR Name='azahar-qt.exe'")
    return [int(row.ProcessId) for row in rows]


def archive_in_use(archive):
    import pythoncom
    from src.overlay.window_tracker import _process_path
    pythoncom.CoInitialize()
    # Les objets COM sont libérés au retour, avant CoUninitialize.
    try:pids=_azahar_pids()
    finally:pythoncom.CoUninitialize()
    for pid in pids:
        executable=_process_path(pid)
        if not executable:raise RuntimeError('Impossible de vérifier un Azahar ouvert. Fermez-le avant cet import.')
        try:
            if archive_for(executable)==Path(archive).resolve():return True
        except (OSError,ValueError):continue
    return False


def close_game(ticket):
    import win32gui,win32con,win32process
    from src.overlay.window_tracker import _process_path
    pid=ticket['pid'];expected=str(Path(ticket['emulator']).resolve()).lower()
    if not pid or not _process_path(pid):return
    if _process_path(pid)!=expected:raise ValueError('La fenêtre Azahar a changé ; aucun jeu fermé.')
    def visit(hwnd,_):
        if win32process.GetWindowThreadProcessId(hwnd)[1]==pid and win32gui.IsWindowVisible(hwnd):
            win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
    win32gui.EnumWindows(visit,None)
    deadline=time.monotonic()+45
    while _process_path(pid) and time.monotonic()<deadline:time.sleep(.15)
    if _process_path(pid):raise RuntimeError('Azahar attend votre confirmation de fermeture. Aucun fichier remplacé ; réessayez après l’avoir fermé.')


def restart_game(ticket):
    from PyQt6.QtNetwork import QLocalSocket
    from src.companion_control import endpoint
    # Le compagnon précédent sort normalement avec son émulateur, après sa restauration.
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        connection=QLocalSocket();connection.connectToServer(endpoint(ticket['profile']))
        present=connection.waitForConnected(150);connection.abort()
        if not present:break
        time.sleep(.2)
    else:raise RuntimeError('Import terminé. Le précédent compagnon termine sa fermeture ; relancez ensuite run_coop.bat.')
    emulator=Path(ticket['emulator']);game=subprocess.Popen([str(emulator),ticket['game']],cwd=emulator.parent)
    args=list(ticket['args'])+['--join',ticket['join'],'--emulator-pid',str(game.pid)]
    python=ROOT/'.venv/Scripts/pythonw.exe'
    subprocess.Popen([str(python if python.exists() else sys.executable),'-m','src.main',*args],cwd=ROOT,
        env=dict(os.environ,PYTHONUTF8='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def perform_import(ticket,slot,*,close=close_game,running=archive_in_use,restart=restart_game,backup_root=None):
    bundle=Path(ticket['bundle']).read_bytes();decode_progress(bundle)
    # Revalider le dossier local ; aucune adresse de fichier n'arrive du salon.
    archive=archive_for(ticket['emulator'])
    if archive!=Path(ticket['archive']).resolve():raise ValueError('Le dossier de sauvegarde a changé ; import annulé.')
    close(ticket)
    backup=install_progress(archive,bundle,slot,backup_root or ROOT/'backups',is_running=lambda:running(archive))
    try:restart(ticket)
    except Exception as exc:raise RestartPending(backup,exc) from exc
    return backup


def run(path):
    from PyQt6.QtCore import pyqtSignal,QObject
    from PyQt6.QtWidgets import QApplication,QDialog,QVBoxLayout,QComboBox,QHBoxLayout
    from src.overlay.coop_panel import button,label
    from src.overlay.menu_widgets import MENU_STYLE
    app=QApplication(sys.argv);app.setApplicationName('ALBW Online Co-op')
    class ImportDialog(QDialog):
        busy=False
        def reject(self):
            if not self.busy:super().reject()
        def closeEvent(self,event):
            if self.busy:event.ignore()
            else:super().closeEvent(event)
        def paintEvent(self,event):
            from PyQt6.QtGui import QPainter
            from PyQt6.QtCore import QRectF
            from src.overlay.menu_widgets import draw_wood_panel
            p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw_wood_panel(p,QRectF(self.rect()).adjusted(2,2,-2,-2))
    class Events(QObject):
        finished=pyqtSignal(object,object)
    events=Events();window=ImportDialog();window.setWindowTitle('Rejoindre avec la progression de l’hôte')
    window.setStyleSheet(MENU_STYLE);window.setMinimumWidth(420)
    layout=QVBoxLayout(window);layout.setContentsMargins(24,24,24,24);layout.setSpacing(14)
    layout.addWidget(label('La progression de votre groupe','heading'))
    info=label('Préparation de votre copie de secours…');layout.addWidget(info)
    slots=QComboBox();slots.addItems(['Fichier 1','Fichier 2','Fichier 3']);slots.setAccessibleName('Fichier de destination')
    layout.addWidget(slots);slots.setEnabled(False)
    layout.addWidget(label('Votre progression actuelle sera conservée dans backups/. Après confirmation, votre jeu se fermera proprement puis rouvrira. Enregistrez avant de continuer.','muted'))
    row=QHBoxLayout();layout.addLayout(row)
    cancel=button('Annuler',window.reject);row.addWidget(cancel)
    start=button('Importer et rouvrir le jeu',lambda:begin(),'primary');start.setEnabled(False);row.addWidget(start)
    ticket=None;worker=None
    def topmost(on):
        # Jamais setWindowFlag ici : il masque la fenêtre un instant, ce qui
        # termine exec() puis le processus, et l'import en cours avec lui.
        try:
            import win32con,win32gui
            win32gui.SetWindowPos(int(window.winId()),win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST,
                                  0,0,0,0,win32con.SWP_NOMOVE|win32con.SWP_NOSIZE|win32con.SWP_NOACTIVATE)
        except Exception:pass
    def begin():
        nonlocal worker
        window.busy=True;destination=slots.currentIndex()+1
        slots.setEnabled(False);start.setEnabled(False);cancel.setEnabled(False)
        # Azahar peut demander de confirmer sa fermeture : ne pas le recouvrir.
        topmost(False)
        info.setText('Fermeture du jeu… Si Azahar demande de confirmer, répondez Oui.\nCopie de secours puis installation ensuite.')
        def work():
            try:events.finished.emit(perform_import(ticket,destination),None)
            except RestartPending as exc:events.finished.emit(exc.backup,str(exc))
            except Exception as exc:events.finished.emit(None,str(exc))
        worker=threading.Thread(target=work,daemon=True);worker.start()
    def finish(result,error):
        window.busy=False;cancel.setEnabled(True);cancel.setText('Fermer')
        if error:info.setText(error);start.setEnabled(result is None);slots.setEnabled(result is None)
        else:info.setText(f'Progression installée. Dans Azahar, chargez le fichier {slots.currentIndex()+1}.\nCopie de secours : {result}')
    events.finished.connect(finish)
    try:
        ticket=json.loads(Path(path).read_text(encoding='utf-8'));decode_progress(Path(ticket['bundle']).read_bytes())
        archive=archive_for(ticket['emulator'])
        if archive!=Path(ticket['archive']).resolve():raise ValueError('Le dossier de sauvegarde a changé.')
        backup=backup_progress(archive,ROOT/'backups')
        info.setText(f'Votre progression est à l’abri. Choisissez où placer celle de l’hôte.\nCopie : {backup}')
        start.setEnabled(True);slots.setEnabled(True)
    except (OSError,ValueError,KeyError,TypeError) as exc:info.setText(str(exc))
    # Ouverte en arrière-plan pendant la partie : elle doit passer devant Azahar.
    window.show();topmost(True);window.raise_();window.activateWindow()
    window.exec()
    # Une installation commencée va toujours jusqu'au bout.
    if worker:worker.join()


if __name__=='__main__':run(sys.argv[1])
