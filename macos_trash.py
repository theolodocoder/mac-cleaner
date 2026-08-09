"""Small ctypes bridge to macOS Foundation's native Trash API."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


class NativeTrashError(OSError):
    """Raised when macOS cannot move an item to Trash."""


def trash_item(path: Path) -> Path:
    """Move *path* to Trash with NSFileManager and return its new URL path."""
    if sys.platform != "darwin":
        raise NativeTrashError("native Trash is only available on macOS")

    # Loading Foundation registers its Objective-C classes with the runtime.
    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p

    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    if address is None:
        raise NativeTrashError("could not access the macOS Objective-C runtime")

    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
    send_id_c_string = ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
    )(address)
    send_id_object = ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )(address)
    send_bool_trash = ctypes.CFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )(address)
    send_c_string = ctypes.CFUNCTYPE(
        ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p
    )(address)

    def cls(name: bytes) -> int:
        value = objc.objc_getClass(name)
        if not value:
            raise NativeTrashError(f"missing macOS class: {name.decode()}")
        return value

    def sel(name: bytes) -> int:
        return objc.sel_registerName(name)

    pool = send_id(cls(b"NSAutoreleasePool"), sel(b"new"))
    try:
        ns_path = send_id_c_string(
            cls(b"NSString"), sel(b"stringWithUTF8String:"), str(path).encode("utf-8")
        )
        source_url = send_id_object(cls(b"NSURL"), sel(b"fileURLWithPath:"), ns_path)
        manager = send_id(cls(b"NSFileManager"), sel(b"defaultManager"))
        resulting_url = ctypes.c_void_p()
        error = ctypes.c_void_p()
        succeeded = send_bool_trash(
            manager,
            sel(b"trashItemAtURL:resultingItemURL:error:"),
            source_url,
            ctypes.byref(resulting_url),
            ctypes.byref(error),
        )
        if not succeeded:
            message = "macOS could not move the item to Trash"
            if error.value:
                description = send_id(error.value, sel(b"localizedDescription"))
                raw_message = send_c_string(description, sel(b"UTF8String"))
                if raw_message:
                    message = raw_message.decode("utf-8", errors="replace")
            raise NativeTrashError(message)

        result_path = send_id(resulting_url.value, sel(b"path"))
        raw_path = send_c_string(result_path, sel(b"UTF8String"))
        if not raw_path:
            raise NativeTrashError("macOS moved the item but did not return its Trash path")
        return Path(raw_path.decode("utf-8", errors="surrogateescape"))
    finally:
        if pool:
            send_id(pool, sel(b"release"))
