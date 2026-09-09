package com.veritype.ime

import android.content.ClipboardManager
import android.content.Context
import android.inputmethodservice.InputMethodService
import android.inputmethodservice.Keyboard
import android.inputmethodservice.KeyboardView
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.inputmethod.EditorInfo
import com.veritype.ime.data.AppDatabase
import com.veritype.ime.data.LogEntry
import com.veritype.ime.data.LogRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * VeriType keyboard service.
 *
 * This is a personal, consent-bounded verbatim typing logger: the user installs
 * it on their OWN device and selects it as their active keyboard. Everything they
 * type THROUGH THIS KEYBOARD is logged to a local Room database. It cannot see
 * any content typed with other keyboards, and it has no network access.
 *
 * Notes on sensitive fields:
 *  - Secure password fields: an IME inherently receives what the user types —
 *    that is what a keyboard is for. Android also shows the user the standard
 *    system warning ("this input method may collect all the text you type")
 *    whenever ANY third-party IME is enabled, which is part of the informed-
 *    consent model here. We therefore keep logging completely uniform and do
 *    NOT exclude or special-case password/variation fields in any way. Only the
 *    coarse input-type CLASS label (text/number/phone/datetime/none) is stored.
 *
 * Robustness:
 *  - All Room writes happen on Dispatchers.IO and are wrapped in try/catch, so a
 *    database error can NEVER crash the keyboard while the user is typing.
 */
class VeriTypeIMEService : InputMethodService(), KeyboardView.OnKeyboardActionListener {

    private lateinit var keyboardView: KeyboardView
    private lateinit var qwertyKeyboard: Keyboard
    private lateinit var symbolsKeyboard: Keyboard
    private lateinit var emojiKeyboard: Keyboard

    /** Active layout id: one of [LAYOUT_QWERTY], [LAYOUT_SYMBOLS], [LAYOUT_EMOJI]. */
    private var activeLayout = LAYOUT_QWERTY
    private var isShifted = false

    private lateinit var repository: LogRepository

    /** Service-lifetime scope; DB writes always go out on the IO dispatcher. */
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** Typed characters accumulate here and are flushed to Room as one segment. */
    private val sessionBuffer = StringBuilder()

    /** Context of the currently focused field, captured in onStartInput. */
    private var currentPackageName: String = "unknown"
    private var currentFieldHint: String = ""
    private var currentInputClass: String = "none"

    /** ISO-8601 formatter. Only touched on the main thread (all IME callbacks). */
    private val isoFormatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)

    override fun onCreate() {
        super.onCreate()
        repository = LogRepository(AppDatabase.getInstance(this).logDao())
        qwertyKeyboard = Keyboard(this, R.xml.qwerty)
        symbolsKeyboard = Keyboard(this, R.xml.symbols)
        emojiKeyboard = Keyboard(this, R.xml.emoji)
    }

    override fun onCreateInputView(): View {
        keyboardView = LayoutInflater.from(this)
            .inflate(R.layout.keyboard_view, null) as KeyboardView
        keyboardView.keyboard = qwertyKeyboard
        keyboardView.setOnKeyboardActionListener(this)
        return keyboardView
    }

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        // Starting a new field while text is pending means a field transition —
        // flush what was typed into the previous field first.
        if (!restarting) {
            flushBuffer()
        }
        super.onStartInput(attribute, restarting)
        currentPackageName = attribute?.packageName ?: "unknown"
        currentFieldHint = attribute?.hintText?.toString() ?: ""
        currentInputClass = inputClassLabel(attribute?.inputType ?: 0)
    }

    override fun onFinishInput() {
        // Field transition / focus loss: persist whatever is pending.
        flushBuffer()
        super.onFinishInput()
    }

    override fun onDestroy() {
        flushBuffer()
        serviceScope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------------
    // KeyboardView.OnKeyboardActionListener
    // ------------------------------------------------------------------

    override fun onKey(primaryCode: Int, keyCodes: IntArray?) {
        val ic = currentInputConnection ?: return
        when (primaryCode) {
            Keyboard.KEYCODE_DELETE -> {
                ic.deleteSurroundingText(1, 0)
                sessionBuffer.append(DEL_MARKER)
            }

            Keyboard.KEYCODE_MODE_CHANGE -> switchLayout()

            // VeriType custom keycodes (see res/xml/*.xml header comments).
            KEYCODE_CLIPBOARD -> pasteFromClipboard()

            KEYCODE_EMOJI -> showLayout(LAYOUT_EMOJI)

            KEYCODE_BACK_TO_QWERTY -> showLayout(LAYOUT_QWERTY)

            Keyboard.KEYCODE_SHIFT -> {
                isShifted = !isShifted
                qwertyKeyboard.isShifted = isShifted
                keyboardView.invalidateAllKeys()
            }

            Keyboard.KEYCODE_DONE -> {
                val action = currentInputEditorInfo?.imeOptions?.and(EditorInfo.IME_MASK_ACTION)
                    ?: EditorInfo.IME_ACTION_NONE
                if (action != EditorInfo.IME_ACTION_NONE) {
                    // Field declared an action (search/send/go/...): trigger it.
                    ic.performEditorAction(action)
                } else {
                    ic.commitText("\n", 1)
                }
                sessionBuffer.append(ENTER_MARKER)
                flushBuffer()
            }

            else -> {
                var code = primaryCode
                if (isShifted && activeLayout == LAYOUT_QWERTY && code in 'a'.code..'z'.code) {
                    code = Character.toUpperCase(code)
                }
                val text = code.toChar().toString()
                // Commit directly on the InputConnection so this is NOT routed
                // back through our overridden commitText() (avoids double log).
                ic.commitText(text, 1)
                sessionBuffer.append(text)

                // One-shot shift: revert after a single capitalised character.
                if (isShifted) {
                    isShifted = false
                    qwertyKeyboard.isShifted = false
                    keyboardView.invalidateAllKeys()
                }

                if (sessionBuffer.length >= FLUSH_THRESHOLD) {
                    flushBuffer()
                }
            }
        }
    }

    /**
     * Text delivered through the OnKeyboardActionListener.onText path (e.g. a
     * key with android:keyOutputText instead of android:codes). Logged
     * uniformly. Note: onKey() commits directly on the InputConnection, so
     * normal key taps do not double-log through here.
     *
     * (InputMethodService has no commitText() to override; framework-side
     * commits go straight to the InputConnection. The listener's onText is
     * also abstract, so there is no super implementation to call.)
     */
    override fun onText(text: CharSequence?) {
        if (!text.isNullOrEmpty()) {
            // Keys with android:keyOutputText (the emoji layout) deliver here;
            // commit to the field ourselves and log uniformly.
            currentInputConnection?.commitText(text, 1)
            sessionBuffer.append(text)
        }
    }

    override fun onPress(primaryCode: Int) {}

    override fun onRelease(primaryCode: Int) {}

    override fun swipeLeft() {}

    override fun swipeRight() {}

    override fun swipeDown() {}

    override fun swipeUp() {}

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    private fun switchLayout() {
        showLayout(if (activeLayout == LAYOUT_QWERTY) LAYOUT_SYMBOLS else LAYOUT_QWERTY)
    }

    private fun showLayout(layout: Int) {
        activeLayout = layout
        keyboardView.keyboard = when (layout) {
            LAYOUT_SYMBOLS -> symbolsKeyboard
            LAYOUT_EMOJI -> emojiKeyboard
            else -> qwertyKeyboard
        }
        isShifted = false
        qwertyKeyboard.isShifted = false
        keyboardView.invalidateAllKeys()
    }

    /**
     * Paste the system clipboard's primary clip at the cursor. The pasted
     * content is logged like any other typed-through-keyboard text, prefixed
     * with a [CLIP] marker so it is distinguishable in the log viewer.
     */
    private fun pasteFromClipboard() {
        val ic = currentInputConnection ?: return
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = cm.primaryClip
        if (clip != null && clip.itemCount > 0) {
            val text = clip.getItemAt(0).coerceToText(this)?.toString()
            if (!text.isNullOrEmpty()) {
                ic.commitText(text, 1)
                sessionBuffer.append(CLIP_MARKER).append(text)
                if (sessionBuffer.length >= FLUSH_THRESHOLD) flushBuffer()
            }
        }
    }

    /**
     * Flush the pending typed segment to Room. Any failure is swallowed — the
     * keyboard must stay alive no matter what happens to the database.
     */
    private fun flushBuffer() {
        if (sessionBuffer.isEmpty()) return
        val segment = sessionBuffer.toString()
        sessionBuffer.setLength(0)

        val now = System.currentTimeMillis()
        val entry = LogEntry(
            timestamp = now,
            isoDateTime = isoFormatter.format(Date(now)),
            appPackage = currentPackageName,
            fieldHint = currentFieldHint,
            inputTypeClass = currentInputClass,
            text = segment
        )
        serviceScope.launch {
            try {
                repository.insert(entry)
            } catch (e: Exception) {
                // Deliberately ignored: a DB error must never crash the IME
                // while the user is typing.
            }
        }
    }

    /**
     * Coarse input-type CLASS label only. We intentionally never branch on
     * TYPE_TEXT_VARIATION_PASSWORD etc. — logging is uniform for every field.
     */
    private fun inputClassLabel(inputType: Int): String =
        when (inputType and InputType.TYPE_MASK_CLASS) {
            InputType.TYPE_CLASS_TEXT -> "text"
            InputType.TYPE_CLASS_NUMBER -> "number"
            InputType.TYPE_CLASS_PHONE -> "phone"
            InputType.TYPE_CLASS_DATETIME -> "datetime"
            else -> "none"
        }

    companion object {
        private const val DEL_MARKER = "[DEL]"
        private const val ENTER_MARKER = "[ENTER]\n"
        private const val CLIP_MARKER = "[CLIP] "
        private const val FLUSH_THRESHOLD = 200

        /** VeriType custom keycodes. */
        private const val KEYCODE_CLIPBOARD = -101
        private const val KEYCODE_EMOJI = -102
        private const val KEYCODE_BACK_TO_QWERTY = -103

        /** Layout ids for [activeLayout]. */
        private const val LAYOUT_QWERTY = 0
        private const val LAYOUT_SYMBOLS = 1
        private const val LAYOUT_EMOJI = 2
    }
}
