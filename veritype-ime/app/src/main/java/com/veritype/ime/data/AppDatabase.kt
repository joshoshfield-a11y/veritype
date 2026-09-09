package com.veritype.ime.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * The entire log lives in this single on-device Room database:
 *   /data/data/com.veritype.ime/databases/veritype_log.db
 * Uninstalling the app wipes it completely.
 */
@Database(entities = [LogEntry::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {

    abstract fun logDao(): LogDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "veritype_log.db"
                ).build().also { INSTANCE = it }
            }
    }
}
