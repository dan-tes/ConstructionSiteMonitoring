import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthApiError } from '../api/types'
import { AuthLayout } from '../components/AuthLayout'
import { Field, PrimaryButton, TextInput } from '../components/ui'
import { useAuth } from '../context/AuthContext'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)
    try {
      await login(username, password)
      navigate('/projects', { replace: true })
    } catch (err) {
      setError(err instanceof AuthApiError ? err.message : 'Не удалось войти. Попробуйте ещё раз.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthLayout
      title="Вход в систему"
      subtitle="Мониторинг объектов начинается здесь"
      footer={
        <>
          Ещё нет аккаунта?{' '}
          <Link to="/register" className="font-medium text-safety-400 hover:text-safety-300">
            Зарегистрироваться
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label="Ник" htmlFor="username">
          <TextInput
            id="username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="prorab_ivan"
          />
        </Field>
        <Field label="Пароль" htmlFor="password">
          <TextInput
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>

        {error && (
          <div className="rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
            {error}
          </div>
        )}

        <PrimaryButton type="submit" isLoading={isLoading} className="mt-2 w-full">
          Войти
        </PrimaryButton>
      </form>
    </AuthLayout>
  )
}
