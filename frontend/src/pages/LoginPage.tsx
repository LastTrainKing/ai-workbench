import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Database,
  Network,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/form'
import { InlineAlert } from '@/components/ui/feedback'
import { useAuthStore } from '@/store/auth'
import { useToastStore } from '@/store/toast'
import { toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

type Mode = 'login' | 'register'

interface FormState {
  identifier: string
  password: string
  email: string
  username: string
  display_name: string
}

const EMPTY: FormState = {
  identifier: '',
  password: '',
  email: '',
  username: '',
  display_name: '',
}

const FEATURES = [
  { icon: Database, title: '本地优先知识库', text: '上传 .md / .txt / .pdf，自动切片并向量化。' },
  { icon: Network, title: '可解释的混合检索', text: '关键词 + 语义双路召回，分数构成完全可见。' },
  { icon: Bot, title: 'Agent 与执行轨迹', text: '工具调用逐步留痕，每个 AI 运行都可回放。' },
]

function validate(mode: Mode, form: FormState): Partial<Record<keyof FormState, string>> {
  const errors: Partial<Record<keyof FormState, string>> = {}
  if (mode === 'login') {
    if (form.identifier.trim().length < 3) errors.identifier = '请输入用户名或邮箱（至少 3 个字符）'
    if (!form.password) errors.password = '请输入密码'
    return errors
  }

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = '请输入有效的邮箱地址'
  if (!/^[A-Za-z0-9_.-]{3,64}$/.test(form.username))
    errors.username = '3–64 位，仅允许字母、数字、下划线、点、连字符'
  if (form.password.length < 8) errors.password = '密码至少 8 位'
  else if (!(/[A-Za-z]/.test(form.password) && /\d/.test(form.password)))
    errors.password = '密码必须同时包含字母和数字'
  if (form.display_name.length > 128) errors.display_name = '昵称最长 128 个字符'
  return errors
}

export default function LoginPage() {
  // The demo account is created by the backend only when `app_env == "dev"`, and the
  // backend is the real gate. This is the second half of the same decision: a **build
  // time** switch, so the credentials are not in a shipped bundle at all.
  //
  // It used to read `env` off `/health`. That endpoint answers "is the process up?" and
  // nothing else now — a probe anyone can reach should answer the one question it is
  // asked — so the frontend cannot ask it, and does not need to: `npm run build` is a
  // production build and `npm run dev` is not.
  const showDemoCredentials = import.meta.env.DEV

  const navigate = useNavigate()
  const location = useLocation()
  const login = useAuthStore((state) => state.login)
  const register = useAuthStore((state) => state.register)
  const success = useToastStore((state) => state.success)

  const [mode, setMode] = useState<Mode>('login')
  const [form, setForm] = useState<FormState>(EMPTY)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [submitting, setSubmitting] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)

  const redirectTo = (location.state as { from?: string } | null)?.from || '/'

  const update = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
    setServerError(null)
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const validation = validate(mode, form)
    setErrors(validation)
    if (Object.keys(validation).length > 0) return

    setSubmitting(true)
    setServerError(null)
    try {
      if (mode === 'login') {
        await login({ identifier: form.identifier.trim(), password: form.password })
        success('登录成功', '欢迎回到 AI Workbench')
        // A returning account goes where it was headed. Whether it still needs the
        // wizard is not decided here — `RequireOnboarding` answers that from the
        // profile, which is the one place the answer lives.
        navigate(redirectTo, { replace: true })
      } else {
        await register({
          email: form.email.trim(),
          username: form.username.trim(),
          password: form.password,
          display_name: form.display_name.trim() || form.username.trim(),
        })
        success('账号创建成功', '已自动登录')
        // A brand-new account has never been through the wizard — true by definition,
        // so it goes straight there instead of taking a status round-trip first.
        navigate('/welcome', { replace: true })
      }
    } catch (error) {
      const apiError = toApiError(error)
      setServerError(apiError.displayMessage)
    } finally {
      setSubmitting(false)
    }
  }

  const fillDemo = () => {
    setMode('login')
    setForm({ ...EMPTY, identifier: 'demo', password: 'demo1234' })
    setErrors({})
    setServerError(null)
  }

  return (
    <div className="grid min-h-full lg:grid-cols-[1.05fr_minmax(420px,0.95fr)]">
      {/* brand panel */}
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-subtle bg-surface p-10 lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.5]"
          style={{
            backgroundImage:
              'radial-gradient(circle at 20% 15%, rgb(var(--c-accent) / 0.18), transparent 45%), radial-gradient(circle at 85% 80%, rgb(var(--c-info) / 0.12), transparent 45%)',
          }}
          aria-hidden
        />
        <div className="relative">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg bg-accent text-accent-fg">
              <Sparkles className="size-4" />
            </span>
            <span className="text-base font-semibold tracking-tight text-primary">AI Workbench</span>
          </div>
          <h1 className="mt-10 max-w-md text-3xl font-semibold leading-tight tracking-tight text-primary">
            把知识、任务与 AI 执行
            <br />
            收进同一个工作台
          </h1>
          <p className="mt-3 max-w-md text-sm text-muted">
            本地优先的 AI 知识与任务执行平台：文档入库、混合检索、RAG 问答、计划生成、Agent 工具调用，
            全部可追溯、可解释。
          </p>
        </div>

        <ul className="relative space-y-4">
          {FEATURES.map((feature) => {
            const Icon = feature.icon
            return (
              <li key={feature.title} className="flex items-start gap-3">
                <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-subtle bg-elevated text-accent">
                  <Icon className="size-3.5" />
                </span>
                <div>
                  <p className="text-xs font-medium text-primary">{feature.title}</p>
                  <p className="text-2xs text-muted">{feature.text}</p>
                </div>
              </li>
            )
          })}
        </ul>

        <p className="relative text-2xs text-faint">
          FastAPI · PostgreSQL · Redis · Qdrant · React 18 · TypeScript
        </p>
      </section>

      {/* form panel */}
      <section className="flex items-center justify-center px-5 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center gap-2.5 lg:hidden">
            <span className="flex size-8 items-center justify-center rounded-lg bg-accent text-accent-fg">
              <Sparkles className="size-4" />
            </span>
            <span className="text-base font-semibold tracking-tight text-primary">AI Workbench</span>
          </div>

          <div className="mb-5 grid grid-cols-2 gap-0.5 rounded-md border border-subtle bg-inset p-0.5">
            {(
              [
                { key: 'login', label: '登录' },
                { key: 'register', label: '注册' },
              ] as const
            ).map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  setMode(item.key)
                  setErrors({})
                  setServerError(null)
                }}
                className={cn(
                  'rounded py-1.5 text-xs font-medium transition-colors',
                  mode === item.key
                    ? 'bg-accent text-accent-fg'
                    : 'text-muted hover:bg-elevated hover:text-primary',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <form onSubmit={onSubmit} className="space-y-3.5" noValidate>
            {mode === 'login' ? (
              <>
                <Field label="用户名或邮箱" htmlFor="identifier" required error={errors.identifier}>
                  <Input
                    id="identifier"
                    autoComplete="username"
                    autoFocus
                    value={form.identifier}
                    invalid={Boolean(errors.identifier)}
                    onChange={(event) => update('identifier', event.target.value)}
                    placeholder="demo 或 demo@aiworkbench.dev"
                  />
                </Field>
                <Field label="密码" htmlFor="password" required error={errors.password}>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    value={form.password}
                    invalid={Boolean(errors.password)}
                    onChange={(event) => update('password', event.target.value)}
                    placeholder="••••••••"
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label="邮箱" htmlFor="email" required error={errors.email}>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="email"
                    autoFocus
                    value={form.email}
                    invalid={Boolean(errors.email)}
                    onChange={(event) => update('email', event.target.value)}
                    placeholder="you@example.com"
                  />
                </Field>
                <Field
                  label="用户名"
                  htmlFor="username"
                  required
                  error={errors.username}
                  hint="用于登录，仅限字母、数字、_ . -"
                >
                  <Input
                    id="username"
                    autoComplete="username"
                    value={form.username}
                    invalid={Boolean(errors.username)}
                    onChange={(event) => update('username', event.target.value)}
                    placeholder="yourname"
                  />
                </Field>
                <Field label="昵称" htmlFor="display_name" error={errors.display_name}>
                  <Input
                    id="display_name"
                    value={form.display_name}
                    onChange={(event) => update('display_name', event.target.value)}
                    placeholder="展示名称（可留空）"
                  />
                </Field>
                <Field
                  label="密码"
                  htmlFor="password"
                  required
                  error={errors.password}
                  hint="至少 8 位，且必须同时包含字母和数字"
                >
                  <Input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    value={form.password}
                    invalid={Boolean(errors.password)}
                    onChange={(event) => update('password', event.target.value)}
                    placeholder="••••••••"
                  />
                </Field>
              </>
            )}

            {serverError ? (
              <InlineAlert tone="danger" title="请求失败">
                {serverError}
              </InlineAlert>
            ) : null}

            <Button
              type="submit"
              variant="primary"
              size="md"
              block
              loading={submitting}
              iconRight={<ArrowRight className="size-4" />}
            >
              {mode === 'login' ? '登录' : '创建账号'}
            </Button>
          </form>

          {showDemoCredentials ? (
          <div className="mt-5 rounded-md border border-subtle bg-surface px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-2xs font-medium text-muted">
              <TriangleAlert className="size-3" />
              演示账号
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <code className="font-mono text-2xs text-primary">demo / demo1234</code>
              <Button size="xs" variant="ghost" onClick={fillDemo} icon={<CheckCircle2 className="size-3" />}>
                填入
              </Button>
            </div>
          </div>
          ) : null}
        </div>
      </section>
    </div>
  )
}
