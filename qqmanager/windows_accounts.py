"""Find QQ NT profiles whose local files are currently open by Windows QQ."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import sys


def _profile_roots():
    documents = ctypes.create_unicode_buffer(260)
    roots = []
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, documents) == 0:
        roots.append(Path(documents.value) / 'Tencent Files')
    roots.append(Path.home() / 'Documents' / 'Tencent Files')
    return dict.fromkeys(roots)


class _Process(ctypes.Structure):
    _fields_ = [('pid', wintypes.DWORD), ('started', wintypes.FILETIME)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('process', _Process), ('name', wintypes.WCHAR * 256),
                ('service', wintypes.WCHAR * 64), ('kind', ctypes.c_int),
                ('status', wintypes.DWORD), ('session', wintypes.DWORD),
                ('restartable', wintypes.BOOL)]


def _is_qq(pid):
    kernel = ctypes.WinDLL('kernel32')
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        return bool(kernel.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, image,
                                                      ctypes.byref(size))
                    and Path(image.value).name.lower() == 'qq.exe')
    finally:
        kernel.CloseHandle(wintypes.HANDLE(handle))


def _qq_uses_files(paths):
    # Restart Manager reports owners of file handles without opening the databases.
    rm = ctypes.WinDLL('rstrtmgr')
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(session), 0, key):
        return False
    try:
        files = (wintypes.LPCWSTR * len(paths))(*(str(path) for path in paths))
        if rm.RmRegisterResources(session, len(files), files, 0, None, 0, None):
            return False
        needed, count, reason = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD()
        owners = None
        for _ in range(3):
            result = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                  owners, ctypes.byref(reason))
            if result == 0:
                return any(_is_qq(owners[i].process.pid) for i in range(count.value))
            if result != 234:  # ERROR_MORE_DATA
                return False
            count.value = needed.value
            owners = (_ProcessInfo * count.value)()
        return False
    finally:
        rm.RmEndSession(session)


def active_accounts():
    """Return active local account numbers. A historical directory alone is insufficient."""
    if sys.platform != 'win32':
        return []
    active = set()
    for root in _profile_roots():
        try:
            profiles = list(root.iterdir())
        except OSError:
            continue
        for profile in profiles:
            if profile.name in active or not re.fullmatch(r'[1-9][0-9]{4,19}', profile.name):
                continue
            db = profile / 'nt_qq' / 'nt_db'
            try:
                files = [db / (name + suffix) for name in ('emoji.db', 'settings.db')
                         for suffix in ('', '-shm', '-wal') if (db / (name + suffix)).is_file()]
                if files and _qq_uses_files(files):
                    active.add(profile.name)
            except OSError:
                continue
    return sorted(active)
