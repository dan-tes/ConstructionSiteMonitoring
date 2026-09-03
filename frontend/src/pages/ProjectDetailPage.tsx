import { ArrowLeft, Download, FileText, HardHat, Image as ImageIcon, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { AiStageAnalysis } from '../components/AiStageAnalysis'
import { FileDropZone } from '../components/FileDropZone'
import { JournalEntryCard } from '../components/JournalEntryCard'
import { EditableText } from '../components/EditableText'
import { Field, PrimaryButton, TextInput } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useProjects } from '../context/ProjectsContext'
import { todayDateString } from '../lib/date'
import { formatFileSize } from '../lib/files'

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { user } = useAuth()
  const { getProject, updateProject, setProjectPlan, addJournalEntry } = useProjects()
  const project = id ? getProject(id) : undefined

  const [comment, setComment] = useState('')
  const [entryDate, setEntryDate] = useState(todayDateString)
  const [mediaFiles, setMediaFiles] = useState<File[]>([])

  if (!project) {
    return <Navigate to="/projects" replace />
  }

  const handleAddMedia = (files: File[]) => {
    setMediaFiles((prev) => [...prev, ...files])
  }

  const handleSubmitEntry = (e: React.FormEvent) => {
    e.preventDefault()
    if (!comment.trim() && mediaFiles.length === 0) return
    addJournalEntry(project.id, comment, mediaFiles, entryDate, user?.username ?? 'Неизвестно')
    setComment('')
    setEntryDate(todayDateString())
    setMediaFiles([])
  }

  const hasMedia = project.entries.some((entry) => entry.media.length > 0)

  return (
    <div className="min-h-svh">
      <header className="border-b border-site-800 bg-site-900/60 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-5 py-4">
          <Link to="/projects" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-safety-500 text-site-950">
              <HardHat className="h-5 w-5" strokeWidth={2.25} />
            </span>
            <span className="font-display text-lg font-bold tracking-tight text-site-100">
              СтройМонитор
            </span>
          </Link>
          <span className="hidden text-sm text-site-400 sm:inline">
            Прораб <span className="font-medium text-site-200">{user?.username}</span>
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-5 py-10">
        <Link
          to="/projects"
          className="inline-flex items-center gap-1.5 text-sm text-site-400 transition hover:text-safety-400"
        >
          <ArrowLeft className="h-4 w-4" />
          Все объекты
        </Link>

        <div className="mt-4">
          <EditableText
            value={project.name}
            as="h1"
            className="font-display text-2xl font-bold text-site-100 sm:text-3xl"
            onSave={(name) => updateProject(project.id, { name })}
          />
          <EditableText
            value={project.description}
            as="p"
            multiline
            placeholder="Добавить описание объекта"
            className="mt-2 max-w-2xl text-site-400"
            onSave={(description) => updateProject(project.id, { description })}
          />
        </div>

        <div className="hazard-stripes mt-6 h-1 w-full rounded-full opacity-70" />

        <section className="mt-8">
          <h2 className="font-display text-lg font-semibold text-site-100">План объекта</h2>
          <div className="mt-3">
            {project.plan ? (
              <div className="flex items-center justify-between gap-3 rounded-xl border border-site-700 bg-site-900/50 p-4">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-site-800 text-safety-400">
                    {project.plan.kind === 'image' ? (
                      <ImageIcon className="h-5 w-5" />
                    ) : (
                      <FileText className="h-5 w-5" />
                    )}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-site-100">{project.plan.name}</p>
                    <p className="text-xs text-site-500">{formatFileSize(project.plan.size)}</p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <a
                    href={project.plan.url}
                    download={project.plan.name}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-site-600 px-3 py-2 text-sm font-medium text-site-200 transition hover:border-safety-400 hover:text-safety-300"
                  >
                    <Download className="h-4 w-4" />
                    Скачать
                  </a>
                  <button
                    type="button"
                    onClick={() => setProjectPlan(project.id, null)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-site-600 px-3 py-2 text-sm font-medium text-site-300 transition hover:border-red-500/50 hover:text-red-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ) : (
              <FileDropZone
                label="Загрузить план объекта"
                hint="PDF или изображение"
                accept="application/pdf,image/*"
                onFiles={(files) => setProjectPlan(project.id, files[0])}
              />
            )}
          </div>
        </section>

        <div className="mt-8">
          <AiStageAnalysis hasMedia={hasMedia} />
        </div>

        <section className="mt-10">
          <h2 className="font-display text-lg font-semibold text-site-100">Журнал объекта</h2>

          <form
            onSubmit={handleSubmitEntry}
            className="mt-3 flex flex-col gap-3 rounded-xl border border-site-700 bg-site-900/50 p-4"
          >
            <Field label="Дата записи" htmlFor="entry-date">
              <TextInput
                id="entry-date"
                type="date"
                required
                max={todayDateString()}
                value={entryDate}
                onChange={(e) => setEntryDate(e.target.value)}
                className="w-auto"
              />
            </Field>

            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              placeholder="Что произошло на объекте? Комментарий к записи…"
              className="w-full rounded-lg border border-site-600 bg-site-800/80 px-3.5 py-2.5 text-site-100 outline-none placeholder:text-site-500 transition focus:border-safety-400 focus:ring-2 focus:ring-safety-400/30"
            />

            <FileDropZone
              label="Прикрепить фото или видео"
              hint="Можно выбрать несколько файлов"
              accept="image/*,video/*"
              multiple
              onFiles={handleAddMedia}
            />

            {mediaFiles.length > 0 && (
              <ul className="flex flex-wrap gap-2">
                {mediaFiles.map((file, index) => (
                  <li
                    key={`${file.name}-${index}`}
                    className="flex items-center gap-2 rounded-lg border border-site-600 bg-site-800 px-2.5 py-1.5 text-xs text-site-300"
                  >
                    {file.name}
                    <button
                      type="button"
                      onClick={() => setMediaFiles((prev) => prev.filter((_, i) => i !== index))}
                      className="text-site-500 hover:text-red-400"
                      aria-label={`Убрать ${file.name}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex justify-end">
              <PrimaryButton type="submit" disabled={!comment.trim() && mediaFiles.length === 0}>
                Добавить запись
              </PrimaryButton>
            </div>
          </form>

          <div className="mt-6 flex flex-col gap-3">
            {project.entries.length === 0 ? (
              <p className="rounded-xl border border-dashed border-site-700 bg-site-900/30 px-4 py-8 text-center text-sm text-site-500">
                Записей пока нет — добавьте первую выше
              </p>
            ) : (
              project.entries.map((entry) => <JournalEntryCard key={entry.id} entry={entry} />)
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
