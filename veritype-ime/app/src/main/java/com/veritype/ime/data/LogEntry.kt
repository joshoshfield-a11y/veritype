package com.veritype.ime.data

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * One verbatim log entry: a contiguous segment of text the user typed through
 * the VeriType keyboard into a specific app/field.
 */
@Entity(tableName = "log_entries")
data class LogEntry(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,

    /** Epoch milliseconds when the segment was flushed. */
    val timestamp: Long,

    /** ISO-8601 local datetime with offset, e.g. 2025-01-31T14:22:07.512+09:00 */
    val isoDateTime: String,

    /** Package name of the app that held the focused input field. */
    val appPackage: String,

    /** Hint text of the field, if the target app declared one (may be empty). */
    val fieldHint: String,

    /**
     * Input type CLASS label only: text / number / phone / datetime / none.
     * We deliberately record only the coarse class, never a special-cased
     * interpretation (e.g. no "password" handling) — logging is uniform.
     */
    val inputTypeClass: String,

    /** The typed text segment. "[DEL]" marks a backspace, "\n" marks enter. */
    val text: String
)
