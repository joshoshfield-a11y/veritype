package com.veritype.ime

import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.veritype.ime.data.AppDatabase
import com.veritype.ime.data.LogEntry
import com.veritype.ime.data.LogRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Log viewer / manager. Shows every logged entry (newest first) and offers:
 *  - Export TXT / Export JSON via the Storage Access Framework (CreateDocument)
 *  - Clear All (with confirmation)
 *  - Shortcuts to the system IME settings and the input-method picker
 *  - First-run enable instructions
 */
class MainActivity : AppCompatActivity() {

    private enum class ExportFormat { TXT, JSON }

    private lateinit var repository: LogRepository
    private lateinit var adapter: LogAdapter

    private val exportTxtLauncher =
        registerForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
            uri?.let { exportLog(it, ExportFormat.TXT) }
        }

    private val exportJsonLauncher =
        registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
            uri?.let { exportLog(it, ExportFormat.JSON) }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        repository = LogRepository(AppDatabase.getInstance(this).logDao())

        adapter = LogAdapter()
        findViewById<RecyclerView>(R.id.log_recycler).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = this@MainActivity.adapter
        }

        findViewById<View>(R.id.btn_enable_ime).setOnClickListener {
            startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
        }
        findViewById<View>(R.id.btn_switch_keyboard).setOnClickListener {
            getSystemService(InputMethodManager::class.java)?.showInputMethodPicker()
        }
        findViewById<View>(R.id.btn_export_txt).setOnClickListener {
            exportTxtLauncher.launch("veritype-log.txt")
        }
        findViewById<View>(R.id.btn_export_json).setOnClickListener {
            exportJsonLauncher.launch("veritype-log.json")
        }
        findViewById<View>(R.id.btn_clear_all).setOnClickListener { confirmClearAll() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                repository.allEntries.collect { entries -> adapter.submitList(entries) }
            }
        }

        showInstructionsOnFirstRun()
    }

    // ------------------------------------------------------------------
    // Export (Storage Access Framework — no storage permission needed)
    // ------------------------------------------------------------------

    private fun exportLog(uri: Uri, format: ExportFormat) {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    val entries = repository.getAllOnce()
                    val content = when (format) {
                        ExportFormat.TXT -> buildTxt(entries)
                        ExportFormat.JSON -> buildJson(entries)
                    }
                    contentResolver.openOutputStream(uri)?.use { out ->
                        out.write(content.toByteArray(Charsets.UTF_8))
                    } ?: return@withContext null
                    entries.size
                } catch (e: Exception) {
                    null
                }
            }
            val message = if (result != null) {
                getString(R.string.export_success, result)
            } else {
                getString(R.string.export_failed)
            }
            Toast.makeText(this@MainActivity, message, Toast.LENGTH_LONG).show()
        }
    }

    private fun buildTxt(entries: List<LogEntry>): String {
        val sb = StringBuilder()
        sb.appendLine("VeriType verbatim typing log")
        sb.appendLine("Entries: ${entries.size}")
        sb.appendLine("------------------------------------------------------------")
        for (e in entries) {
            sb.appendLine(
                "[${e.isoDateTime}] app=${e.appPackage} " +
                    "type=${e.inputTypeClass} field=${e.fieldHint.ifEmpty { "-" }}"
            )
            sb.appendLine(e.text)
            sb.appendLine("------------------------------------------------------------")
        }
        return sb.toString()
    }

    private fun buildJson(entries: List<LogEntry>): String {
        val sb = StringBuilder()
        sb.append("[\n")
        entries.forEachIndexed { index, e ->
            sb.append("  {\n")
            sb.append("    \"id\": ${e.id},\n")
            sb.append("    \"timestamp\": ${e.timestamp},\n")
            sb.append("    \"isoDateTime\": \"${jsonEscape(e.isoDateTime)}\",\n")
            sb.append("    \"appPackage\": \"${jsonEscape(e.appPackage)}\",\n")
            sb.append("    \"fieldHint\": \"${jsonEscape(e.fieldHint)}\",\n")
            sb.append("    \"inputTypeClass\": \"${jsonEscape(e.inputTypeClass)}\",\n")
            sb.append("    \"text\": \"${jsonEscape(e.text)}\"\n")
            sb.append(if (index == entries.lastIndex) "  }\n" else "  },\n")
        }
        sb.append("]\n")
        return sb.toString()
    }

    private fun jsonEscape(s: String): String = buildString(s.length) {
        for (c in s) {
            when (c) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                '\b' -> append("\\b")
                else -> if (c < ' ') append("\\u%04x".format(c.code)) else append(c)
            }
        }
    }

    // ------------------------------------------------------------------
    // Clear all / first run
    // ------------------------------------------------------------------

    private fun confirmClearAll() {
        AlertDialog.Builder(this)
            .setTitle(R.string.clear_all_title)
            .setMessage(R.string.clear_all_message)
            .setPositiveButton(R.string.clear_all_confirm) { _, _ ->
                lifecycleScope.launch(Dispatchers.IO) {
                    try {
                        repository.deleteAll()
                    } catch (e: Exception) {
                        // Ignore — the live list will simply stay as-is.
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun showInstructionsOnFirstRun() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (prefs.getBoolean(KEY_INSTRUCTIONS_SHOWN, false)) return
        AlertDialog.Builder(this)
            .setTitle(R.string.first_run_title)
            .setMessage(R.string.first_run_message)
            .setPositiveButton(R.string.open_ime_settings) { _, _ ->
                startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
        prefs.edit().putBoolean(KEY_INSTRUCTIONS_SHOWN, true).apply()
    }

    companion object {
        private const val PREFS_NAME = "veritype_prefs"
        private const val KEY_INSTRUCTIONS_SHOWN = "instructions_shown"
    }
}
