"""Read an NTFS volume's Master File Table directly (like WizTree does).

Walking folders asks the file system about every file one by one, which is
slow on hard disks because the head keeps jumping around. The MFT holds one
record per file, so reading it front to back gives the name, parent folder,
size and modification time of every file in one sequential pass.

Needs administrator rights (to open the raw volume) and only reads; nothing is
ever written. Any problem raises MftUnavailable and the caller falls back to
the normal folder walk.
"""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass

from opensweep.core.fsutil import Cancelled

ROOT_RECORD = 5
REC_MASK = 0x0000FFFFFFFFFFFF
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CHUNK = 4 * 1024 * 1024

_u16 = struct.Struct("<H").unpack_from
_u32 = struct.Struct("<I").unpack_from
_u64 = struct.Struct("<Q").unpack_from


class MftUnavailable(Exception):
    pass


@dataclass
class Geometry:
    sector: int
    cluster: int
    record: int
    mft_lcn: int


def _geometry(boot: bytes) -> Geometry:
    if boot[3:11] != b"NTFS    ":
        raise MftUnavailable("not an NTFS volume")
    sector = _u16(boot, 0x0B)[0]
    cluster = sector * boot[0x0D]
    mft_lcn = _u64(boot, 0x30)[0]
    raw = struct.unpack_from("<b", boot, 0x40)[0]
    record = 2 ** (-raw) if raw < 0 else raw * cluster
    return Geometry(sector, cluster, record, mft_lcn)


def _fixup(buf: bytearray, start: int, size: int, sector: int) -> bool:
    """Apply the update sequence array of one record in place. False if the record is torn."""
    usa_off = _u16(buf, start + 4)[0]
    usa_count = _u16(buf, start + 6)[0]
    if usa_off + usa_count * 2 > size:
        return False
    check = buf[start + usa_off:start + usa_off + 2]
    for i in range(1, usa_count):
        end = start + i * sector - 2
        if end + 2 > start + size:
            break
        if buf[end:end + 2] != check:
            return False
        buf[end:end + 2] = buf[start + usa_off + 2 * i:start + usa_off + 2 * i + 2]
    return True


def _data_runs(buf: bytes, off: int) -> list[tuple[int, int]]:
    """Decode a non-resident attribute's run list into (lcn, cluster count) extents."""
    runs, lcn = [], 0
    while True:
        header = buf[off]
        if header == 0:
            break
        len_size, off_size = header & 0x0F, header >> 4
        off += 1
        length = int.from_bytes(buf[off:off + len_size], "little")
        off += len_size
        if off_size:
            delta = int.from_bytes(buf[off:off + off_size], "little", signed=True)
            lcn += delta
            runs.append((lcn, length))
        off += off_size
    return runs


@dataclass
class Records:
    """Every in-use file and folder on the volume, keyed by MFT record number."""
    names: dict[int, str]
    parents: dict[int, int]
    sizes: dict[int, int]
    mtimes: dict[int, int]
    dirs: set[int]
    reparse: set[int]
    count: int


def read_records(drive: str, cancel: threading.Event | None = None, progress=None) -> Records:
    """Parse the MFT of `drive` ("C:\\")."""
    if os.name != "nt":
        raise MftUnavailable("Windows only")
    letter = drive.rstrip("\\/")
    try:
        fd = os.open(f"\\\\.\\{letter}", os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise MftUnavailable(f"cannot open volume: {exc}") from exc
    try:
        geo = _geometry(os.read(fd, 512))
        rec_size = geo.record

        # Record 0 describes the MFT itself: where its pieces are on disk.
        os.lseek(fd, geo.mft_lcn * geo.cluster, os.SEEK_SET)
        first = bytearray(os.read(fd, max(rec_size, geo.sector)))
        if first[:4] != b"FILE" or not _fixup(first, 0, rec_size, geo.sector):
            raise MftUnavailable("bad $MFT record")
        runs, mft_size = None, 0
        a = _u16(first, 0x14)[0]
        while a + 8 <= rec_size:
            atype, alen = _u32(first, a)[0], _u32(first, a + 4)[0]
            if atype == 0xFFFFFFFF or alen == 0:
                break
            if atype == 0x80 and first[a + 8] and first[a + 9] == 0:
                runs = _data_runs(first, a + _u16(first, a + 0x20)[0])
                mft_size = _u64(first, a + 0x30)[0]
                break
            a += alen
        if not runs:
            raise MftUnavailable("no $MFT data runs")

        names: dict[int, str] = {}
        name_ns: dict[int, int] = {}
        parents: dict[int, int] = {}
        sizes: dict[int, int] = {}
        mtimes: dict[int, int] = {}
        dirs: set[int] = set()
        reparse: set[int] = set()
        total_records = mft_size // rec_size
        recno = 0
        done_bytes = 0
        for lcn, clusters in runs:
            remaining = clusters * geo.cluster
            os.lseek(fd, lcn * geo.cluster, os.SEEK_SET)
            while remaining > 0 and recno < total_records:
                if cancel is not None and cancel.is_set():
                    raise Cancelled
                want = min(CHUNK, remaining)
                buf = bytearray(os.read(fd, want))
                if not buf:
                    break
                remaining -= len(buf)
                done_bytes += len(buf)
                for start in range(0, len(buf) - rec_size + 1, rec_size):
                    this = recno
                    recno += 1
                    if buf[start:start + 4] != b"FILE":
                        continue
                    flags = _u16(buf, start + 0x16)[0]
                    if not flags & 1:  # not in use
                        continue
                    if not _fixup(buf, start, rec_size, geo.sector):
                        continue
                    base = _u64(buf, start + 0x20)[0] & REC_MASK
                    owner = base or this
                    if flags & 2 and not base:
                        dirs.add(this)
                    a = start + _u16(buf, start + 0x14)[0]
                    end = start + rec_size
                    while a + 8 <= end:
                        atype = _u32(buf, a)[0]
                        if atype == 0xFFFFFFFF:
                            break
                        alen = _u32(buf, a + 4)[0]
                        if alen == 0 or a + alen > end:
                            break
                        nonres = buf[a + 8]
                        if atype == 0x10 and not nonres:      # $STANDARD_INFORMATION
                            c = a + _u16(buf, a + 0x14)[0]
                            mtimes[owner] = _u64(buf, c + 0x08)[0]
                            if _u32(buf, c + 0x20)[0] & FILE_ATTRIBUTE_REPARSE_POINT:
                                reparse.add(owner)
                        elif atype == 0x30 and not nonres:    # $FILE_NAME
                            c = a + _u16(buf, a + 0x14)[0]
                            ns = buf[c + 0x41]
                            prev = name_ns.get(owner)
                            # Prefer Win32 names over 8.3 DOS names; keep the first hard link only.
                            if prev is None or (prev == 2 and ns != 2):
                                length = buf[c + 0x40]
                                names[owner] = buf[c + 0x42:c + 0x42 + length * 2].decode("utf-16-le", "replace")
                                parents[owner] = _u64(buf, c)[0] & REC_MASK
                                name_ns[owner] = ns
                        elif atype == 0x80 and buf[a + 9] == 0:  # unnamed $DATA
                            if nonres:
                                if _u64(buf, a + 0x10)[0] == 0:  # first piece holds the real size
                                    sizes[owner] = _u64(buf, a + 0x30)[0]
                            else:
                                sizes[owner] = _u32(buf, a + 0x10)[0]
                        a += alen
                    if recno >= total_records:
                        break
                if progress:
                    progress(f"{len(names)}|{letter}\\$MFT  {done_bytes * 100 // max(mft_size, 1)}%")
        return Records(names, parents, sizes, mtimes, dirs, reparse, len(names))
    finally:
        os.close(fd)


def filetime_to_unix(ft: int) -> float:
    return ft / 10_000_000 - 11644473600 if ft else 0.0
