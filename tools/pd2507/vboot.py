#!/usr/bin/env python3
"""Vendor boot image (VNDRBOOT) reader / repacker — prototype for the PD2507 port.

Android's vendor_boot holds the phone's platform ramdisk, the DTBs and, on
devices without a recovery partition, the recovery ramdisk as one fragment of
its ramdisk table.  Skyward's pack tool only knows `ANDROID!` boot images, so
this is the missing half: parse the header and the fragment table, and put an
image back together byte for byte.

Subcommands:
    info <img>                      header fields, fragments, AVB extent
    verify <img>                    parse and repack, expect identical bytes
    replace-recovery <img> <cpio> <out>
                                    swap the RECOVERY fragment's ramdisk for
                                    <cpio> (uncompressed newc cpio)
"""
import argparse
import hashlib
import struct
import sys

MAGIC = b"VNDRBOOT"
AVB_FOOTER_MAGIC = b"AVBf"
AVB_FOOTER_SIZE = 64

# vendor_boot v4 header sizes (see Android's bootimg.h).
HDR_V3_FIXED = 2112
HDR_V4_FIXED = 2128

# vendor_ramdisk_table_entry_v4: size, offset, type, name[32], board_id[16]
ENTRY_SIZE = 108
TYPE_PLATFORM = 1
TYPE_RECOVERY = 2
TYPE_DLKM = 3
TYPE_VENDOR = 4
TYPE_NAMES = {TYPE_PLATFORM: "platform", TYPE_RECOVERY: "recovery", TYPE_DLKM: "dlkm", TYPE_VENDOR: "vendor"}


class VendorBoot:
    """A parsed vendor_boot image, with its AVB trailer kept aside."""

    def __init__(self, raw):
        self.raw = raw
        self.avb_footer = None
        self.avb_vbmeta = b""
        self.image = raw
        self._split_avb()
        self._parse()
        self._parse_table()

    # ---- AVB trailer -------------------------------------------------------
    def _split_avb(self):
        raw = self.raw
        if len(raw) >= AVB_FOOTER_SIZE + 4 and raw[-AVB_FOOTER_SIZE:-AVB_FOOTER_SIZE + 4] == AVB_FOOTER_MAGIC:
            magic, _maj, _min, orig, vb_off, vb_size = struct.unpack(">4sIIQQQ", raw[-AVB_FOOTER_SIZE:-AVB_FOOTER_SIZE + 36])
            self.avb_footer = {"original_image_size": orig, "vbmeta_offset": vb_off, "vbmeta_size": vb_size}
            self.avb_vbmeta = raw[vb_off:vb_off + vb_size]
            self.image = raw[:orig]
        else:
            self.avb_footer = None
            self.image = raw

    # ---- header ------------------------------------------------------------
    def _parse(self):
        img = self.image
        if img[:8] != MAGIC:
            raise ValueError("not a vendor_boot image (magic %r)" % img[:8])
        h = self.header = {}
        h["header_version"] = struct.unpack("<I", img[8:12])[0]
        h["page_size"] = struct.unpack("<I", img[12:16])[0]
        h["kernel_addr"] = struct.unpack("<I", img[16:20])[0]
        h["ramdisk_addr"] = struct.unpack("<I", img[20:24])[0]
        h["vendor_ramdisk_size"] = struct.unpack("<I", img[24:28])[0]
        h["cmdline"] = img[28:28 + 2048].split(b"\0")[0]
        h["tags_addr"] = struct.unpack("<I", img[2076:2080])[0]
        h["name"] = img[2080:2096].split(b"\0")[0]
        h["header_size"] = struct.unpack("<I", img[2096:2100])[0]
        h["dtb_size"] = struct.unpack("<I", img[2100:2104])[0]
        h["dtb_addr"] = struct.unpack("<Q", img[2104:2112])[0]
        h["vendor_ramdisk_table_size"] = struct.unpack("<I", img[2112:2116])[0]
        h["vendor_ramdisk_table_entry_num"] = struct.unpack("<I", img[2116:2120])[0]
        h["vendor_ramdisk_table_entry_size"] = struct.unpack("<I", img[2120:2124])[0]
        h["bootconfig_size"] = struct.unpack("<I", img[2124:2128])[0]
        if h["header_version"] != 4:
            raise ValueError("only vendor_boot v4 is handled, got v%d" % h["header_version"])

        page = h["page_size"]
        off = _align(h["header_size"], page)
        self.ramdisk_off = off
        self.vendor_ramdisk = img[off:off + h["vendor_ramdisk_size"]]
        off = _align(off + h["vendor_ramdisk_size"], page)
        self.dtb_off = off
        self.dtb = img[off:off + h["dtb_size"]]
        off = _align(off + h["dtb_size"], page)
        self.table_off = off
        self.table = img[off:off + h["vendor_ramdisk_table_size"]]
        off = _align(off + h["vendor_ramdisk_table_size"], page)
        self.bootconfig_off = off
        self.bootconfig = img[off:off + h["bootconfig_size"]]
        self.image_size = _align(off + h["bootconfig_size"], page)

    def _parse_table(self):
        self.fragments = []
        for i in range(self.header["vendor_ramdisk_table_entry_num"]):
            at = i * self.header["vendor_ramdisk_table_entry_size"]
            e = self.table[at:at + ENTRY_SIZE]
            size, off, kind = struct.unpack("<III", e[:12])
            name = e[12:44].split(b"\0")[0].decode("utf-8", "replace")
            self.fragments.append({
                "index": i, "size": size, "offset": off, "type": kind,
                "type_name": TYPE_NAMES.get(kind, "type-%d" % kind),
                "name": name,
                "board_id": list(struct.unpack("<16I", e[44:108])),
                "data": self.vendor_ramdisk[off:off + size],
            })

    # ---- packing -----------------------------------------------------------
    def fragment_bytes(self):
        """The ramdisk area: every fragment's data, in table order."""
        return b"".join(f["data"] for f in self.fragments)

    def build(self):
        """Repack the image with the current fragments, header and trailers."""
        page = self.header["page_size"]
        area = self.fragment_bytes()
        dtb = self.dtb
        bootconfig = self.bootconfig

        table = bytearray()
        off = 0
        for f in self.fragments:
            e = struct.pack("<III", len(f["data"]), off, f["type"])
            e += f["name"].encode()[:31].ljust(32, b"\0")
            e += struct.pack("<16I", *f["board_id"])
            table += e
            off += len(f["data"])

        hdr = bytearray(bytearray(page))
        hdr[0:8] = MAGIC
        struct.pack_into("<I", hdr, 8, 4)
        struct.pack_into("<I", hdr, 12, page)
        struct.pack_into("<I", hdr, 16, self.header["kernel_addr"])
        struct.pack_into("<I", hdr, 20, self.header["ramdisk_addr"])
        struct.pack_into("<I", hdr, 24, len(area))
        cl = self.header["cmdline"][:2047]
        hdr[28:28 + len(cl)] = cl
        struct.pack_into("<I", hdr, 2076, self.header["tags_addr"])
        nm = self.header["name"][:15]
        hdr[2080:2080 + len(nm)] = nm
        struct.pack_into("<I", hdr, 2096, HDR_V4_FIXED)
        struct.pack_into("<I", hdr, 2100, len(dtb))
        struct.pack_into("<Q", hdr, 2104, self.header["dtb_addr"])
        struct.pack_into("<I", hdr, 2112, len(table))
        struct.pack_into("<I", hdr, 2116, len(self.fragments))
        struct.pack_into("<I", hdr, 2120, ENTRY_SIZE)
        struct.pack_into("<I", hdr, 2124, len(bootconfig))

        out = bytearray()
        out += hdr
        out += area
        out = _pad(out, page)
        out += dtb
        out = _pad(out, page)
        out += table
        out = _pad(out, page)
        out += bootconfig
        out = _pad(out, page)
        return bytes(out)

    def image_with_avb(self, new_image=None):
        """The image plus whatever AVB trailer follows it.  A changed image
        cannot keep its signature; the vbmeta blob is kept as it was, since
        only the partition's own verifier reads it and an unlocked bootloader
        with verification off ignores it."""
        img = self.build() if new_image is None else new_image
        if self.avb_footer is None:
            return img
        total = max(len(self.raw), len(img) + len(self.avb_vbmeta) + AVB_FOOTER_SIZE)
        out = bytearray(total)
        out[:len(img)] = img
        vb_off = len(img)
        out[vb_off:vb_off + len(self.avb_vbmeta)] = self.avb_vbmeta
        if vb_off + len(self.avb_vbmeta) + AVB_FOOTER_SIZE > total:
            raise ValueError("image does not fit the partition")
        struct.pack_into(">4sIIQQQ", out, total - AVB_FOOTER_SIZE,
                         AVB_FOOTER_MAGIC, 1, 0, len(img), vb_off, len(self.avb_vbmeta))
        return bytes(out)


def _align(n, page):
    return (n + page - 1) // page * page


def _pad(buf, page):
    return buf + b"\0" * (_align(len(buf), page) - len(buf))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("img")
    p = sub.add_parser("verify")
    p.add_argument("img")
    p = sub.add_parser("replace-recovery")
    p.add_argument("img")
    p.add_argument("cpio")
    p.add_argument("out")
    a = ap.parse_args(argv)

    raw = open(a.img, "rb").read()
    vb = VendorBoot(raw)

    if a.cmd == "info":
        h = vb.header
        print("image %s  %d bytes (image %d + vbmeta %d + pad)" % (
            a.img, len(raw), len(vb.image), len(vb.avb_vbmeta)))
        print("  page_size      %d" % h["page_size"])
        print("  cmdline        %s" % h["cmdline"].decode("utf-8", "replace"))
        print("  dtb_size       %d" % h["dtb_size"])
        print("  bootconfig     %d" % h["bootconfig_size"])
        print("  fragments      %d" % len(vb.fragments))
        for f in vb.fragments:
            print("    [%d] %-8s %-12s size=%-10d off=%-10d magic=%s" % (
                f["index"], f["type_name"], f["name"], f["size"], f["offset"], f["data"][:4].hex()))
        if vb.avb_footer:
            print("  avb            vbmeta %d bytes, orig size %d" % (
                vb.avb_footer["vbmeta_size"], vb.avb_footer["original_image_size"]))
        return 0

    if a.cmd == "verify":
        same_image = vb.build() == vb.image
        rebuilt = vb.image_with_avb()
        print("image round-trip :", "identical" if same_image else "DIFFERS")
        print("partition        :", "identical" if rebuilt == raw else "DIFFERS (%d vs %d bytes)" % (len(rebuilt), len(raw)))
        if rebuilt == raw:
            print("sha256           :", hashlib.sha256(rebuilt).hexdigest())
        return 0 if rebuilt == raw else 1

    if a.cmd == "replace-recovery":
        cpio = open(a.cpio, "rb").read()
        tgt = [f for f in vb.fragments if f["type"] == TYPE_RECOVERY]
        if not tgt:
            print("no RECOVERY fragment", file=sys.stderr)
            return 2
        tgt[0]["data"] = compress_lz4_legacy(cpio)
        tgt[0]["size"] = len(tgt[0]["data"])
        img = vb.build()
        out = vb.image_with_avb(img)
        open(a.out, "wb").write(out)
        print("wrote %s  %d bytes (was %d)" % (a.out, len(out), len(raw)))
        print("recovery fragment %d -> %d bytes" % (tgt[0]["size"], tgt[0]["size"]))
        return 0
    return 0


def compress_lz4_legacy(data, block=8 * 1024 * 1024):
    """LZ4 legacy framing: magic, then [u32 compressed_size][lz4 block]...,
    which is what this phone's stock ramdisks use."""
    import lz4.block
    out = bytearray(b"\x02\x21\x4c\x18")
    for i in range(0, len(data), block):
        blk = lz4.block.compress(data[i:i + block], mode="high_compression", store_size=False)
        out += struct.pack("<I", len(blk)) + blk
    return bytes(out)


if __name__ == "__main__":
    sys.exit(main())
