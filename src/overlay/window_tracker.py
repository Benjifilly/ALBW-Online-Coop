"""Windows-only geometry discovery, independent from the menu widgets."""
import ctypes
import math
from ctypes import wintypes
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetAncestor.argtypes = [wintypes.HWND,wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.MonitorFromWindow.argtypes = [wintypes.HWND,wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL


class MonitorInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),
              ('dwFlags',wintypes.DWORD),('szDevice',wintypes.WCHAR*32)]


user32.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
user32.GetMonitorInfoW.restype=wintypes.BOOL
ENUM_CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUM_CALLBACK, wintypes.LPARAM]


def _process_path(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_azahar_hwnd_and_pid(exclude_hwnd=0, preferred_pid=0):
    best = (0, None, 0)

    def visit(hwnd, _):
        nonlocal best
        if hwnd == exclude_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if preferred_pid and pid.value != preferred_pid:
            return True
        path = _process_path(pid.value)
        if not path.endswith(("\\azahar.exe", "\\azahar-qt.exe")):
            return True
        rect = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best[0]:
                best = (area, hwnd, pid.value)
        return True

    user32.EnumWindows(ENUM_CALLBACK(visit), 0)
    return best[1], best[2]


def client_rect_on_screen(origin,size,native_origin,logical_screen,dpr):
    if not math.isfinite(dpr) or dpr<=0 or min(size)<=0:return None
    # Sous Windows, Qt réduit les dimensions, mais conserve l'origine de l'écran.
    return QRect(logical_screen.x()+round((origin[0]-native_origin[0])/dpr),
                 logical_screen.y()+round((origin[1]-native_origin[1])/dpr),
                 round(size[0]/dpr),round(size[1]/dpr))


def get_window_client_logical_rect(hwnd, dpr=None):
    if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
    monitor=user32.MonitorFromWindow(hwnd,2)
    if not monitor or not user32.GetMonitorInfoW(monitor,ctypes.byref(info)):return None
    screens=QGuiApplication.screens() if QGuiApplication.instance() else []
    if not screens:return None
    screen=next((s for s in screens if s.name().casefold()==info.szDevice.casefold()),None)
    if screen is None:
        screen=min(screens,key=lambda s:(s.geometry().x()-info.rcMonitor.left)**2+(s.geometry().y()-info.rcMonitor.top)**2)
    return client_rect_on_screen((origin.x,origin.y),(rect.right-rect.left,rect.bottom-rect.top),
        (info.rcMonitor.left,info.rcMonitor.top),screen.geometry(),screen.devicePixelRatio())


def game_is_foreground(game,foreground,owned=()):
    if foreground and foreground in owned:return True
    if not game or not foreground:return False
    if foreground==game:return True
    root=user32.GetAncestor(game,2)
    return bool(root and root==user32.GetAncestor(foreground,2))


def raise_without_activation(hwnd):
    if not hwnd or not user32.IsWindow(hwnd):return False
    # Garder le HUD devant le plein écran, sans déplacer, redimensionner ni activer.
    return bool(user32.SetWindowPos(hwnd,wintypes.HWND(-1),0,0,0,0,0x0001|0x0002|0x0010|0x0200))
