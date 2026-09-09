package com.veritype.ime.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface LogDao {

    @Insert
    suspend fun insert(entry: LogEntry)

    /** Live stream of all entries, newest first, for the viewer UI. */
    @Query("SELECT * FROM log_entries ORDER BY timestamp DESC, id DESC")
    fun observeAll(): Flow<List<LogEntry>>

    /** One-shot snapshot, oldest first, used for TXT/JSON export. */
    @Query("SELECT * FROM log_entries ORDER BY timestamp ASC, id ASC")
    suspend fun getAllOnce(): List<LogEntry>

    @Query("DELETE FROM log_entries")
    suspend fun deleteAll()
}
