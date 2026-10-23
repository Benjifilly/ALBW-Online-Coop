"""Private local companion control, including a graceful patch-restoring exit."""
import hashlib
from pathlib import Path
from PyQt6.QtCore import QTimer,QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


def endpoint(profile):
    root=str(Path(__file__).resolve().parents[1]).lower()
    return 'albw-companion-'+hashlib.sha256((root+'|'+(profile or 'default')).encode()).hexdigest()[:24]


def control_lock_path(profile):
    folder=Path(__file__).resolve().parents[1]/'.local/profiles'
    folder.mkdir(parents=True,exist_ok=True)
    return folder/(endpoint(profile)+'.lock')


def listen(app,profile,on_show):
    # Windows named pipes allow several listeners with the same name. A socket
    # listen alone is not a single-instance guard and STOP can reach a sibling.
    lock=QLockFile(str(control_lock_path(profile)))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):return None
    server=QLocalServer(app)
    server.profile_lock=lock
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    def accept():
        while server.hasPendingConnections():
            connection=server.nextPendingConnection()
            def receive(connection=connection):
                command=bytes(connection.read(64)).strip()
                if command==b'STOP':
                    connection.write(b'STOPPING\n')
                    connection.flush()
                    QTimer.singleShot(0,app.quit)
                elif command==b'SHOW':
                    on_show()
                    connection.disconnectFromServer()
            connection.readyRead.connect(receive)
            connection.disconnected.connect(connection.deleteLater)
            if connection.bytesAvailable():receive()
    server.newConnection.connect(accept)
    if server.listen(endpoint(profile)):return server
    lock.unlock()
    return None


def stop(profile,timeout=8000):
    socket=QLocalSocket()
    socket.connectToServer(endpoint(profile))
    if not socket.waitForConnected(250):
        return False
    socket.write(b'STOP\n')
    if socket.bytesToWrite() and not socket.waitForBytesWritten(1000):
        return False
    if not socket.bytesAvailable() and not socket.waitForReadyRead(1000):
        return False
    if bytes(socket.readAll()).strip()!=b'STOPPING':
        return False
    return socket.state()==QLocalSocket.LocalSocketState.UnconnectedState or socket.waitForDisconnected(timeout)
