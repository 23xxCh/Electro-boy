"""Windows user-bound DPAPI vault; no plaintext credentials on disk."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


def runtime_dir():
    path = Path(os.environ['LOCALAPPDATA']) / 'AnkerEnergyAgent'
    path.mkdir(parents=True, exist_ok=True)
    return path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]


def transform(data, encrypt):
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    if encrypt:
        okay = crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        okay = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not okay:
        raise RuntimeError('credential_vault_failed')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def save(app_id, secret):
    data = json.dumps({'app_id':app_id, 'app_secret':secret}).encode()
    (runtime_dir() / 'feishu.dpapi').write_bytes(transform(data, True))


def load():
    return json.loads(transform((runtime_dir() / 'feishu.dpapi').read_bytes(), False))
