import { ArrowLeft, Cpu, Eye, HardHat, Loader2, Truck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useProjects } from '../context/ProjectsContext'
import { MediaGrid } from '../components/MediaGrid'

export function EntryDetailPage() {
  const { id, entryId } = useParams<{ id: string; entryId: string }>()
  const { user } = useAuth()
  const { getProject, refreshProject, refreshEntry } = useProjects()
  const project = id ? getProject(id) : undefined
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    if (!id) return
    refreshProject(id).catch(() => setNotFound(true))
  }, [id, refreshProject])

  const entry = project?.entries.find((e) => e.id === entryId)
  const insight = entry?.insight
  const status = insight?.status
  const isReady = status === 'ready'
  const isFailed = status === 'failed'

  // Poll the combined analysis while it's running (the entry exists but
  // isn't done yet — see backend's analysis.py run_entry_analysis).
  useEffect(() => {
    if (!id || !entryId || !entry) return
    if (status === 'ready' || status === 'failed') return
    const timer = setInterval(() => {
      void refreshEntry(id, entryId).catch(() => {})
    }, 4000)
    return () => clearInterval(timer)
  }, [id, entryId, entry, status, refreshEntry])

  if (notFound) {
    return <Navigate to="/projects" replace />
  }

  if (!project) {
    return (
      <div className="flex min-h-svh items-center justify-center text-site-400">Загрузка…</div>
    )
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
          to={`/projects/${project.id}`}
          className="inline-flex items-center gap-1.5 text-sm text-site-400 transition hover:text-safety-400"
        >
          <ArrowLeft className="h-4 w-4" />
          {project.name}
        </Link>

        {!entry ? (
          <p className="mt-8 rounded-xl border border-dashed border-site-700 bg-site-900/30 px-4 py-10 text-center text-sm text-site-500">
            Запись не найдена — возможно, она была удалена.
          </p>
        ) : (
          <>
            <h1 className="mt-4 font-display text-2xl font-bold text-site-100 sm:text-3xl">
              Запись от {new Date(entry.date).toLocaleDateString('ru-RU')}
            </h1>
            <p className="mt-1 text-sm text-site-500">
              {entry.author}
              {entry.comment ? ` — ${entry.comment}` : ''}
            </p>

            <div className="hazard-stripes mt-6 h-1 w-full rounded-full opacity-70" />

            <MediaGrid files={entry.media} />

            {isFailed ? (
              <div className="mt-8 rounded-2xl border border-red-900/60 bg-red-950/30 p-5">
                <h2 className="font-display text-lg font-semibold text-site-100">
                  Не удалось проанализировать запись
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-site-400">
                  Нейросеть не смогла обработать эти файлы. Попробуйте загрузить их ещё раз.
                </p>
              </div>
            ) : !isReady ? (
              <div className="mt-8 flex items-start gap-3 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-safety-400" />
                <div>
                  <h2 className="font-display text-lg font-semibold text-site-100">
                    Запись анализируется
                  </h2>
                  <p className="mt-2 text-sm leading-relaxed text-site-400">
                    Нейросеть обрабатывает загруженные файлы. Пока анализ не завершён, сказать
                    что-либо об этапе строительства или технике в кадре невозможно — результаты
                    появятся на этой странице автоматически.
                  </p>
                </div>
              </div>
            ) : (
              <>
                <section className="mt-8 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-site-800 text-safety-400">
                      <Cpu className="h-4.5 w-4.5" strokeWidth={1.75} />
                    </span>
                    <h2 className="font-display text-lg font-semibold text-site-100">
                      Этап строительства по плану
                    </h2>
                  </div>
                  <p className="mt-3 text-sm leading-relaxed text-site-300">
                    {insight?.stageSummary}
                  </p>
                </section>

                <section className="mt-4 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-site-800 text-safety-400">
                      <Truck className="h-4.5 w-4.5" strokeWidth={1.75} />
                    </span>
                    <h2 className="font-display text-lg font-semibold text-site-100">
                      Техника в кадре
                    </h2>
                  </div>
                  <p className="mt-3 text-sm leading-relaxed text-site-300">
                    {insight?.equipmentSummary}
                  </p>
                </section>

                {insight?.visualPhaseName && (
                  <section className="mt-4 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                    <div className="flex items-center gap-2.5">
                      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-site-800 text-safety-400">
                        <Eye className="h-4.5 w-4.5" strokeWidth={1.75} />
                      </span>
                      <h2 className="font-display text-lg font-semibold text-site-100">
                        Этап по фото (эксперимент)
                      </h2>
                    </div>
                    <p className="mt-3 text-sm leading-relaxed text-site-300">
                      {insight.visualPhaseName}
                      {typeof insight.visualPhaseConfidence === 'number' &&
                        ` (${Math.round(insight.visualPhaseConfidence * 100)}%)`}
                    </p>
                    <p className="mt-2 text-xs leading-relaxed text-site-500">
                      Отдельная визуальная модель определяет этап прямо по кадру, без учёта
                      техники. Пока распознаёт не все этапы — используйте как дополнительную
                      подсказку, а не как основной результат.
                    </p>
                  </section>
                )}

                <p className="mt-4 text-xs text-site-500">
                  Оценка сформирована нейросетью по всем файлам этой записи
                </p>
              </>
            )}
          </>
        )}
      </main>
    </div>
  )
}
