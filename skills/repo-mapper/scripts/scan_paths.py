"""Open directory entries for scanning without following symlinks or junctions."""

from contextlib import contextmanager
import os
import stat


def is_reparse_point(metadata):
    mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(metadata, "st_file_attributes", 0) & mask)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel.CreateFileW
    _create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    _create_file.restype = wintypes.HANDLE
    _file_info = _kernel.GetFileInformationByHandleEx
    _file_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _file_info.restype = wintypes.BOOL
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL

    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 1
    _FILE_SHARE_WRITE = 2
    _FILE_SHARE_DELETE = 4
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_ATTRIBUTE_TAG_INFO = 9

    class _AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

    def _open_windows_entry(path, *, directory):
        flags = _FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= _FILE_FLAG_BACKUP_SEMANTICS
            # While a directory handle is held, deny writes and renames to that directory.
            share_mode = _FILE_SHARE_READ
        else:
            share_mode = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
        handle = _create_file(
            os.fspath(path), _GENERIC_READ, share_mode, None, _OPEN_EXISTING, flags, None,
        )
        if handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _AttributeTagInfo()
            if not _file_info(handle, _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError("Refusing to follow a reparse point")
            if bool(info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory:
                raise OSError("Entry type changed during scan")
        except OSError:
            _close_handle(handle)
            raise
        return handle


@contextmanager
def open_directory(path, parent=None):
    """Hold the parent open while using its child, or open the scan root."""
    if os.name == "nt":
        path = path if parent is None else parent / path
        handle = _open_windows_entry(path, directory=True)
        try:
            yield path
        finally:
            _close_handle(handle)
    else:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(path, flags, dir_fd=parent)
        try:
            yield fd
        finally:
            os.close(fd)


def read_source(directory, name, limit):
    if os.name == "nt":
        handle = _open_windows_entry(directory / name, directory=False)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _close_handle(handle)
            raise
    else:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(name, flags, dir_fd=directory)

    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Entry is no longer a regular file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read(limit)
    finally:
        os.close(fd)


def has_venv_marker(directory):
    try:
        if os.name == "nt":
            (directory / "pyvenv.cfg").lstat()
        else:
            os.stat("pyvenv.cfg", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True
