import { FileText, Loader2, Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ProjectFile } from '../types/project'

export function MediaGrid({
  files,
  getVideoHref,
}: {
  files: ProjectFile[]
  getVideoHref?: (file: ProjectFile) => string
}) {
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
        // In the journal grid (getVideoHref set) every non-image item is a video,
        // even if the browser failed to report a video MIME type.
        if (file.kind === 'video' || getVideoHref) {
          if (getVideoHref) {
            const analyzing =
              file.insight?.status === 'analyzing' || file.insight?.status === 'pending'
            return (
              <Link
                key={file.id}
                to={getVideoHref(file)}
                className="group relative aspect-square overflow-hidden rounded-lg border border-site-700 bg-black transition hover:border-safety-400"
              >
                <video
                  src={file.url}
                  muted
                  preload="metadata"
                  className="pointer-events-none h-full w-full object-cover"
                />
                <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/30 transition group-hover:bg-black/20">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-safety-500 text-site-950">
                    <Play className="h-4 w-4 translate-x-0.5" fill="currentColor" />
                  </span>
                </span>
                {analyzing && (
                  <span className="pointer-events-none absolute inset-x-1 bottom-1 flex items-center justify-center gap-1 rounded-md bg-black/70 px-1.5 py-1 text-[11px] font-medium text-site-200">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Анализ
                  </span>
                )}
              </Link>
            )
          }
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
