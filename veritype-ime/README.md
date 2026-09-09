# VeriType

**VeriType** is a personal verbatim typing logger for Android, implemented as a
custom soft keyboard (IME). You install it on your **own** phone, select it as
your active keyboard, and every character you type **through it** is logged
locally on the device. A viewer app (the launcher activity) shows the log and
lets you export or delete it.


## Keyboard features (v1.1)

- **Styled keycaps** — top-lit gradient keycaps with rounded corners, 3dp row
  gaps, and a teal press-state that lights a key while your finger is down
  (state-list drawable: `res/drawable/key_background.xml`).
- **📋 Clipboard key** — a dedicated clipboard key on both the QWERTY and
  symbols layouts pastes the system clipboard's primary clip at the cursor.
  Pasted content is logged like typed text, prefixed with `[CLIP] ` so it is
  distinguishable in the log viewer.
- **😀 Emoji layout** — an 😀 key on the bottom row opens a 5-row emoji layout
  (smileys, gestures, nature, objects, food/activities); `ABC` returns to
  QWERTY. Emoji keys use `android:keyOutputText`, which the service commits
  and logs through `onText()`.

## What VeriType deliberately does NOT do

- **No network.** The app does not declare `android.permission.INTERNET`.
  Nothing is ever transmitted, synced, or uploaded. You can verify this in
  `AndroidManifest.xml`.
- **No AccessibilityService.** VeriType uses only the standard, user-visible
  `InputMethodService` API (`BIND_INPUT_METHOD`). It cannot read other apps'
  screens, notifications, or content.
- **No other apps' content.** It logs only keystrokes the user themselves types
  while VeriType is the active keyboard. Text typed through any other keyboard
  is invisible to it.
- **No stealth.** The app appears in the launcher, the keyboard is a visible
  keyboard like any other, and Android shows its standard system warning when
  you enable any third-party input method. That warning is part of the
  consent-bounded design.
- **No special-casing of sensitive fields.** Logging is uniform: every field
  is treated the same, and only the coarse input-type *class* label
  (`text` / `number` / `phone` / `datetime` / `none`) is recorded — never a
  password-specific interpretation. This is disclosed here and in the code
  comments; note that any IME inherently receives what its user types, which
  is exactly why Android shows the enable warning.

## What gets logged

Each log entry (Room entity `LogEntry`, table `log_entries`) contains:

| Field            | Meaning                                                        |
|------------------|----------------------------------------------------------------|
| `id`             | Auto-generated primary key                                     |
| `timestamp`      | Epoch milliseconds                                             |
| `isoDateTime`    | ISO-8601 local datetime with offset                            |
| `appPackage`     | Package of the app holding the focused field (from EditorInfo) |
| `fieldHint`      | The field's hint text, if the target app declared one          |
| `inputTypeClass` | Coarse input-type class label only                             |
| `text`           | The typed segment. `[DEL]` = backspace, `[ENTER]` = return, `[CLIP]` = clipboard paste     |

Segments are buffered in memory and flushed to the database on field
transitions, on Enter, when the keyboard is dismissed, and every 200
characters. All database writes run on the IO dispatcher and are wrapped in
try/catch so a database error can never crash the keyboard while typing.

## Build (Android Studio)

Requires Android Studio Koala/Jellyfish-era tooling (AGP 8.5.2, Gradle 8.7,
Kotlin 2.0.20, KSP 2.0.20-1.0.25). `compileSdk 34`, `targetSdk 34`,
`minSdk 26`.

1. **File → Open** and select the `veritype-ime` folder.
2. Let Gradle sync (it will resolve the Gradle 8.7 distribution declared in
   `gradle/wrapper/gradle-wrapper.properties` and all dependencies).
3. Connect your phone with USB debugging enabled, then **Run → Run 'app'**
   (or `./gradlew :app:installDebug`).

## Enabling the keyboard on a Samsung Galaxy S25

1. Open **Settings → General management → Keyboard list and default**.
2. Enable **VeriType**. Android shows its standard third-party-keyboard
   warning — this is expected and is the user's explicit consent step.
3. Tap **Default keyboard** and select **VeriType** (or use the "Switch
   keyboard" button in the VeriType app to open the input-method picker).

The VeriType app also shows these instructions in a dialog on first launch,
with a shortcut button to the system input-method settings.

## Viewing, exporting, clearing

Open the **VeriType** launcher activity:

- The list shows every entry, newest first (timestamp, target app, field
  info, typed text).
- **Export TXT** / **Export JSON** use the Storage Access Framework
  (`ActivityResultContracts.CreateDocument`): you pick where the file is
  saved (e.g. Documents, Downloads, or any document provider). No storage
  permission is needed or requested.
- **Clear All** permanently deletes the whole log after a confirmation dialog.

## Privacy notes

- All data lives in the app's private Room database at
  `/data/data/com.veritype.ime/databases/veritype_log.db` — not readable by
  other apps on a non-rooted device.
- The app sets `android:allowBackup="false"`, so the log is not included in
  cloud/device backups.
- **Uninstalling the app wipes the database completely.**
- Install this only on your own device, and be aware that anyone who picks up
  your unlocked phone can open the VeriType app and read the log.

## Technical notes

- The keyboard UI uses the legacy
  `android.inputmethodservice.Keyboard` / `KeyboardView` classes. They are
  deprecated since API 29 but still fully functional on current Android
  versions, and they are by far the simplest self-contained way to render a
  keyboard without writing a custom `View`.
- Because it is a plain IME, VeriType works in every app that accepts text
  input — but it sees *only* what is typed while it is the active keyboard.
