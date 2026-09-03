import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthApiError } from '../api/types'
import { AuthLayout } from '../components/AuthLayout'
import { Field, PrimaryButton, TextInput } from '../components/ui'
import { useAuth } from '../context/AuthContext'

const MIN_PASSWORD_LENGTH = 6

export function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Пароль должен быть не короче ${MIN_PASSWORD_LENGTH} символов`)
      return
    }
    if (password !== confirmPassword) {
      setError('Пароли не совпадают')
      return
    }

    setIsLoading(true)
    try {
      await register(username, password)
      navigate('/projects', { replace: true })
    } catch (err) {
      setError(err instanceof AuthApiError ? err.message : 'Не удалось зарегистрироваться. Попробуйте ещё раз.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthLayout
      title="Регистрация"
      subtitle="Создайте аккаунт прораба"
      footer={
        <>
          Уже есть аккаунт?{' '}
          <Link to="/login" className="font-medium text-safety-400 hover:text-safety-300">
            Войти
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
            minLength={3}
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
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        <Field label="Повторите пароль" htmlFor="confirmPassword">
          <TextInput
            id="confirmPassword"
            name="confirmPassword"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>

        {error && (
          <div className="rounded-lg border border-red-900/60 bg-red-950/40 px-3.5 py-2.5 text-sm text-red-300">
            {error}
          </div>
        )}

        <PrimaryButton type="submit" isLoading={isLoading} className="mt-2 w-full">
          Создать аккаунт
        </PrimaryButton>
      </form>
    </AuthLayout>
  )
}
