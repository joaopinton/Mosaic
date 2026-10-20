"""Cestino di Windows: i file ci vanno sempre, mai cancellati in modo definitivo.

Su unità di rete o rimovibili `FOF_ALLOWUNDO` cancellerebbe davvero: per questo prima si controlla che l'unità abbia un
Cestino (e che non sia impostato su «elimina subito» né troppo piccolo per il file) e, se non va bene, il file resta dov'è.
Come ultima difesa non si passa `FOF_NOCONFIRMATION` e si passa `FOF_WANTNUKEWARNING`: se Windows stesse per eliminare
davvero un file, chiede conferma e l'utente può rispondere No (l'operazione si annulla e il file resta).
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from collections.abc import Iterable
from ctypes import wintypes
from pathlib import Path

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_WANTNUKEWARNING = 0x4000
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
DRIVE_FIXED = 3
BITBUCKET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"


class TrashUnavailable(OSError):
    """Il file non può andare nel Cestino: resta dov'è."""


class _ShFileOp(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


class _QueryInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def has_recycle_bin(path: Path) -> bool:
    """True se `path` sta su un disco fisso con un Cestino utilizzabile."""
    if sys.platform != "win32":
        return False
    anchor = Path(os.path.abspath(path)).anchor
    if not anchor or ctypes.windll.kernel32.GetDriveTypeW(anchor) != DRIVE_FIXED:  # type: ignore[attr-defined]
        return False
    info = _QueryInfo(ctypes.sizeof(_QueryInfo), 0, 0)
    return ctypes.windll.shell32.SHQueryRecycleBinW(anchor, ctypes.byref(info)) == 0  # type: ignore[attr-defined]


def _volume_settings(anchor: str) -> dict[str, int]:
    """Impostazioni del Cestino di un volume (NukeOnDelete, MaxCapacity in MB) dal registro, se ci sono."""
    import winreg

    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(anchor, buf, 64):  # type: ignore[attr-defined]
        return {}
    guid = buf.value[buf.value.find("{"):buf.value.rfind("}") + 1]
    out: dict[str, int] = {}
    for path in (BITBUCKET_KEY + chr(92) + "Volume" + chr(92) + guid, BITBUCKET_KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                for name in ("NukeOnDelete", "MaxCapacity"):
                    if name not in out:
                        with contextlib.suppress(FileNotFoundError, ValueError, TypeError):
                            out[name] = int(winreg.QueryValueEx(key, name)[0])
        except OSError:
            continue
    return out


def check_can_recycle(path: Path) -> None:
    """Solleva `TrashUnavailable` se Windows eliminerebbe il file invece di cestinarlo."""
    if not has_recycle_bin(path):
        raise TrashUnavailable(f"l'unità di {path.name} non ha un Cestino: il file resta dov'è")
    settings = _volume_settings(Path(os.path.abspath(path)).anchor)
    if settings.get("NukeOnDelete"):
        raise TrashUnavailable("il Cestino di questa unità è impostato su «elimina subito»: il file resta dov'è")
    capacity = settings.get("MaxCapacity")
    if capacity and path.stat().st_size > capacity * 1024 * 1024:
        raise TrashUnavailable(f"{path.name} è più grande del Cestino ({capacity} MB): il file resta dov'è")


def send_to_recycle_bin(paths: Iterable[Path]) -> None:
    """Sposta i file nel Cestino. Solleva `TrashUnavailable` (o OSError) senza aver toccato nulla se non è possibile."""
    files = [Path(os.path.abspath(p)) for p in paths if Path(p).exists()]
    if not files:
        return
    if sys.platform != "win32":
        raise TrashUnavailable("il Cestino è disponibile solo su Windows: i file restano dove sono")
    for f in files:
        check_can_recycle(f)
    op = _ShFileOp(None, FO_DELETE, "\0".join(str(f) for f in files) + "\0\0", None,
                   FOF_ALLOWUNDO | FOF_WANTNUKEWARNING | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if code != 0 or op.fAnyOperationsAborted:
        raise OSError(f"Windows non ha spostato i file nel Cestino (codice {code:#x})")
    left = [f for f in files if f.exists()]
    if left:
        raise OSError(f"file ancora presente dopo lo spostamento nel Cestino: {left[0].name}")
