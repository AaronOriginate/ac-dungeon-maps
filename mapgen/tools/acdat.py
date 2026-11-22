"""Minimal AC dat reader: directory, EnvCell, Environment/CellStruct geometry."""
import struct
from pathlib import Path

DATDIR = Path(__file__).resolve().parents[3] / "game-data" / "client-files"


class Dat:
    def __init__(self, path):
        self.f = open(path, "rb")
        try:
            self.f.seek(0x140)
            h = struct.unpack("<9I", self.f.read(36))
            self.block = h[1]
            if self.block <= 4:
                raise ValueError("Invalid dat block size")
            self.files = {}
            self._dir(h[8], set())
        except BaseException:
            self.f.close()
            raise

    def _raw(self, off, size):
        out = bytearray()
        visited = set()
        while size:
            if off in visited:
                raise ValueError("Repeated dat block")
            visited.add(off)
            self.f.seek(off)
            nxt, = struct.unpack("<I", self.f.read(4))
            n = min(size, self.block - 4)
            chunk = self.f.read(n)
            if len(chunk) != n or (size > n and not nxt):
                raise ValueError("Truncated dat block chain")
            out.extend(chunk)
            size -= n
            off = nxt
        return bytes(out)

    def _dir(self, off, visited):
        if off in visited:
            raise ValueError("Repeated dat directory")
        visited.add(off)
        sz = 4 * 0x3E + 4 + 24 * 0x3D
        b = self._raw(off, sz)
        br = struct.unpack_from("<62I", b, 0)
        n = struct.unpack_from("<I", b, 248)[0]
        if n > 0x3D:
            raise ValueError("Invalid dat directory size")
        for i in range(n):
            _, oid, foff, fsz, _, _ = struct.unpack_from("<6I", b, 252 + 24 * i)
            self.files[oid] = (foff, fsz)
        if br[0]:
            for child in br[:n + 1]:
                self._dir(child, visited)

    def get(self, oid):
        if oid not in self.files: return None
        return self._raw(*self.files[oid])


class R:
    def __init__(self, b): self.b, self.p = b, 0
    def u(self, fmt):
        v = struct.unpack_from("<" + fmt, self.b, self.p); self.p += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]
    def align(self): self.p = (self.p + 3) & ~3


def frame(r):
    ox, oy, oz, qw, qx, qy, qz = r.u("7f")
    return (ox, oy, oz), (qw, qx, qy, qz)


def qrot(q, v):
    w, x, y, z = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2 * (y * vz - z * vy); ty = 2 * (z * vx - x * vz); tz = 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def env_cell(b):
    r = R(b)
    cid, flags, _ = r.u("3I")
    nsurf, nport, nstab = r.u("BBH")
    r.p += 2 * nsurf
    env = 0x0D000000 | r.u("H")
    cs = r.u("H")
    pos, rot = frame(r)
    portals = []
    for _ in range(nport):
        fl, poly, other, otherp = r.u("4H")
        portals.append(dict(flags=fl, poly=poly, other=other, otherp=otherp))
    vis = [r.u("H") for _ in range(nstab)]
    statics = []
    if flags & 2:
        n = r.u("I")
        for _ in range(n):
            sid = r.u("I"); statics.append((sid,) + frame(r))
    return dict(id=cid, flags=flags, env=env, cs=cs, pos=pos, rot=rot, portals=portals, vis=vis, statics=statics)


def polygon(r, alt=False):
    npts, stip = r.u("BB")
    sides = r.u("i")
    r.u("hh")
    vids = [r.u("h") for _ in range(npts)]
    if not stip & 4: r.p += npts
    # Only clockwise polygons store a separate set of negative-side UV indices.
    if sides == 2 and not stip & 8 and not (alt and stip & 4): r.p += npts
    return vids


def _is_tag(b):
    try: t = b[::-1].decode("ascii")
    except UnicodeDecodeError: return False
    return len(t) == 4 and t.isalpha()


def _badpolys(cs):
    return sum(1 for v in cs["polys"].values() if len(v) < 3 or any(x not in cs["verts"] for x in v))


def _plausible_after(r):
    q = (r.p + 3) & ~3
    return _is_tag(r.b[q:q + 4])


def cell_struct(r, alt=False):
    npoly, nphys, nport = r.u("3I")
    vt, nv = r.u("iI")
    if vt != 1:
        raise ValueError("Unknown cell vertex format")
    verts = {}
    for _ in range(nv):
        k = r.u("H"); nuv = r.u("H")
        o = r.u("3f"); r.u("3f"); r.p += 8 * nuv
        verts[k] = o
    polys = {}
    for _ in range(npoly):
        k = r.u("H"); polys[k] = polygon(r, alt)
    portal_polys = [r.u("H") for _ in range(nport)]
    return dict(verts=verts, polys=polys, portal_polys=portal_polys, nphys=nphys)


def _parse_at(r, hdr, last=False):
    try:
        cs = cell_struct(r)
    except (struct.error, ValueError):
        cs = None
    if cs is None or (not last and not _plausible_after(r)) or _badpolys(cs):
        # visible polygons may use the no-UV layout: try it and keep whichever reads cleaner
        p1 = r.p
        r.p = hdr + 4
        try:
            cs2 = cell_struct(r, alt=True)
        except (struct.error, ValueError):
            cs2 = None
        if cs2 is not None and (cs is None or _badpolys(cs2) < _badpolys(cs)): cs = cs2
        elif cs is not None: r.p = p1
    return cs


def environment(b):
    r = R(b)
    r.u("I")
    n = r.u("I")
    out = {}
    for idx in range(n):
        hdr = r.p
        k = r.u("I")
        cs = _parse_at(r, hdr, idx == n - 1)
        if cs is None:   # false header from the previous resync: look further on for key k
            q = hdr + 2
            while q + 24 <= len(b) and not (_struct_head(b, q) and struct.unpack_from("<I", b, q)[0] == k): q += 2
            if q + 24 > len(b): break
            hdr = q; r.p = q + 4
            cs = _parse_at(r, hdr, idx == n - 1)
            if cs is None: break
        for pid in [pid for pid, v in cs["polys"].items() if any(x not in cs["verts"] for x in v) or len(v) < 3]:
            del cs["polys"][pid]
        out[k] = cs
        if idx == n - 1: break
        ok = False
        try:
            r.align()
            skip_bsp(r, 2)
            p0 = r.p
            for alt in (False, True):
                r.p = p0
                try:
                    for _ in range(cs["nphys"]):
                        r.u("H"); polygon(r, alt)
                except struct.error:
                    continue
                if _is_tag(r.b[r.p:r.p + 4]): break
            skip_bsp(r, 1)
            if r.u("I"): skip_bsp(r, 0)
            r.align()
            ok = _struct_head(r.b, r.p) and struct.unpack_from("<I", r.b, r.p)[0] == k + 1
        except (struct.error, RecursionError, UnicodeDecodeError):
            ok = False
        if not ok:   # resync: find the next struct header (key k+1) after the polygons
            q = hdr + 24
            while q + 24 <= len(b) and not (_struct_head(b, q) and struct.unpack_from("<I", b, q)[0] == k + 1): q += 2
            if q + 24 > len(b): break
            r.p = q
    return out


def _struct_head(b, p):
    if p + 24 > len(b): return False
    k, npoly, nphys, nport, vt, nv = struct.unpack_from("<IIIIiI", b, p)
    return k < 4096 and 0 < npoly < 5000 and nphys < 5000 and nport < 64 and vt == 1 and 0 < nv < 5000


def skip_bsp(r, kind):
    """BSP node skipping. kind: 0 drawing, 1 physics, 2 cell."""
    tag = r.b[r.p:r.p + 4][::-1].decode("latin1"); r.p += 4
    if tag == "LEAF":
        if kind == 1:
            r.u("I")  # leaf index
            r.u("I")  # solid
            r.u("4f")  # sphere
            n = r.u("I"); r.p += 2 * n
        else:
            r.u("I")
        return
    if tag == "PORT":
        r.u("4f")
        skip_bsp(r, kind); skip_bsp(r, kind)
        if kind == 0:
            r.u("4f")
            np_ = r.u("I"); npo = r.u("I")
            r.p += 2 * np_
            r.p += 4 * npo
        return
    r.u("4f")  # plane
    if tag in ("BPnn", "BPIn"):
        skip_bsp(r, kind)
    elif tag in ("BpIN", "BpnN"):
        skip_bsp(r, kind)
    elif tag in ("BPIN", "BPnN"):
        skip_bsp(r, kind); skip_bsp(r, kind)
    if kind == 2: return
    r.u("4f")
    if kind == 0:
        n = r.u("I"); r.p += 2 * n


# ---------------------------------------------------------------- static objects
def rcu(r):
    b0 = r.u("B")
    if not b0 & 0x80: return b0
    b1 = r.u("B")
    if not b0 & 0x40: return ((b0 & 0x7F) << 8) | b1
    s = r.u("H")
    return ((((b0 & 0x3F) << 8) | b1) << 16) | s


def gfxobj_phys(b):
    """physics geometry of a GfxObj: (verts, polys) or None if it has no physics"""
    r = R(b)
    r.u("I"); flags = r.u("I")
    n = rcu(r); r.p += 4 * n
    vt, nv = r.u("iI")
    if vt != 1: return None
    verts = {}
    for _ in range(nv):
        k = r.u("H"); nuv = r.u("H")
        o = r.u("3f"); r.u("3f"); r.p += 8 * nuv
        verts[k] = o
    if not flags & 1: return None
    np_ = rcu(r); p0 = r.p
    for alt in (False, True):
        r.p = p0; polys = {}
        try:
            for _ in range(np_):
                k = r.u("H"); polys[k] = polygon(r, alt)
        except struct.error:
            continue
        if all(len(v) >= 3 and all(x in verts for x in v) for v in polys.values()) and _is_tag(r.b[r.p:r.p + 4]): break
    polys = {k: v for k, v in polys.items() if len(v) >= 3 and all(x in verts for x in v)}
    return verts, polys


def setup_parts(b):
    """[(gfxobj id, origin, quat(w,x,y,z), scale)] using the first placement frame"""
    r = R(b)
    r.u("I"); flags = r.u("I")
    n = r.u("I"); parts = [r.u("I") for _ in range(n)]
    if flags & 1: r.p += 4 * n
    scales = [r.u("3f") for _ in range(n)] if flags & 2 else [(1.0, 1.0, 1.0)] * n
    for _ in range(2):                     # holding locations, connection points
        c = r.u("i"); r.p += c * (4 + 4 + 28)
    pc = r.u("i")
    frames = None
    if pc > 0:
        r.u("i")
        frames = [frame(r) for _ in range(n)]
    if frames is None: frames = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))] * n
    return [(parts[i], frames[i][0], frames[i][1], scales[i]) for i in range(n)]
