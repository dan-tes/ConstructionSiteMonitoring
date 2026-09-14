import { Construction, FolderKanban, HardHat, LogOut, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Modal } from '../components/Modal'
import { Field, GhostButton, PrimaryButton, TextInput } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useProjects } from '../context/ProjectsContext'

export function ProjectsPage() {
  const { user, logout } = useAuth()
  const { projects, loading, error, createProject } = useProjects()
  const navigate = useNavigate()
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || isCreating) return
    setIsCreating(true)
    setCreateError(null)
    try {
      const project = await createProject(name, description)
      setIsModalOpen(false)
      setName('')
      setDescription('')
      navigate(`/projects/${project.id}`)
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Не удалось создать объект')
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <div className="min-h-svh">
      <header className="border-b border-site-800 bg-site-900/60 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-safety-500 text-site-950">
              <HardHat className="h-5 w-5" strokeWidth={2.25} />
            </span>
            <span className="font-display text-lg font-bold tracking-tight text-site-100">
              СтройМонитор
            </span>
          </div>

          <div className="flex items-center gap-4">
            <span className="hidden text-sm text-site-400 sm:inline">
              Прораб <span className="font-medium text-site-200">{user?.username}</span>
            </span>
            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 rounded-lg border border-site-700 px-3 py-2 text-sm font-medium text-site-300 transition hover:border-red-500/50 hover:text-red-400"
            >
              <LogOut className="h-4 w-4" />
              Выйти
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-10">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl font-bold text-site-100 sm:text-3xl">Объекты</h1>
            <p className="mt-1 text-site-400">Все ваши строительные площадки в одном месте</p>
          </div>
          <PrimaryButton type="button" onClick={() => setIsModalOpen(true)}>
            <Plus className="h-4 w-4" />
            Новый объект
          </PrimaryButton>
        </div>

        <div className="hazard-stripes h-1 w-full rounded-full opacity-70" />

        {error && (
          <div className="mt-6 rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
            {error}
          </div>
        )}

        {loading && projects.length === 0 ? (
          <p className="mt-10 text-center text-sm text-site-500">Загрузка объектов…</p>
        ) : projects.length === 0 ? (
          <div className="mt-10 flex flex-col items-center justify-center rounded-2xl border border-dashed border-site-700 bg-site-900/40 px-6 py-20 text-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-site-800 text-safety-400">
              <Construction className="h-8 w-8" strokeWidth={1.75} />
            </span>
            <h2 className="mt-5 font-display text-xl font-semibold text-site-100">
              Пока нет ни одного объекта
            </h2>
            <p className="mt-2 max-w-sm text-sm text-site-400">
              Здесь появятся строительные площадки, за которыми вы следите — статус, прогресс и данные с
              камер.
            </p>
          </div>
        ) : (
          <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => (
              <Link
                key={project.id}
                to={`/projects/${project.id}`}
                className="group flex flex-col rounded-2xl border border-site-700 bg-site-900/50 p-5 transition hover:border-safety-400/60 hover:bg-site-900"
              >
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-site-800 text-safety-400 transition group-hover:bg-safety-500 group-hover:text-site-950">
                  <FolderKanban className="h-5.5 w-5.5" strokeWidth={1.75} />
                </span>
                <h3 className="mt-4 font-display text-lg font-semibold text-site-100">{project.name}</h3>
                <p className="mt-1 line-clamp-2 min-h-10 text-sm text-site-400">
                  {project.description || 'Без описания'}
                </p>
                <div className="mt-4 flex items-center justify-between text-xs text-site-500">
                  <span>{project.entries.length} видео</span>
                  <span>{project.plan ? 'План загружен' : 'Плана нет'}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>

      {isModalOpen && (
        <Modal title="Новый объект" onClose={() => setIsModalOpen(false)}>
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <Field label="Название" htmlFor="project-name">
              <TextInput
                id="project-name"
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ЖК «Северный», корпус 2"
              />
            </Field>
            <Field label="Описание" htmlFor="project-description">
              <textarea
                id="project-description"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Кратко об объекте (необязательно)"
                className="w-full rounded-lg border border-site-600 bg-site-800/80 px-3.5 py-2.5 text-site-100 outline-none placeholder:text-site-500 transition focus:border-safety-400 focus:ring-2 focus:ring-safety-400/30"
              />
            </Field>
            {createError && (
              <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
                {createError}
              </p>
            )}
            <div className="mt-2 flex justify-end gap-3">
              <GhostButton type="button" onClick={() => setIsModalOpen(false)}>
                Отмена
              </GhostButton>
              <PrimaryButton type="submit" isLoading={isCreating}>
                Создать
              </PrimaryButton>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
