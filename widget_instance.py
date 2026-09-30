"""Keep one live Windows widget for each shared runtime directory."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path


class WidgetInstance:
    """Reserve a runtime directory until close() or process termination."""

    def __init__(self, runtime: Path):
        self.acquired = False
        self._handle = None
        if os.name != "nt":
            raise RuntimeError("WidgetInstance requires Windows")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL

        normalized = os.path.normcase(str(runtime.resolve()))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        name = "Local\\PixelStudio." + digest
        ctypes.set_last_error(0)
        # The live named handle reserves the directory; no thread waits on it.
        handle = create_mutex(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            if not self._close_handle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return

        self._handle = handle
        self.acquired = True

    def close(self) -> None:
        """Release this reservation; repeated calls are harmless."""
        if self._handle is None:
            return
        if not self._close_handle(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = None
        self.acquired = False
