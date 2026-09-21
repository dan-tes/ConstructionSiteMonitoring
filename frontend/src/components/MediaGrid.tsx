import { FileText } from 'lucide-react'
import type { ProjectFile } from '../types/project'

/** A simple grid for viewing a batch of uploaded files — no per-file
 * analysis link any more, since analysis is combined per journal entry
 * (see JournalEntryCard, which links to the entry's own page instead). */
export function MediaGrid({ files }: { files: ProjectFile[] }) {
  if (files.length === 0) return null

  return (
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
      {files.map((file) => {
        if (file.kind === 'image') {
          return (
            <a
              key={file.id}
              href={file.url}
              target="_blank"
              rel="noreferrer"
              className="aspect-square overflow-hidden rounded-lg border border-site-700 bg-site-800"
            >
              <img src={file.url} alt={file.name} className="h-full w-full object-cover" />
            </a>
          )
        }
        if (file.kind === 'video') {
          return (
            <video
              key={file.id}
              src={file.url}
              controls
              className="aspect-square w-full rounded-lg border border-site-700 bg-black object-cover"
            />
          )
        }
        return (
          <a
            key={file.id}
            href={file.url}
            target="_blank"
            rel="noreferrer"
            className="flex aspect-square flex-col items-center justify-center gap-1.5 rounded-lg border border-site-700 bg-site-800 p-2 text-center"
          >
            <FileText className="h-6 w-6 text-site-400" />
            <span className="line-clamp-2 text-xs text-site-400">{file.name}</span>
          </a>
        )
      })}
    </div>
  )
}
