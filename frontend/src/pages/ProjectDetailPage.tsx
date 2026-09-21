import { ArrowLeft, Download, FileSpreadsheet, HardHat, Table2, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { FileDropZone } from '../components/FileDropZone'
import { JournalEntryCard } from '../components/JournalEntryCard'
import { EditableText } from '../components/EditableText'
import { PrimaryButton } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useProjects } from '../context/ProjectsContext'
import { todayDateString } from '../lib/date'
import { formatFileSize } from '../lib/files'

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { user } = useAuth()
  const {
    getProject,
    refreshProject,
    updateProject,
    setProjectPlan,
    downloadCanonicalPlan,
    addJournalEntry,
  } = useProjects()
  const project = id ? getProject(id) : undefined

  const [mediaFiles, setMediaFiles] = useState<File[]>([])
  const [entryDate, setEntryDate] = useState(todayDateString())
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [entryError, setEntryError] = useState<string | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  const planInsightStatus = project?.plan?.insight?.status

  useEffect(() => {
    if (!id) return
    refreshProject(id).catch(() => setNotFound(true))
  }, [id, refreshProject])

  // Most uploaded plans aren't already in our canonical shape, so upload_plan
  // hands them to an LLM to re-map onto our 10 canonical phases — poll while
  // that's in flight (mirrors EntryDetailPage's analysis polling).
  useEffect(() => {
    if (!id || !planInsightStatus) return
    if (planInsightStatus === 'ready' || planInsightStatus === 'failed') return
    const timer = setInterval(() => {
      void refreshProject(id).catch(() => {})
    }, 4000)
    return () => clearInterval(timer)
  }, [id, planInsightStatus, refreshProject])

  if (notFound) {
    return <Navigate to="/projects" replace />
  }

  if (!project) {
    return (
      <div className="flex min-h-svh items-center justify-center text-site-400">
        Загрузка объекта…
      </div>
    )
  }

  const entriesWithMedia = project.entries.filter((entry) => entry.media.length > 0)
  const videos = entriesWithMedia.flatMap((entry) => entry.media)
  const isAnalyzing = videos.length > 0 && !project.planStatus

  const changePlan = (file: File | null) => {
    setPlanError(null)
    void setProjectPlan(project.id, file).catch((err) =>
      setPlanError(err instanceof Error ? err.message : 'Не удалось загрузить план'),
    )
  }

  const handleDownloadCanonicalPlan = async () => {
    setPlanError(null)
    try {
      const blob = await downloadCanonicalPlan(project.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${project.name || 'plan'}_canonical.xlsx`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : 'Не удалось скачать канонический план')
    }
  }

  const handleAddMedia = (files: File[]) => {
    setMediaFiles((prev) => [...prev, ...files])
  }

  const handleSubmitEntry = async (e: React.FormEvent) => {
    e.preventDefault()
    if (mediaFiles.length === 0 || isSubmitting) return
    setIsSubmitting(true)
    setEntryError(null)
    try {
      await addJournalEntry(project.id, mediaFiles, user?.username ?? 'Неизвестно', entryDate)
      setMediaFiles([])
      setEntryDate(todayDateString())
    } catch (err) {
      setEntryError(err instanceof Error ? err.message : 'Не удалось добавить видео или фото')
    } finally {
      setIsSubmitting(false)
    }
  }

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
            onSave={(name) => void updateProject(project.id, { name }).catch(() => {})}
          />
          <EditableText
            value={project.description}
            as="p"
            multiline
            placeholder="Добавить описание объекта"
            className="mt-2 max-w-2xl text-site-400"
            onSave={(description) => void updateProject(project.id, { description }).catch(() => {})}
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
                    <FileSpreadsheet className="h-5 w-5" />
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
                    Оригинал
                  </a>
                  {planInsightStatus === 'ready' && (
                    <button
                      type="button"
                      onClick={() => void handleDownloadCanonicalPlan()}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-site-600 px-3 py-2 text-sm font-medium text-site-200 transition hover:border-safety-400 hover:text-safety-300"
                    >
                      <Table2 className="h-4 w-4" />
                      Канонический план
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => changePlan(null)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-site-600 px-3 py-2 text-sm font-medium text-site-300 transition hover:border-red-500/50 hover:text-red-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ) : (
              <FileDropZone
                label="Загрузить план объекта"
                hint="CSV или Excel-таблица (.csv, .xlsx, .xls)"
                accept=".csv,.xlsx,.xls,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onFiles={(files) => changePlan(files[0])}
              />
            )}
          </div>
          {planError && (
            <p className="mt-3 rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
              {planError}
            </p>
          )}
          {(planInsightStatus === 'pending' || planInsightStatus === 'analyzing') && (
            <p className="mt-3 text-sm text-site-400">
              План не в каноническом формате — ИИ автоматически приводит его к нужному виду…
            </p>
          )}
          {planInsightStatus === 'failed' && (
            <p className="mt-3 rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
              Не удалось автоматически распознать план. Проверьте файл или загрузите план в
              каноническом формате.
            </p>
          )}
        </section>

        <section className="mt-10">
          <h2 className="font-display text-lg font-semibold text-site-100">Состояние объекта по плану</h2>
          <div className="mt-3 rounded-xl border border-site-700 bg-site-900/50 p-4">
            {videos.length === 0 ? (
              <p className="text-sm text-site-500">
                Загрузите видео или фото с объекта — по ним ИИ оценит текущее состояние
                строительства относительно плана.
              </p>
            ) : isAnalyzing ? (
              <p className="text-sm text-site-400">
                Идёт анализ загруженных файлов. Оценка состояния объекта появится, когда анализ
                завершится.
              </p>
            ) : (
              <>
                <p className="whitespace-pre-line text-sm leading-relaxed text-site-300">
                  {project.planStatus}
                </p>
                <p className="mt-3 text-xs text-site-500">
                  Сформировано автоматически по результатам анализа загруженных видео и фото
                </p>
              </>
            )}
          </div>
        </section>

        <section className="mt-10">
          <h2 className="font-display text-lg font-semibold text-site-100">Видео и фото объекта</h2>

          <form
            onSubmit={handleSubmitEntry}
            className="mt-3 flex flex-col gap-3 rounded-xl border border-site-700 bg-site-900/50 p-4"
          >
            <FileDropZone
              label="Загрузить видео или фото"
              hint="Можно выбрать несколько файлов"
              accept="video/*,image/*"
              multiple
              onFiles={handleAddMedia}
            />

            <label className="flex flex-col gap-1.5 text-sm text-site-300">
              Дата съёмки
              <input
                type="date"
                value={entryDate}
                max={todayDateString()}
                onChange={(e) => setEntryDate(e.target.value)}
                className="w-fit rounded-lg border border-site-600 bg-site-900 px-3 py-2 text-site-100 focus:border-safety-400 focus:outline-none"
              />
            </label>

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

            {entryError && (
              <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
                {entryError}
              </p>
            )}

            <div className="flex justify-end">
              <PrimaryButton type="submit" isLoading={isSubmitting} disabled={mediaFiles.length === 0}>
                Добавить
              </PrimaryButton>
            </div>
          </form>

          <div className="mt-6 flex flex-col gap-3">
            {entriesWithMedia.length === 0 ? (
              <p className="rounded-xl border border-dashed border-site-700 bg-site-900/30 px-4 py-8 text-center text-sm text-site-500">
                Видео и фото пока нет — загрузите первое выше
              </p>
            ) : (
              entriesWithMedia.map((entry) => (
                <JournalEntryCard key={entry.id} entry={entry} projectId={project.id} />
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
