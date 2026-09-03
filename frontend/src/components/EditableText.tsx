import { Check, Pencil, X } from 'lucide-react'
import { useState } from 'react'

export function EditableText({
  value,
  onSave,
  multiline,
  placeholder,
  as: As = 'p',
  className = '',
}: {
  value: string
  onSave: (next: string) => void
  multiline?: boolean
  placeholder?: string
  as?: 'p' | 'h1' | 'h2'
  className?: string
}) {
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(value)

  const startEditing = () => {
    setDraft(value)
    setIsEditing(true)
  }

  const save = () => {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== value) onSave(trimmed)
    setIsEditing(false)
  }

  const cancel = () => setIsEditing(false)

  if (isEditing) {
    const InputTag = multiline ? 'textarea' : 'input'
    return (
      <div className="flex items-start gap-2">
        <InputTag
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={multiline ? 3 : undefined}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !multiline) save()
            if (e.key === 'Escape') cancel()
          }}
          className="w-full rounded-lg border border-safety-400 bg-site-800 px-3 py-2 text-site-100 outline-none focus:ring-2 focus:ring-safety-400/30"
        />
        <button
          type="button"
          onClick={save}
          className="rounded-lg bg-safety-500 p-2 text-site-950 transition hover:bg-safety-400"
          aria-label="Сохранить"
        >
          <Check className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={cancel}
          className="rounded-lg border border-site-600 p-2 text-site-300 transition hover:text-site-100"
          aria-label="Отмена"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    )
  }

  return (
    <div className="group flex items-start gap-2">
      <As className={className || undefined}>
        {value || <span className="text-site-500">{placeholder}</span>}
      </As>
      <button
        type="button"
        onClick={startEditing}
        className="mt-1 rounded-md p-1 text-site-500 opacity-0 transition group-hover:opacity-100 hover:text-safety-400"
        aria-label="Редактировать"
      >
        <Pencil className="h-4 w-4" />
      </button>
    </div>
  )
}
