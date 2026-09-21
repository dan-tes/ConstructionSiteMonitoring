import { AlertTriangle, ChevronRight, Loader2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { MediaGrid } from './MediaGrid'
import type { JournalEntry } from '../types/project'

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('ru-RU')
}

export function JournalEntryCard({
  entry,
  projectId,
}: {
  entry: JournalEntry
  projectId: string
}) {
  if (entry.media.length === 0) return null

  const status = entry.insight?.status
  const isAnalyzing = status === 'pending' || status === 'analyzing'
  const isFailed = status === 'failed'
  const isReady = status === 'ready'

  return (
    <div className="rounded-xl border border-site-700 bg-site-900/50 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-site-200">{formatDate(entry.date)}</p>
          <p className="text-xs text-site-500">{entry.author}</p>
          {entry.comment && <p className="mt-1 text-sm text-site-400">{entry.comment}</p>}
        </div>
        <Link
          to={`/projects/${projectId}/entries/${entry.id}`}
          className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-safety-400 transition hover:text-safety-300"
        >
          Анализ
          <ChevronRight className="h-4 w-4" />
        </Link>
      </div>

      {isAnalyzing && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-site-400">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Анализируется…
        </p>
      )}
      {isFailed && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-red-400">
          <AlertTriangle className="h-3.5 w-3.5" />
          Не удалось проанализировать
        </p>
      )}
      {isReady && entry.insight?.stageSummary && (
        <p className="mt-3 line-clamp-2 text-sm text-site-300">{entry.insight.stageSummary}</p>
      )}

      <MediaGrid files={entry.media} />
    </div>
  )
}
