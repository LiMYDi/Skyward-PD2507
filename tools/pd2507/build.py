#!/usr/bin/env python3
"""Build a Skyward vendor_boot for the vivo iQOO Z10 Turbo+ (PD2507 / V2507A).

PD2507 has no recovery partition: recovery is the RECOVERY fragment of the
vendor_boot image.  skyward-pack only knows `ANDROID!` boot images, so this
pipeline wraps it:

    stock vendor_boot  --(extract RECOVERY fragment)-->  synthetic boot image
        --> skyward-pack recovery ... --> dressed boot image
        --> (take its ramdisk) --> put back into vendor_boot --> out

Every container step is byte-exact and verified against the stock image
(`--verify`), so anything that breaks is in skyward-pack or in Skyward itself.
"""
import argparse
import hashlib
import os
import struct
import subprocess
import sys
import urllib.request
import zlib

OTA_URL = "https://sysuptxdl.vivo.com.cn/upgrade/oem/files/202603140311285424ef3f0a973f58505eeae6db72af3f.zip"
PAGE = 4096
BOOT_MAGIC = b"ANDROID!"
VENDOR_MAGIC = b"VNDRBOOT"
AVB_MAGIC = b"AVBf"
AVB_FOOTER = 64
TYPE_RECOVERY = 2


# --------------------------------------------------------------------------
# HTTP ranges
# --------------------------------------------------------------------------
class Http:
    def __init__(self, url):
        self.url = url
        self.session = urllib.request.build_opener()

    def get(self, start, end, tries=4):
        last = None
        for _ in range(tries):
            try:
                req = urllib.request.Request(self.url, headers={
                    "User-Agent": "Mozilla/5.0", "Range": "bytes=%d-%d" % (start, end)})
                data = self.session.open(req, timeout=120).read()
                if len(data) == end - start + 1:
                    return data
                last = RuntimeError("short read %d/%d" % (len(data), end - start + 1))
            except Exception as e:  # noqa: BLE001 - network flake, retried
                last = e
        raise RuntimeError("range %d-%d failed: %s" % (start, end, last))


def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, i


def _fields(b):
    i, out = 0, []
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]
            i += n
        elif w == 5:
            v = struct.unpack("<I", b[i:i + 4])[0]
            i += 4
        elif w == 1:
            v = struct.unpack("<Q", b[i:i + 8])[0]
            i += 8
        else:
            raise ValueError("wire type %d" % w)
        out.append((f, w, v))
    return out


def fetch_partition(name, out_path):
    """Pull one partition out of the OTA over HTTP ranges, without the 9 GB."""
    if os.path.exists(out_path):
        print("cached %s" % out_path)
        return out_path
    http = Http(OTA_URL)
    tail = http.get(0, 600)
    nl, el = struct.unpack("<HH", tail[26:30])
    payload_at = 30 + nl + el  # payload.bin is the first entry, stored
    head = http.get(payload_at, payload_at + 23)
    if head[:4] != b"CrAU":
        raise SystemExit("payload.bin is not where the zip says")
    msize = struct.unpack(">Q", head[12:20])[0]
    sig = struct.unpack(">I", head[20:24])[0]
    manifest = http.get(payload_at + 24, payload_at + 23 + msize)
    base = payload_at + 24 + msize + sig  # this payload's data area

    part = None
    for v in [v for f, w, v in _fields(manifest) if f == 13]:
        fs = _fields(v)
        nm = [x for f, w, x in fs if f == 1][0].decode()
        if nm == name:
            part = fs
    if part is None:
        raise SystemExit("no %s partition in the package" % name)
    size = [v for f, w, v in _fields([x for f, w, x in part if f == 7][0]) if f == 1][0]
    buf = bytearray(size)
    for op in [x for f, w, x in part if f == 8]:
        fo = _fields(op)
        kind = [v for f, w, v in fo if f == 1][0]
        off = [v for f, w, v in fo if f == 2][0]
        ln = [v for f, w, v in fo if f == 3][0]
        want = ([v for f, w, v in fo if f == 8] or [None])[0]
        ext = [v for f, w, v in fo if f == 6]
        raw = http.get(base + off, base + off + ln - 1)
        if want and hashlib.sha256(raw).digest() != want:
            raise SystemExit("op at %d does not hash as the package says" % off)
        if kind == 0:
            data = raw
        elif kind in (13, 14):
            import zstandard
            data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 28)
        elif kind == 6:
            data = b"\0" * ln
        else:
            raise SystemExit("unsupported op type %d" % kind)
        pos = 0
        for e in ext:
            ef = _fields(e)
            sb = [v for f, w, v in ef if f == 1][0]
            nb = [v for f, w, v in ef if f == 2][0]
            n = nb * PAGE
            buf[sb * PAGE:sb * PAGE + n] = data[pos:pos + n]
            pos += n
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(buf)
    print("took %s out: %d bytes, sha256 %s" % (name, size, hashlib.sha256(buf).hexdigest()[:16]))
    return out_path


# --------------------------------------------------------------------------
# LZ4 legacy (what Android ramdisks here use)
# --------------------------------------------------------------------------
def lz4_legacy_decode(data):
    import lz4.block
    if data[:4] != b"\x02\x21\x4c\x18":
        raise ValueError("not an lz4 legacy stream")
    i, out = 4, bytearray()
    while i + 4 <= len(data):
        (n,) = struct.unpack("<I", data[i:i + 4])
        i += 4
        if n == 0 or i + n > len(data):
            break
        out += lz4.block.decompress(data[i:i + n], uncompressed_size=8 << 20)
        i += n
    return bytes(out)


def lz4_legacy_encode(data, block=8 << 20):
    import lz4.block
    out = bytearray(b"\x02\x21\x4c\x18")
    for i in range(0, len(data), block):
        blk = lz4.block.compress(data[i:i + block], mode="high_compression", store_size=False)
        out += struct.pack("<I", len(blk)) + blk
    return bytes(out)


def lz4_frame_encode(data):
    """A modern LZ4 frame — what skyward-pack's decoder reads."""
    import lz4.frame
    return lz4.frame.compress(bytes(data), compression_level=9)


def lz4_frame_decode(data):
    import lz4.frame
    return lz4.frame.decompress(bytes(data))


# --------------------------------------------------------------------------
# vendor_boot
# --------------------------------------------------------------------------
class VendorBoot:
    def __init__(self, raw):
        self.raw = raw
        self.image = raw
        self.vbmeta = b""
        if raw[-AVB_FOOTER:-AVB_FOOTER + 4] == AVB_MAGIC:
            _m, _a, _b, orig, vb_off, vb_size = struct.unpack(">4sIIQQQ", raw[-AVB_FOOTER:-AVB_FOOTER + 36])
            self.vbmeta = raw[vb_off:vb_off + vb_size]
            self.image = raw[:orig]
        img = self.image
        if img[:8] != VENDOR_MAGIC:
            raise SystemExit("not a vendor_boot image")
        h = self.header = {}
        h["page"] = struct.unpack("<I", img[12:16])[0]
        h["kernel_addr"] = struct.unpack("<I", img[16:20])[0]
        h["ramdisk_addr"] = struct.unpack("<I", img[20:24])[0]
        h["cmdline"] = img[28:28 + 2048].split(b"\0")[0]
        h["tags_addr"] = struct.unpack("<I", img[2076:2080])[0]
        h["name"] = img[2080:2096].split(b"\0")[0]
        h["dtb_size"] = struct.unpack("<I", img[2100:2104])[0]
        h["dtb_addr"] = struct.unpack("<Q", img[2104:2112])[0]
        h["table_size"] = struct.unpack("<I", img[2112:2116])[0]
        h["table_num"] = struct.unpack("<I", img[2116:2120])[0]
        h["entry_size"] = struct.unpack("<I", img[2120:2124])[0]
        h["bootconfig_size"] = struct.unpack("<I", img[2124:2128])[0]
        page = h["page"]
        off = _align(2128, page)
        self.area = img[off:off + struct.unpack("<I", img[24:28])[0]]
        off = _align(off + len(self.area), page)
        self.dtb = img[off:off + h["dtb_size"]]
        off = _align(off + len(self.dtb), page)
        self.table = img[off:off + h["table_size"]]
        off = _align(off + len(self.table), page)
        self.bootconfig = img[off:off + h["bootconfig_size"]]
        self.fragments = []
        for i in range(h["table_num"]):
            e = self.table[i * h["entry_size"]:i * h["entry_size"] + 108]
            size, at, kind = struct.unpack("<III", e[:12])
            self.fragments.append({
                "kind": kind,
                "name": e[12:44].split(b"\0")[0].decode("utf-8", "replace"),
                "board_id": list(struct.unpack("<16I", e[44:108])),
                "data": self.area[at:at + size],
            })

    def recovery(self):
        for f in self.fragments:
            if f["kind"] == TYPE_RECOVERY:
                return f
        raise SystemExit("vendor_boot has no RECOVERY fragment")

    def build(self):
        page = self.header["page"]
        table = bytearray()
        off = 0
        for f in self.fragments:
            table += struct.pack("<III", len(f["data"]), off, f["kind"])
            table += f["name"].encode()[:31].ljust(32, b"\0")
            table += struct.pack("<16I", *f["board_id"])
            off += len(f["data"])
        area = b"".join(f["data"] for f in self.fragments)

        hdr = bytearray(page)
        hdr[0:8] = VENDOR_MAGIC
        struct.pack_into("<I", hdr, 8, 4)
        struct.pack_into("<I", hdr, 12, page)
        struct.pack_into("<I", hdr, 16, self.header["kernel_addr"])
        struct.pack_into("<I", hdr, 20, self.header["ramdisk_addr"])
        struct.pack_into("<I", hdr, 24, len(area))
        cmd = self.header["cmdline"][:2047]
        hdr[28:28 + len(cmd)] = cmd
        struct.pack_into("<I", hdr, 2076, self.header["tags_addr"])
        nm = self.header["name"][:15]
        hdr[2080:2080 + len(nm)] = nm
        struct.pack_into("<I", hdr, 2096, 2128)
        struct.pack_into("<I", hdr, 2100, len(self.dtb))
        struct.pack_into("<Q", hdr, 2104, self.header["dtb_addr"])
        struct.pack_into("<I", hdr, 2112, len(table))
        struct.pack_into("<I", hdr, 2116, len(self.fragments))
        struct.pack_into("<I", hdr, 2120, 108)
        struct.pack_into("<I", hdr, 2124, len(self.bootconfig))

        out = bytearray(hdr) + area
        out = _pad(out, page) + self.dtb
        out = _pad(out, page) + table
        out = _pad(out, page) + self.bootconfig
        out = _pad(out, page)
        return bytes(out)

    def with_avb(self, image):
        total = max(len(self.raw), len(image) + len(self.vbmeta) + AVB_FOOTER)
        out = bytearray(total)
        out[:len(image)] = image
        out[len(image):len(image) + len(self.vbmeta)] = self.vbmeta
        struct.pack_into(">4sIIQQQ", out, total - AVB_FOOTER,
                         AVB_MAGIC, 1, 0, len(image), len(image), len(self.vbmeta))
        return bytes(out)


# --------------------------------------------------------------------------
# synthetic / dressed boot images
# --------------------------------------------------------------------------
def boot_image(ramdisk, size_hint):
    """An `ANDROID!` v4 image carrying `ramdisk`, padded to `size_hint` and
    ended with an AVB footer, the shape skyward-pack expects of a stock
    recovery partition."""
    hdr = bytearray(PAGE)
    hdr[0:8] = BOOT_MAGIC
    struct.pack_into("<I", hdr, 8, 0)             # kernel_size
    struct.pack_into("<I", hdr, 12, len(ramdisk))  # ramdisk_size
    struct.pack_into("<I", hdr, 20, 1584)          # header_size
    struct.pack_into("<I", hdr, 40, 4)             # header_version
    out = bytearray(hdr) + ramdisk
    out = bytearray(_pad(out, PAGE))
    total = max(size_hint, len(out) + AVB_FOOTER)
    out = bytearray(out) + bytearray(total - len(out))
    struct.pack_into(">4sIIQQQ", out, total - AVB_FOOTER, AVB_MAGIC, 1, 0, len(out) - AVB_FOOTER, 0, 0)
    return bytes(out)


def boot_ramdisk(data):
    if data[:8] != BOOT_MAGIC:
        raise SystemExit("skyward-pack did not write a boot image")
    ksize = struct.unpack("<I", data[8:12])[0]
    rsize = struct.unpack("<I", data[12:16])[0]
    at = PAGE + _align(ksize, PAGE)
    return data[at:at + rsize]


def _align(n, page):
    return (n + page - 1) // page * page


def _pad(buf, page):
    pad = _align(len(buf), page) - len(buf)
    return bytes(buf) + b"\0" * pad


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="out/pd2507")
    ap.add_argument("--stock", help="use this vendor_boot image instead of fetching it")
    ap.add_argument("--stock-url", help="download the stock vendor_boot from this URL instead of the package")
    ap.add_argument("--pack", help="path to a built skyward-pack; without it only the stock image is fetched")
    ap.add_argument("--binary", help="skyward built for aarch64-linux-android")
    ap.add_argument("--system-init", help="skyward-system-init, linked statically")
    ap.add_argument("--config", default="devices/pd2507.toml")
    ap.add_argument("--fonts", default="upstream/resources/fonts")
    ap.add_argument("--libraries", help="folder of libraries the recovery lacks")
    ap.add_argument("--out", default="out/vendor_boot_pd2507.img")
    ap.add_argument("--verify", action="store_true", help="check the container round-trips byte for byte")
    a = ap.parse_args(argv)

    os.makedirs(a.work, exist_ok=True)
    if a.stock:
        stock = a.stock
    elif a.stock_url:
        stock = os.path.join(a.work, "stock_vendor_boot.img")
        if not os.path.exists(stock):
            print("downloading %s" % a.stock_url)
            with urllib.request.urlopen(urllib.request.Request(a.stock_url, headers={"User-Agent": "dsh"}), timeout=600) as r, open(stock, "wb") as fh:
                while True:
                    buf = r.read(1 << 20)
                    if not buf:
                        break
                    fh.write(buf)
    else:
        stock = fetch_partition("vendor_boot", os.path.join(a.work, "stock_vendor_boot.img"))
    raw = open(stock, "rb").read()
    vb = VendorBoot(raw)
    frag = vb.recovery()

    # The container must be byte-exact before anything else is trusted.
    if vb.build() != vb.image or vb.with_avb(vb.build()) != raw:
        raise SystemExit("vendor_boot round-trip is not byte-exact; refusing to build")
    print("container: %d fragments, recovery %d bytes, round-trip exact" % (len(vb.fragments), len(frag["data"])))
    if a.verify or not a.pack:
        return 0

    cpio = lz4_legacy_decode(frag["data"])
    print("recovery ramdisk: %d bytes compressed, %d bytes of cpio" % (len(frag["data"]), len(cpio)))
    synth = os.path.join(a.work, "synth_boot.img")
    dressed = os.path.join(a.work, "dressed_boot.img")
    # skyward-pack decodes a modern LZ4 frame; the stock fragment is legacy
    # framing, so hand it the same cpio in a frame and take back what it writes.
    open(synth, "wb").write(boot_image(lz4_frame_encode(cpio), len(raw)))

    cmd = [a.pack, "recovery", "--stock", synth, "--binary", a.binary,
           "--system-init", a.system_init, "--config", a.config,
           "--font", os.path.join(a.fonts, "MiSansVF.ttf"),
           "--mono", os.path.join(a.fonts, "KirkMono-Regular.ttf"),
           "--mono-license", os.path.join(a.fonts, "KirkMono-OFL.txt"),
           "--out", dressed]
    if a.libraries:
        cmd += ["--libraries", a.libraries]
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)

    new_ramdisk = boot_ramdisk(open(dressed, "rb").read())
    if new_ramdisk[:4] != b"\x02\x21\x4c\x18":
        # Whatever framing came back, the fragment must be legacy LZ4 for the
        # kernel's initramfs unpacker to read it.
        print("re-wrapping the ramdisk from %s into legacy framing" % new_ramdisk[:4].hex())
        new_ramdisk = lz4_legacy_encode(lz4_frame_decode(new_ramdisk))
    frag["data"] = new_ramdisk
    image = vb.with_avb(vb.build())
    if len(image) > len(raw):
        raise SystemExit("image grew past the partition: %d > %d" % (len(image), len(raw)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "wb").write(image)
    print("wrote %s (%d bytes, partition %d)" % (a.out, len(image), len(raw)))
    print("sha256 %s" % hashlib.sha256(image).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
