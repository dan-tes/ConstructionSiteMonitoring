import { Calendar } from 'lucide-react'
import { MediaGrid } from './MediaGrid'
import { formatDateOnly } from '../lib/date'
import type { JournalEntry } from '../types/project'

export function JournalEntryCard({ entry }: { entry: JournalEntry }) {
  return (
    <div className="rounded-xl border border-site-700 bg-site-900/50 p-4">
      <div className="flex items-center gap-2 text-sm text-site-300">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-site-800 text-safety-400">
          <Calendar className="h-3.5 w-3.5" />
        </span>
        <span className="font-medium text-site-200">{formatDateOnly(entry.date)}</span>
      </div>
      {entry.comment && <p className="mt-3 text-sm text-site-200">{entry.comment}</p>}
      <MediaGrid files={entry.media} />
    </div>
  )
}
