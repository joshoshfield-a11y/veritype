# filecarver

A small, dependency-free file-carving utility for **legitimate personal
data recovery** on media **you own**. It scans a raw disk image for JPEG,
PDF, and plain-text signatures and extracts ("carves") the matching byte
ranges, writing a `manifest.csv` of everything found. This is the same
fundamental technique used by established open-source forensic tools such
as PhotoRec and Scalpel.

**Python 3, standard library only.** Pillow (PIL) is used
opportunistically to sanity-check carved JPEGs if installed, but is not
required.

---

## 1. Scope and ethics

Use this tool **only on media you own** — your own SD cards, USB sticks,
camera cards, and disk images you created from them. Carving recovers
deleted data at the raw byte level; running it against other people's
devices or accounts without authorization may be illegal and is outside
the scope of this project.

## 2. Step 1 — image your media

Carving runs against an **image file**, never against the live device.
Imaging is read-only and safe for the source media.

### Linux

Identify the device (be careful — picking the wrong device can destroy
data when writing; `dd` below only reads):

```bash
lsblk                      # find your SD card / USB stick, e.g. /dev/sdX
sudo dd if=/dev/sdX of=sdcard.img bs=4M status=progress conv=noerror,sync
```

For failing/aged media, `ddrescue` retries around bad sectors and can
resume interrupted runs:

```bash
sudo apt install gddrescue          # Debian/Ubuntu
sudo ddrescue -d /dev/sdX sdcard.img sdcard.map
```

### macOS

```bash
diskutil list                       # find the device, e.g. /dev/disk4
diskutil unmountDisk /dev/disk4
sudo dd if=/dev/disk4 of=sdcard.img bs=4m status=progress
```

(`gddrescue` is also available via Homebrew: `brew install ddrescue`.)

### Windows

`dd` exists for Windows but GUI imagers are friendlier and safer to
operate. Common free options:

- **FTK Imager** (free; "Create Disk Image" → raw/dd format)
- **OSFClone** (free, open source; writes raw dd images)
- **HDDSuperClone** / **USB Image Tool** for USB sticks

Produce a **raw / dd** image (not E01 or proprietary formats) so
`carver.py` can read it directly.

## 3. Step 2 — run the carver

```bash
python3 carver.py sdcard.img
# carves everything into ./carved/

python3 carver.py sdcard.img --out recovered --types jpg
# only JPEGs, into ./recovered/

python3 carver.py sdcard.img --types jpg,pdf --max-size 32 --chunk-size 16
# cap carved files at 32 MB, scan in 16 MB chunks

python3 carver.py sdcard.img --min-txt-len 1024 -v
# require 1 KB minimum for text runs, verbose logging
```

### Options

| Option          | Default     | Meaning                                        |
|-----------------|-------------|------------------------------------------------|
| `--out DIR`     | `./carved`  | Output directory                               |
| `--types`       | `jpg,pdf,txt` | Comma-separated list of types to carve       |
| `--max-size MB` | `64`        | Maximum size of any single carved file         |
| `--chunk-size MB` | `8`       | Scan chunk size (memory stays bounded)         |
| `--min-txt-len` | `512`       | Minimum printable-run length for TXT carving   |
| `--no-pil-check`| off         | Skip the PIL JPEG re-open sanity check         |
| `-v`            | off         | Debug logging                                  |

### Output

- `carved_0001.jpg`, `carved_0002.pdf`, … — the extracted files.
- `manifest.csv` — one row per carved file:

```csv
filename,type,start_offset,end_offset,size_bytes,validation
carved_0001.jpg,jpg,1048576,1164299,112724,OK
```

Offsets are absolute byte offsets in the image. The `validation` column
records sanity checks: JPEGs must start with `FF D8 FF` and end with
`FF D9` (and are re-opened with PIL if available); PDFs must match the
`%PDF-1.x` header regex. Entries flagged `BAD-EOI` or `PIL-FAIL` are
truncated or partially overwritten — some image viewers will still
display the recoverable portion.

## 4. Why some carved JPEGs won't open

Carving is a heuristic over raw bytes, not filesystem repair. Expect some
carved files to be damaged:

- **Fragmentation.** The carver assumes each file is stored contiguously
  between its header and footer. On heavily used FAT32/exFAT cards, a
  deleted file's clusters may be scattered; the carved output then
  contains the first fragment plus unrelated data. Fragment reassembly
  is out of scope (PhotoRec has the same limitation).
- **Partial overwrite.** Once a file is deleted, its blocks are free and
  new data (photos, app caches, filesystem metadata) reuses them. The
  header may survive while later blocks are gone — such JPEGs show the
  top of the picture and gray below.
- **Trailing slack.** Where a footer can't be found, carving stops at
  the next header or `--max-size`; the tail of the file is whatever
  happened to follow on disk. Harmless for most viewers, but flagged.
- **Embedded thumbnails.** JPEG EXIF thumbnails contain their own
  `FF D8 … FF D9` markers; a simple first-EOI carve can truncate at the
  thumbnail's EOI. Viewers usually still render the result.

Tip: stop using the card immediately once you realize files are deleted.
Every write reduces what carving can recover.

## 5. What this tool CANNOT do: modern Android internal storage

This matters for expectations. On a modern Samsung Galaxy (e.g. S25) or
any recent Android phone:

- **File-Based Encryption (FBE):** every file on internal storage is
  encrypted with a per-file key. Deleting a file wipes its key, so the
  deleted ciphertext is cryptographically unrecoverable — **carving
  cannot help, even with root access.** A raw dump of `/data` yields
  only ciphertext for deleted files.
- **Flash TRIM / discard:** the flash controller proactively erases
  blocks freed by deletion, often within minutes, physically destroying
  the data before any tool could read it.

For data deleted from **internal storage**, use the supported recovery
paths instead:

- **Samsung Gallery → Recycle bin** (30-day retention)
- **Google Photos → Trash** (60 days if backed up)
- **My Files → Recycle bin**
- **Samsung Cloud / Smart Switch backups**
- Google account backups (Drive, WhatsApp backups, etc.)

Carving **does** work on unencrypted media: SD cards set up as
*portable storage* (FAT32/exFAT), USB drives, camera cards, and images
of old unencrypted devices.

## 6. Self-test

`make_test_image.py` builds a synthetic image (random noise + one JPEG,
one PDF, one 2 KB text block) and runs the carver against it:

```bash
python3 make_test_image.py
```

It asserts that exactly one `.jpg`, one `.pdf`, and one `.txt` are
carved and that the manifest offsets match the embedded payloads.

## 7. Files

| File                | Purpose                                   |
|---------------------|-------------------------------------------|
| `carver.py`         | The carving utility (stdlib only)         |
| `make_test_image.py`| Synthetic-image generator + self-test     |
| `README.md`         | This guide                                |
