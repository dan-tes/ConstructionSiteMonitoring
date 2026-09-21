import { ArrowLeft, Cpu, Eye, HardHat, Loader2, Truck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useProjects } from '../context/ProjectsContext'
import { formatFileSize } from '../lib/files'

export function VideoDetailPage() {
  const { id, videoId } = useParams<{ id: string; videoId: string }>()
  const { user } = useAuth()
  const { getProject, refreshProject, refreshVideo } = useProjects()
  const project = id ? getProject(id) : undefined
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    if (!id) return
    refreshProject(id).catch(() => setNotFound(true))
  }, [id, refreshProject])

  const entry = project?.entries.find((e) => e.media.some((m) => m.id === videoId))
  const video = entry?.media.find((m) => m.id === videoId)
  const isImage = video?.kind === 'image'
  const insight = video?.insight
  const status = insight?.status
  const isReady = status === 'ready'
  const isFailed = status === 'failed'
  const photos = insight?.photos ?? []

  // Poll the analysis while it's running (the video exists but isn't done yet).
  useEffect(() => {
    if (!id || !videoId || !video) return
    if (status === 'ready' || status === 'failed') return
    const timer = setInterval(() => {
      void refreshVideo(id, videoId).catch(() => {})
    }, 4000)
    return () => clearInterval(timer)
  }, [id, videoId, video, status, refreshVideo])

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

        {!video ? (
          <p className="mt-8 rounded-xl border border-dashed border-site-700 bg-site-900/30 px-4 py-10 text-center text-sm text-site-500">
            Файл не найден — возможно, он был удалён.
          </p>
        ) : (
          <>
            <h1 className="mt-4 font-display text-2xl font-bold text-site-100 sm:text-3xl">
              {video.name}
            </h1>
            <p className="mt-1 text-sm text-site-500">{formatFileSize(video.size)}</p>

            <div className="hazard-stripes mt-6 h-1 w-full rounded-full opacity-70" />

            {isImage ? (
              <img
                src={video.url}
                alt={video.name}
                className="mt-6 w-full rounded-xl border border-site-700 bg-black object-contain"
              />
            ) : (
              <video
                src={video.url}
                controls
                className="mt-6 w-full rounded-xl border border-site-700 bg-black"
              />
            )}

            {isFailed ? (
              <div className="mt-8 rounded-2xl border border-red-900/60 bg-red-950/30 p-5">
                <h2 className="font-display text-lg font-semibold text-site-100">
                  Не удалось проанализировать {isImage ? 'фото' : 'видео'}
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-site-400">
                  Нейросеть не смогла обработать этот файл. Попробуйте загрузить{' '}
                  {isImage ? 'фото' : 'видео'} ещё раз.
                </p>
              </div>
            ) : !isReady ? (
              <div className="mt-8 flex items-start gap-3 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-safety-400" />
                <div>
                  <h2 className="font-display text-lg font-semibold text-site-100">
                    {isImage ? 'Фото анализируется' : 'Видео анализируется'}
                  </h2>
                  <p className="mt-2 text-sm leading-relaxed text-site-400">
                    Нейросеть обрабатывает этот файл. Пока анализ не завершён, сказать что-либо об
                    этапе строительства, технике в кадре или зафиксированных событиях невозможно —
                    результаты появятся на этой странице автоматически.
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

                {photos.length > 0 && (
                  <section className="mt-4 rounded-2xl border border-site-700 bg-site-900/50 p-5">
                    <h2 className="font-display text-lg font-semibold text-site-100">
                      Кадры из видео
                    </h2>
                    <p className="mt-1 text-sm text-site-400">
                      Техника и важные зафиксированные события
                    </p>
                    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                      {photos.map((photo) => (
                        <a
                          key={photo.id}
                          href={photo.url}
                          target="_blank"
                          rel="noreferrer"
                          className="aspect-square overflow-hidden rounded-lg border border-site-700 bg-site-800"
                        >
                          <img
                            src={photo.url}
                            alt={photo.name}
                            className="h-full w-full object-cover"
                          />
                        </a>
                      ))}
                    </div>
                  </section>
                )}

                <p className="mt-4 text-xs text-site-500">
                  Оценка сформирована нейросетью по этому {isImage ? 'фото' : 'видео'}
                </p>
              </>
            )}
          </>
        )}
      </main>
    </div>
  )
}
