import { Fragment, type ReactNode } from 'react'

/**
 * Renders text that may contain `[label](url)` markdown links — the only
 * markup the backend's narrative reports (report.py, blocks 5/6) are ever
 * instructed to produce, each one pointing at a specific photo/video that
 * backs up a claim in the text (e.g. "no equipment visible in this photo").
 * Not a general markdown renderer: everything else is shown as plain text.
 */
export function LinkifiedText({ text, className }: { text: string; className?: string }) {
  const linkRe = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g
  const parts: ReactNode[] = []
  let lastIndex = 0
  let key = 0
  let match: RegExpExecArray | null
  while ((match = linkRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(<Fragment key={key++}>{text.slice(lastIndex, match.index)}</Fragment>)
    }
    parts.push(
      <a
        key={key++}
        href={match[2]}
        target="_blank"
        rel="noreferrer"
        className="text-safety-400 underline decoration-dotted underline-offset-2 hover:text-safety-300"
      >
        {match[1]}
      </a>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push(<Fragment key={key++}>{text.slice(lastIndex)}</Fragment>)
  }
  return <p className={className}>{parts}</p>
}
