import { MediaGrid } from './MediaGrid'
import type { JournalEntry } from '../types/project'

export function JournalEntryCard({
  entry,
  projectId,
}: {
  entry: JournalEntry
  projectId: string
}) {
  if (entry.media.length === 0) return null

  return (
    <div className="rounded-xl border border-site-700 bg-site-900/50 p-4">
      <MediaGrid
        files={entry.media}
        getVideoHref={(file) => `/projects/${projectId}/videos/${file.id}`}
      />
    </div>
  )
}
