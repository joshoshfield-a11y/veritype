package com.veritype.ime

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.veritype.ime.data.LogEntry

class LogAdapter : ListAdapter<LogEntry, LogAdapter.LogViewHolder>(DIFF) {

    inner class LogViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val timestamp: TextView = itemView.findViewById(R.id.tv_timestamp)
        private val meta: TextView = itemView.findViewById(R.id.tv_meta)
        private val text: TextView = itemView.findViewById(R.id.tv_text)

        fun bind(entry: LogEntry) {
            timestamp.text = entry.isoDateTime
            meta.text = itemView.context.getString(
                R.string.log_meta_format,
                entry.appPackage,
                entry.inputTypeClass,
                entry.fieldHint.ifEmpty { "–" }
            )
            text.text = entry.text
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): LogViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_log, parent, false)
        return LogViewHolder(view)
    }

    override fun onBindViewHolder(holder: LogViewHolder, position: Int) {
        holder.bind(getItem(position))
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<LogEntry>() {
            override fun areItemsTheSame(oldItem: LogEntry, newItem: LogEntry): Boolean =
                oldItem.id == newItem.id

            override fun areContentsTheSame(oldItem: LogEntry, newItem: LogEntry): Boolean =
                oldItem == newItem
        }
    }
}
