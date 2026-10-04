"""Kernel-owned interprocess exclusion on an existing physical store lock file."""

from __future__ import annotations

import ctypes
import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .safety import Refusal, physical


LOCK_WAIT_SECONDS = 5.0


def _pause(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Refusal("store_lock_busy")
    time.sleep(min(0.02, remaining))


def _windows_open(path: Path, deadline: float) -> int:
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    while True:
        # OPEN_EXISTING, no sharing, OPEN_REPARSE_POINT; never create, follow or replace a lock.
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x00200080, None)
        if handle != ctypes.c_void_p(-1).value:
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except OSError:
                if not kernel.CloseHandle(handle):
                    raise Refusal("store_lock_release_failed") from None
                raise Refusal("store_lock_open_failed") from None
        if ctypes.get_last_error() not in (32, 33):
            raise Refusal("store_lock_open_failed")
        _pause(deadline)


@contextmanager
def exclusive_file(path: Path) -> Iterator[BinaryIO]:
    target = physical(path)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    descriptor: int | None = None
    stream: BinaryIO | None = None
    try:
        if os.name == "nt":
            descriptor = _windows_open(target, deadline)
        else:
            import fcntl

            try:
                descriptor = os.open(target, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                raise Refusal("store_lock_open_failed") from None
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise Refusal("store_lock_acquire_failed") from None
                    _pause(deadline)
        try:
            os.set_inheritable(descriptor, False)
            stream = os.fdopen(descriptor, "r+b")
        except OSError:
            raise Refusal("store_lock_open_failed") from None
        descriptor = None
        yield stream
    finally:
        try:
            if stream is not None:
                stream.close()
            elif descriptor is not None:
                os.close(descriptor)
        except OSError:
            raise Refusal("store_lock_release_failed") from None
