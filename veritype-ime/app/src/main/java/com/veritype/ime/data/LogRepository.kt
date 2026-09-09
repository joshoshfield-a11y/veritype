package com.veritype.ime.data

import kotlinx.coroutines.flow.Flow

/**
 * Thin repository over [LogDao]. Kept deliberately small — callers are
 * responsible for dispatching to the IO thread and for error handling
 * (the IME in particular must never crash because of a DB failure).
 */
class LogRepository(private val dao: LogDao) {

    val allEntries: Flow<List<LogEntry>> = dao.observeAll()

    suspend fun insert(entry: LogEntry) = dao.insert(entry)

    suspend fun getAllOnce(): List<LogEntry> = dao.getAllOnce()

    suspend fun deleteAll() = dao.deleteAll()
}
