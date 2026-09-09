#!/usr/bin/env python3
"""
carver.py -- a small, memory-safe file-carving utility for legitimate
personal data recovery on media YOU OWN.

Purpose
-------
You image your own media (e.g. an SD card from your own phone, a USB
stick) with `dd`/`ddrescue` (or a GUI imager on Windows), producing a
raw byte-for-byte image file. This tool scans that image for well-known
file signatures (JPEG, PDF, plain text) and extracts ("carves") the
matching byte ranges to an output directory, together with a
manifest.csv mapping each carved file to its byte offsets in the image.

This is the same fundamental technique used by established open-source
forensic tools such as PhotoRec and Scalpel.

IMPORTANT LIMITATIONS (read before relying on results)
------------------------------------------------------
1. Contiguous-carve assumption. The carver assumes each file is stored
   as one contiguous run of bytes between its header and footer
   signatures. It does NOT reassemble fragmented files. On heavily
   used media (especially FAT32/exFAT SD cards that have been written
   to repeatedly), files are often fragmented; carved output may be
   truncated or contain garbage from other deleted files.
2. Trailing slack. For formats where a reliable footer is unavailable
   (or the footer was overwritten), carving stops at the next header,
   at --max-size, or at EOF. Carved files may therefore contain
   trailing slack data from whatever occupied the following sectors.
   Validators (JPEG EOI check, PIL re-open) flag but cannot always
   repair this.
3. NOT for modern Android internal storage. Modern Android phones
   (e.g. Samsung Galaxy S25) use file-based encryption (FBE): every
   file's contents are encrypted with a per-file key that is wiped
   when the file is deleted. Deleted-file ciphertext is unrecoverable
   without that key -- even with root, even with carving. Flash
   storage TRIM/discard additionally zeroes discarded blocks. Carving
   works on UNENCRYPTED media: SD cards (FAT32/exFAT), USB drives,
   camera cards, and old unencrypted disk images.

Supported signatures
--------------------
JPEG : SOI marker FF D8 FF ... EOI marker FF D9
PDF  : %PDF- header ... %%EOF trailer (the LAST %%EOF within a
       configurable max window is used, because linearized/updated PDFs
       may contain multiple %%EOF markers)
TXT  : heuristic -- contiguous runs of printable ASCII/UTF-8 of at
       least --min-txt-len bytes (default 512, configurable)

Usage
-----
    python3 carver.py IMAGE [--out DIR] [--types jpg,pdf,txt]
                      [--max-size MB] [--chunk-size MB]
                      [--min-txt-len BYTES] [--no-pil-check]

Stdlib only. PIL/Pillow is used opportunistically (import-guarded) to
sanity-check carved JPEGs when available; it is not required.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys

log = logging.getLogger("carver")

# ---------------------------------------------------------------------------
# Signature constants
# ---------------------------------------------------------------------------

JPEG_SOI = b"\xff\xd8\xff"          # Start Of Image (followed by marker byte)
JPEG_EOI = b"\xff\xd9"              # End Of Image
PDF_HEADER = b"%PDF-"               # PDF magic
PDF_HEADER_RE = re.compile(rb"%PDF-1\.\d")   # strict header validation
PDF_EOF = b"%%EOF"                  # trailer marker

# Bytes treated as "printable" for the TXT heuristic: printable ASCII,
# tab/newline/CR, plus the >=0x80 range is validated as UTF-8 later.
# We accept bytes >= 0x80 provisionally so multi-byte UTF-8 text is not
# split; the run is finally validated by attempting UTF-8 decode.
_TXT_PRINTABLE = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
_TXT_HI = set(range(0x80, 0x100))  # candidate UTF-8 continuation/lead bytes


# ---------------------------------------------------------------------------
# Streaming scanner
# ---------------------------------------------------------------------------

class ChunkedReader:
    """Streams a binary image in overlapping chunks.

    Overlap guarantees that a signature straddling a chunk boundary is
    still seen whole. Offsets returned to callers are absolute byte
    offsets within the image file. Memory use is bounded by chunk size.
    """

    def __init__(self, path: str, chunk_size: int, overlap: int):
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk size")
        self.path = path
        self.chunk_size = chunk_size
        self.overlap = overlap

    def __iter__(self):
        """Yield (absolute_offset, bytes) for each chunk."""
        with open(self.path, "rb") as fh:
            base = 0
            while True:
                fh.seek(base)
                data = fh.read(self.chunk_size)
                if not data:
                    break
                yield base, data
                if len(data) < self.chunk_size:
                    break
                base += self.chunk_size - self.overlap


def find_all(haystack: bytes, needle: bytes, start: int = 0):
    """Yield all start indices of *needle* in *haystack*."""
    idx = haystack.find(needle, start)
    while idx != -1:
        yield idx
        idx = haystack.find(needle, idx + 1)


# ---------------------------------------------------------------------------
# Carvers
# ---------------------------------------------------------------------------

def carve_jpegs(data: bytes, base: int, max_size: int, max_scan: int):
    """Yield (start, end) absolute ranges for JPEG SOI..EOI pairs.

    For each SOI, the first EOI after it closes the file. This is the
    standard simple strategy; JPEGs with embedded thumbnails containing
    their own FFD8/FFD9 pairs are handled because we search the EOI
    *after* the SOI and stop at the first one (embedded thumbnails are
    small and their EOI precedes the outer EOI -- but since we take the
    FIRST EOI, thumbnails can truncate the carve; this is a known,
    documented limitation of simple carving, acceptable for recovery
    where some truncation is recoverable by image viewers).
    """
    pos = 0
    for soi in find_all(data, JPEG_SOI):
        if soi < pos:
            continue  # inside a file we already emitted
        scan_limit = min(len(data), soi + max_scan)
        eoi = data.find(JPEG_EOI, soi + len(JPEG_SOI), scan_limit)
        if eoi == -1:
            end = min(soi + max_size, len(data))
            log.warning(
                "JPEG at offset 0x%X: no EOI within window; carving %d bytes "
                "to max-size/EOF (likely truncated or footer overwritten)",
                base + soi, end - soi)
        else:
            end = eoi + len(JPEG_EOI)
        if end - soi > max_size:
            end = soi + max_size
            log.warning("JPEG at offset 0x%X exceeded --max-size; truncated",
                        base + soi)
        pos = end
        yield base + soi, base + end


def carve_pdfs(data: bytes, base: int, max_size: int, max_scan: int):
    """Yield (start, end) absolute ranges for %PDF-...%%EOF pairs.

    Uses the LAST %%EOF within the max window: PDFs that have been
    incrementally updated contain several %%EOF markers, and the final
    one terminates the complete document.
    """
    pos = 0
    for hdr in find_all(data, PDF_HEADER):
        if hdr < pos:
            continue
        if not PDF_HEADER_RE.match(data, hdr):
            continue  # false positive: '%PDF-' without a 1.x version
        window_end = min(len(data), hdr + max_scan)
        last_eof = -1
        for m in find_all(data, PDF_EOF, hdr + len(PDF_HEADER)):
            if m >= window_end:
                break
            last_eof = m
        if last_eof == -1:
            end = min(hdr + max_size, len(data))
            log.warning(
                "PDF at offset 0x%X: no %%%%EOF within window; carving %d "
                "bytes (footer may be overwritten)", base + hdr, end - hdr)
        else:
            end = last_eof + len(PDF_EOF)
            # Many writers append a newline after %%EOF; include it.
            if data[end:end + 2] == b"\r\n":
                end += 2
            elif data[end:end + 1] in (b"\n", b"\r"):
                end += 1
        if end - hdr > max_size:
            end = hdr + max_size
            log.warning("PDF at offset 0x%X exceeded --max-size; truncated",
                        base + hdr)
        pos = end
        yield base + hdr, base + end


def _decode_run(buf: bytes) -> bool:
    """Return True if *buf* is valid as (predominantly) printable text.

    Validates UTF-8 strictly; falls back to accepting it only if every
    byte is printable ASCII. High bytes that do not form valid UTF-8
    disqualify the run (it is binary data, not text).
    """
    if any(b >= 0x80 for b in buf):
        try:
            buf.decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def carve_txt(data: bytes, base: int, min_len: int, max_size: int):
    """Yield (start, end) absolute ranges of printable text runs.

    A run is a maximal sequence of printable-ASCII / whitespace /
    potential-UTF-8 bytes. Runs shorter than *min_len* are ignored.
    Runs containing invalid high bytes (not valid UTF-8) are rejected.
    Long runs are capped at *max_size*.
    """
    run_start = None
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b in _TXT_PRINTABLE or b in _TXT_HI:
            if run_start is None:
                run_start = i
            i += 1
            continue
        if run_start is not None:
            yield from _emit_txt_run(data, run_start, i, base, min_len,
                                     max_size)
            run_start = None
        i += 1
    if run_start is not None:
        yield from _emit_txt_run(data, run_start, n, base, min_len, max_size)


def _emit_txt_run(data, run_start, run_end, base, min_len, max_size):
    """Validate and yield one text run (helper for carve_txt)."""
    buf = data[run_start:run_end]
    if len(buf) < min_len:
        return
    if not _decode_run(buf):
        return
    if len(buf) > max_size:
        run_end = run_start + max_size
        log.warning("TXT run at offset 0x%X exceeded --max-size; truncated",
                    base + run_start)
    yield base + run_start, base + run_end


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_jpeg(path: str, use_pil: bool) -> str:
    """Return a validation note for a carved JPEG."""
    notes = []
    with open(path, "rb") as fh:
        head = fh.read(3)
        fh.seek(-2, os.SEEK_END)
        tail = fh.read(2)
    if head != JPEG_SOI:
        notes.append("BAD-SOI")
    if tail != JPEG_EOI:
        notes.append("BAD-EOI(missing FF D9)")
    if use_pil:
        try:
            from PIL import Image  # noqa: PLC0415 (guarded optional import)
            try:
                with Image.open(path) as im:
                    im.verify()
                notes.append("PIL-OK")
            except Exception as exc:  # truncated/corrupt image
                notes.append(f"PIL-FAIL({type(exc).__name__})")
        except ImportError:
            pass
    return ";".join(notes) if notes else "OK"


def validate_pdf(path: str) -> str:
    """Return a validation note for a carved PDF."""
    with open(path, "rb") as fh:
        head = fh.read(1024)
    if not PDF_HEADER_RE.search(head):
        return "BAD-HEADER"
    return "OK"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def ranges_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def carve_image(args) -> list[dict]:
    """Scan the image, carve matches, write manifest. Returns manifest rows."""
    chunk_size = args.chunk_size * 1024 * 1024
    max_size = args.max_size * 1024 * 1024
    # Overlap must cover the longest signature-plus-footer we might see
    # straddling a boundary. max_size as overlap would be huge, so we
    # use a pragmatic overlap: signatures/footers themselves are tiny;
    # a match STARTING near the end of a chunk is re-seen in the next
    # chunk because dedup by start offset (seen set) handles repeats,
    # and a footer beyond the chunk end is found because carving below
    # reads the file directly from disk, not from the chunk.
    overlap = 4096

    types = set(args.types)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    # Matches are found on chunk boundaries; actual byte extraction is
    # done with direct seeks, so matches larger than the chunk are fine.
    # To find header/footer pairs spanning chunks we scan with a window:
    # headers found in the overlap zone are skipped (already processed),
    # and the footer search window extends beyond the chunk by reading
    # additional bytes from disk.
    seen_starts = set()
    matches = []  # (type, abs_start, abs_end)

    img_size = os.path.getsize(args.image)
    log.info("Image: %s (%d bytes, %.1f MiB)", args.image, img_size,
             img_size / 1024 / 1024)
    log.info("Types: %s | chunk=%d MiB | max-size=%d MiB | out=%s",
             ",".join(sorted(types)), args.chunk_size, args.max_size, out_dir)

    max_scan = max_size  # footer search window

    with open(args.image, "rb") as fh:
        reader = ChunkedReader(args.image, chunk_size, overlap)
        for base, chunk in reader:
            # Extend the chunk with up to max_scan extra bytes so a
            # header near the chunk end can find its footer. This is a
            # direct disk read; memory stays bounded because the extra
            # read is capped by max_scan.
            extra_len = min(max_scan, max(0, img_size - (base + len(chunk))))
            if extra_len:
                fh.seek(base + len(chunk))
                window = chunk + fh.read(extra_len)
            else:
                window = chunk

            # Only accept matches STARTING in the "fresh" part of the
            # chunk (after the overlap prefix) to avoid duplicates from
            # the overlap region.
            fresh_lo = overlap if base > 0 else 0

            def accept(found):
                """Filter matches to the fresh region; dedup; record."""
                for s, e in found:
                    rel = s - base
                    if rel < fresh_lo or rel >= len(chunk):
                        continue
                    if s in seen_starts:
                        continue
                    seen_starts.add(s)
                    matches.append((t, s, min(e, img_size)))

            if "jpg" in types:
                t = "jpg"
                accept(carve_jpegs(window, base, max_size, max_scan))
            if "pdf" in types:
                t = "pdf"
                accept(carve_pdfs(window, base, max_size, max_scan))
            if "txt" in types:
                t = "txt"
                accept(carve_txt(window, base, args.min_txt_len, max_size))

            log.debug("Scanned chunk @0x%X (%d bytes)", base, len(chunk))

    matches.sort(key=lambda m: m[1])

    # Drop TXT matches that are wholly inside a JPEG/PDF match (binary
    # file data often contains long printable stretches).
    binary_ranges = [(s, e) for t, s, e in matches if t in ("jpg", "pdf")]
    filtered = []
    for t, s, e in matches:
        if t == "txt" and any(s >= bs and e <= be for bs, be in binary_ranges):
            log.info("Skipping TXT run @0x%X: contained within a %s match",
                     s, "binary")
            continue
        filtered.append((t, s, e))

    rows = []
    counters = {}
    for t, s, e in filtered:
        counters[t] = counters.get(t, 0) + 1
        name = f"carved_{sum(counters.values()):04d}.{t}"
        out_path = os.path.join(out_dir, name)
        size = e - s
        with open(args.image, "rb") as fh, open(out_path, "wb") as out:
            fh.seek(s)
            remaining = size
            while remaining:
                blk = fh.read(min(1024 * 1024, remaining))
                if not blk:
                    break
                out.write(blk)
                remaining -= len(blk)
        log.info("Carved %-3s 0x%010X-0x%010X (%d bytes) -> %s",
                 t, s, e - 1, size, name)
        rows.append({
            "filename": name,
            "type": t,
            "start_offset": s,
            "end_offset": e,
            "size_bytes": size,
            "validation": "",
        })

    # Validation pass (after extraction, on the carved files).
    use_pil = not args.no_pil_check
    for row in rows:
        p = os.path.join(out_dir, row["filename"])
        if row["type"] == "jpg":
            row["validation"] = validate_jpeg(p, use_pil)
        elif row["type"] == "pdf":
            row["validation"] = validate_pdf(p)
        else:
            row["validation"] = "OK"
        if row["validation"] != "OK":
            log.warning("Validation %s: %s", row["filename"],
                        row["validation"])

    manifest_path = os.path.join(out_dir, "manifest.csv")
    with open(manifest_path, "w", newline="") as mf:
        writer = csv.DictWriter(mf, fieldnames=[
            "filename", "type", "start_offset", "end_offset",
            "size_bytes", "validation"])
        writer.writeheader()
        writer.writerows(rows)
    log.info("Manifest written: %s (%d entries)", manifest_path, len(rows))
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Carve JPEG/PDF/TXT files from a raw disk image you own.",
        epilog="See the module docstring / README.md for scope and limits.")
    p.add_argument("image", help="Path to the raw image file (e.g. sdcard.img)")
    p.add_argument("--out", default="./carved",
                   help="Output directory (default: ./carved)")
    p.add_argument("--types", default="jpg,pdf,txt",
                   type=lambda s: [x.strip().lower() for x in s.split(",")],
                   help="Comma-separated types to carve: jpg,pdf,txt "
                        "(default: all)")
    p.add_argument("--max-size", type=int, default=64, metavar="MB",
                   help="Maximum size per carved file in MB (default: 64)")
    p.add_argument("--chunk-size", type=int, default=8, metavar="MB",
                   help="Scan chunk size in MB (default: 8)")
    p.add_argument("--min-txt-len", type=int, default=512, metavar="BYTES",
                   help="Minimum length of a printable run to carve as TXT "
                        "(default: 512)")
    p.add_argument("--no-pil-check", action="store_true",
                   help="Skip PIL/Pillow JPEG re-open sanity check")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable debug logging")
    args = p.parse_args(argv)
    valid = {"jpg", "pdf", "txt"}
    bad = set(args.types) - valid
    if bad:
        p.error(f"unknown type(s): {', '.join(sorted(bad))} "
                f"(valid: {', '.join(sorted(valid))})")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s")
    if not os.path.isfile(args.image):
        log.error("Image not found: %s", args.image)
        return 2
    rows = carve_image(args)
    by_type = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    log.info("---- Summary ----")
    if rows:
        for t in sorted(by_type):
            log.info("  %-4s: %d file(s)", t, by_type[t])
        bad = [r["filename"] for r in rows if r["validation"] != "OK"
               and "PIL-OK" not in r["validation"]]
        if bad:
            log.info("  %d file(s) failed validation: %s", len(bad),
                     ", ".join(bad))
    else:
        log.info("  No matches found.")
    log.info("Done. Output: %s", os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
