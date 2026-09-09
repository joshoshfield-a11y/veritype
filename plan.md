# Plan — VeriType Personal IME Logger + File Carver

## Scope (user-clarified)
- Personal-use only, user's own Galaxy S25, NO stealth, NO accessibility hooks, NO screen/tap capture.
- Deliverable A: Android custom IME (InputMethodService) that logs verbatim everything the user types through it (timestamp + target app package + text), local-only storage, visible log viewer + export. No INTERNET permission.
- Deliverable B: file-carving utility (JPEG/PDF/TXT magic-byte carving) for the user's own disk/card images on a PC, with a guide covering FBE limits on S25 internal storage.

## Stage 1 — Skill load
- Load `vibecoding-general-swarm` (mandatory for non-web coding). Apply proportionally: single-project build, one coder subagent; no worktree machinery needed (no shared repo).

## Stage 2 — Parallel builds (two coder subagents, independent)
- Agent A: complete Android Studio project at /mnt/agents/output/veritype-ime/ (Kotlin, AGP 8.x, compileSdk 34, minSdk 26, Room logging, export via SAF, README with build steps).
- Agent B: /mnt/agents/output/filecarver/carver.py + README.md; self-test against synthetic image with embedded JPEG/PDF signatures.

## Stage 3 — Verify & integrate
- Check all files exist and are non-empty; check subagent self-test results.
- Final response: usage notes, honest limits (password fields behavior, FBE on internal storage), REF tags per deliverable.

## Explicitly out of scope
- No GitHub push (user's PAT is compromised; user advised to revoke).
- No APK compilation (no Android SDK in sandbox; user compiles in Android Studio).
- No AccessibilityService-based capture of any kind.
