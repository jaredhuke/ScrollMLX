"""
Read live fan RPM and SoC temperature from the Apple SMC — no sudo, no helper binary.

macOS exposes neither fan speed nor die temperature through psutil (`sensors_fans` doesn't
exist on Darwin) nor through `ioreg`; `powermetrics` needs root. The one path that works for an
unprivileged process is talking to the AppleSMC IOKit user client directly. This is a small,
self-contained ctypes port of the well-known SMC protocol (the same one `iStats`/`stats` use).

Everything here is best-effort: any failure (no fans on a fanless Mac, SMC unavailable, a key
that doesn't exist on this chip) yields None / [] rather than raising, so the /v1/sys endpoint
can call it blindly. The IOKit connection is opened once and reused under a lock.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import struct
import threading
import time

_KERNEL_INDEX_SMC = 2
_SMC_CMD_READ_BYTES = 5
_SMC_CMD_READ_KEYINFO = 9

# Candidate die-temperature sensors across Apple Silicon generations (perf + efficiency cores).
# We probe them once, keep the ones that read back a sane value, and average those each call —
# a single representative SoC temperature. Different chips populate different subsets.
_TEMP_KEYS = (
    "Tp01", "Tp05", "Tp09", "Tp0D", "Tp0H", "Tp0L", "Tp0P", "Tp0T", "Tp0X", "Tp0b", "Tp0f",
    "Tp0C", "Te05", "Te0L", "Te0P", "Te0S", "Tg05", "Tg0D",
)


class _Vers(ctypes.Structure):
    _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte), ("build", ctypes.c_ubyte),
                ("reserved", ctypes.c_ubyte), ("release", ctypes.c_ushort)]


class _PLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_ushort), ("length", ctypes.c_ushort), ("cpuPLimit", ctypes.c_uint),
                ("gpuPLimit", ctypes.c_uint), ("memPLimit", ctypes.c_uint)]


class _KeyInfo(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint), ("dataType", ctypes.c_uint), ("dataAttributes", ctypes.c_ubyte)]


class _KeyData(ctypes.Structure):
    _fields_ = [("key", ctypes.c_uint), ("vers", _Vers), ("pLimitData", _PLimit), ("keyInfo", _KeyInfo),
                ("result", ctypes.c_ubyte), ("status", ctypes.c_ubyte), ("data8", ctypes.c_ubyte),
                ("data32", ctypes.c_uint), ("bytes", ctypes.c_ubyte * 32)]


_lock = threading.Lock()
_iokit = None          # the IOKit dylib
_conn = 0              # io_connect_t to AppleSMC (0 = not open)
_open_attempt_t = 0.0  # monotonic time of the last open attempt (for backoff)
_open_tried = False    # have we attempted to open at least once?
_temp_keys: list[str] = []   # temp sensor keys confirmed present on this machine
_OPEN_RETRY_S = 30.0   # re-attempt a failed open after this long (transient SMC unavailability)
_cache: "dict | None" = None
_cache_t = 0.0
_CACHE_TTL_S = 1.5     # reuse a recent snapshot so rapid polls don't each pay full IOKit cost


def _fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


def _fromcc(v: int) -> str:
    return bytes([(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]).decode("latin1")


def _open() -> bool:
    """Open the AppleSMC user client (cached). A failed open is retried after a backoff so a
    transient unavailability doesn't disable fan/temp for the whole process lifetime."""
    global _iokit, _conn, _open_attempt_t, _open_tried
    if _conn:
        return True
    now = time.monotonic()
    if _open_tried and (now - _open_attempt_t) < _OPEN_RETRY_S:
        return False
    _open_tried = True
    _open_attempt_t = now
    try:
        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        iokit.IOServiceOpen.restype = ctypes.c_int
        iokit.IOServiceOpen.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        iokit.IOObjectRelease.restype = ctypes.c_int
        iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        iokit.IOConnectCallStructMethod.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                                    ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        libc.mach_task_self.restype = ctypes.c_uint
        svc = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
        if not svc:
            return False
        conn = ctypes.c_uint(0)
        kr = iokit.IOServiceOpen(svc, libc.mach_task_self(), 0, ctypes.byref(conn))
        iokit.IOObjectRelease(svc)   # the connection persists; the service handle isn't needed after open
        if kr != 0 or conn.value == 0:
            return False
        _iokit, _conn = iokit, conn.value
        return True
    except Exception:
        _iokit, _conn = None, 0
        return False


def _call(inp: _KeyData) -> "_KeyData | None":
    out = _KeyData()
    osz = ctypes.c_size_t(ctypes.sizeof(_KeyData))
    try:
        kr = _iokit.IOConnectCallStructMethod(_conn, _KERNEL_INDEX_SMC, ctypes.byref(inp),
                                              ctypes.sizeof(_KeyData), ctypes.byref(out), ctypes.byref(osz))
    except Exception:
        return None
    return out if kr == 0 else None


def _read(key: str) -> "tuple[str, bytes] | None":
    """Read one SMC key → (dataType, raw bytes), or None if absent/unreadable."""
    inp = _KeyData()
    inp.key = _fourcc(key)
    inp.data8 = _SMC_CMD_READ_KEYINFO
    info = _call(inp)
    if info is None or info.keyInfo.dataSize == 0:
        return None
    size = info.keyInfo.dataSize
    dtype = _fromcc(info.keyInfo.dataType)
    inp2 = _KeyData()
    inp2.key = _fourcc(key)
    inp2.keyInfo.dataSize = size
    inp2.data8 = _SMC_CMD_READ_BYTES
    val = _call(inp2)
    if val is None:
        return None
    return dtype, bytes(val.bytes[:size])


def _decode(dtype: str, raw: bytes) -> "float | None":
    try:
        if dtype == "flt " and len(raw) >= 4:
            return struct.unpack("<f", raw[:4])[0]
        if dtype == "fpe2" and len(raw) >= 2:    # Intel-era fixed point (kept for completeness)
            return struct.unpack(">H", raw[:2])[0] / 4.0
        if dtype.startswith("ui") or dtype.startswith("si"):
            return float(int.from_bytes(raw, "big", signed=dtype.startswith("si")))
    except Exception:
        return None
    return None


def _read_num(key: str) -> "float | None":
    r = _read(key)
    return _decode(r[0], r[1]) if r else None


def read_fans() -> list[float]:
    """Actual RPM of each fan (empty on fanless Macs or if the SMC is unavailable)."""
    if not _open():
        return []
    count = _read_num("FNum")
    n = int(count) if count else 0
    fans: list[float] = []
    for i in range(n):
        rpm = _read_num(f"F{i}Ac")
        if rpm is not None and 0 <= rpm < 12000:
            fans.append(round(rpm))
    return fans


def read_temp_c() -> "float | None":
    """A representative SoC die temperature in °C, averaged over the core sensors present."""
    global _temp_keys
    if not _open():
        return None
    if not _temp_keys:   # (re)probe while empty — don't latch an empty set if the first probe came up dry
        _temp_keys = [k for k in _TEMP_KEYS
                      if (v := _read_num(k)) is not None and 5.0 < v < 120.0]
    vals = [v for k in _temp_keys if (v := _read_num(k)) is not None and 5.0 < v < 120.0]
    return round(sum(vals) / len(vals), 1) if vals else None


def stats() -> dict:
    """Combined SMC snapshot for /v1/sys. All fields best-effort; never raises. Cached for a
    short TTL so rapid polls / concurrent callers don't each pay the full IOKit cost.

    Call this OFF the event loop (run_in_executor) — the IOKit syscalls are blocking.
    fan_rpm = average across fans (the headline number); fan_max / fans give the detail.
    """
    global _cache, _cache_t
    with _lock:
        now = time.monotonic()
        if _cache is not None and (now - _cache_t) < _CACHE_TTL_S:
            return _cache
        try:
            fans = read_fans()
            temp = read_temp_c()
        except Exception:
            fans, temp = [], None
        out: dict = {"fan_count": len(fans), "fans": fans, "temp_c": temp}
        if fans:
            out["fan_rpm"] = round(sum(fans) / len(fans))
            out["fan_max"] = max(fans)
        else:
            out["fan_rpm"] = None
            out["fan_max"] = None
        _cache, _cache_t = out, now
        return out
