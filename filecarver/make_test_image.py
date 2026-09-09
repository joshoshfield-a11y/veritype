#!/usr/bin/env python3
"""make_test_image.py -- build a synthetic disk image and self-test carver.py.

Creates test_image.bin containing:
  * random noise (simulates unallocated/deleted data),
  * one minimal valid JPEG (built with PIL if available, else a fixed
    known-good minimal JPEG byte sequence with proper FFD8...FFD9),
  * one minimal PDF (%PDF-1.4 ... %%EOF),
  * one 2 KB printable-ASCII text block.

Then runs carver.py against the image and asserts that exactly one file
of each type is carved at the correct offsets with a correct manifest.
"""

from __future__ import annotations

import csv
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CARVER = os.path.join(HERE, "carver.py")

# Known-good minimal JPEG (1x1 white pixel) with proper FFD8 FFE0 ... FFD9.
# Used when Pillow is not installed.
MINIMAL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000"
    "ffdb0043000302020302020303030304030304050805050404050a07070608"
    "0c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113141515150c0f1718"
    "16141812141514"
    "ffc0000b080001000101011100"
    "ffc40014000100000000000000000000000000000000000008"
    "ffc400140101000000000000000000000000000000000000"
    "ffda0008010100003f00d2cf20ffd9"
)


def build_jpeg() -> bytes:
    """Return real JPEG bytes: via PIL if available, else the fixed blob."""
    try:
        from PIL import Image  # noqa: PLC0415
        import io
        buf = io.BytesIO()
        img = Image.new("RGB", (32, 24), (200, 30, 60))
        # draw a simple pattern so the image isn't trivially uniform
        for x in range(32):
            for y in range(24):
                if (x + y) % 7 == 0:
                    img.putpixel((x, y), (30, 200, 90))
        img.save(buf, format="JPEG")
        data = buf.getvalue()
        assert data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9"
        print(f"[gen] JPEG built with PIL ({len(data)} bytes)")
        return data
    except ImportError:
        print(f"[gen] PIL not available; using embedded minimal JPEG "
              f"({len(MINIMAL_JPEG)} bytes)")
        return MINIMAL_JPEG


def build_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
        b"%%EOF\n"
    )


def build_txt() -> bytes:
    body = b""
    i = 0
    while len(body) < 2048:
        if i % 2 == 0:
            body += b"The quick brown fox jumps over the lazy dog. " \
                    b"0123456789\n"
        else:
            body += b"Recoverable plain-text block, line %02d.\n" % (i // 2)
        i += 1
    return body[:2048]


def noise(n: int, rng: random.Random) -> bytes:
    """Random bytes guaranteed to contain no accidental signatures."""
    out = bytearray(rng.randbytes(n) if hasattr(rng, "randbytes")
                    else bytes(rng.randrange(256) for _ in range(n)))
    for sig in (b"\xff\xd8\xff", b"\xff\xd9", b"%PDF-", b"%%EOF"):
        while True:
            i = bytes(out).find(sig)
            if i == -1:
                break
            out[i] = (out[i] + 1) % 256
    # Force non-printable flanks so embedded payloads (esp. the TXT run)
    # are never merged with accidentally printable noise bytes.
    if n >= 16:
        out[:8] = b"\x00" * 8
        out[-8:] = b"\x00" * 8
    return bytes(out)


def main() -> int:
    rng = random.Random(1337)
    jpg, pdf, txt = build_jpeg(), build_pdf(), build_txt()

    parts, offsets = [], {}
    cursor = 0

    def add(name, blob):
        nonlocal cursor
        parts.append(blob)
        if name:
            offsets[name] = cursor
        cursor += len(blob)

    add(None, noise(64 * 1024, rng))
    add("jpg", jpg)
    add(None, noise(96 * 1024, rng))
    add("pdf", pdf)
    add(None, noise(48 * 1024, rng))
    add("txt", txt)
    add(None, noise(64 * 1024, rng))

    image = b"".join(parts)
    img_path = os.path.join(HERE, "test_image.bin")
    with open(img_path, "wb") as fh:
        fh.write(image)
    print(f"[gen] wrote {img_path} ({len(image)} bytes)")
    for k in ("jpg", "pdf", "txt"):
        print(f"[gen]   {k} @ offset {offsets[k]} (0x{offsets[k]:X}), "
              f"{len({'jpg': jpg, 'pdf': pdf, 'txt': txt}[k])} bytes")

    out_dir = tempfile.mkdtemp(prefix="carved_selftest_")
    # Use a tiny chunk size (1 MB min for int MB option) -- the image is
    # ~300 KB, so a single chunk covers it; still exercises the pipeline.
    cmd = [sys.executable, CARVER, img_path, "--out", out_dir, "-v"]
    print(f"[run] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print("[FAIL] carver exited with", proc.returncode)
        return 1

    manifest = os.path.join(out_dir, "manifest.csv")
    with open(manifest, newline="") as fh:
        rows = list(csv.DictReader(fh))

    print("[check] manifest rows:")
    for r in rows:
        print("        ", r)

    errors = []
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    for t, blob in (("jpg", jpg), ("pdf", pdf), ("txt", txt)):
        got = by_type.get(t, [])
        if len(got) != 1:
            errors.append(f"expected exactly 1 {t}, got {len(got)}")
            continue
        r = got[0]
        start, end, size = (int(r["start_offset"]), int(r["end_offset"]),
                            int(r["size_bytes"]))
        if start != offsets[t]:
            errors.append(f"{t}: start {start} != expected {offsets[t]}")
        if size != len(blob) or end != offsets[t] + len(blob):
            errors.append(f"{t}: size/end mismatch "
                          f"(size={size}, expected={len(blob)})")
        carved = open(os.path.join(out_dir, r["filename"]), "rb").read()
        if carved != blob:
            errors.append(f"{t}: carved bytes != original payload")
        if r["validation"] not in ("OK",) and "PIL-OK" not in r["validation"]:
            errors.append(f"{t}: unexpected validation {r['validation']!r}")

    if errors:
        for e in errors:
            print("[FAIL]", e)
        return 1
    print("[PASS] all three payloads carved at correct offsets, bytes "
          "identical, manifest correct.")
    print(f"[info] self-test output dir: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
