import { ArrowRight, Camera, Cpu, Database, Sparkles, UploadCloud } from 'lucide-react'

const PIPELINE_STEPS = [
  { icon: Camera, label: 'Фото из журнала' },
  { icon: UploadCloud, label: 'Сервер' },
  { icon: Cpu, label: 'ML-модель' },
  { icon: Database, label: 'RAG-система' },
  { icon: Sparkles, label: 'Стадия объекта' },
]

export function AiStageAnalysis({ hasMedia }: { hasMedia: boolean }) {
  return (
    <section className="rounded-2xl border border-site-700 bg-site-900/50 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-site-100">
          Стадия объекта по данным ИИ
        </h2>
        <span className="rounded-full bg-safety-500/15 px-2.5 py-1 text-xs font-semibold text-safety-300">
          Скоро
        </span>
      </div>
      <p className="mt-2 max-w-2xl text-sm text-site-400">
        {hasMedia
          ? 'Фото из журнала готовы к анализу. Как только подключим конвейер ниже, здесь появится автоматическая оценка стадии строительства.'
          : 'Добавьте фото в журнал объекта — по ним ИИ будет определять текущую стадию строительства.'}
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {PIPELINE_STEPS.map((step, index) => (
          <div key={step.label} className="flex items-center gap-2">
            <div className="flex w-20 flex-col items-center gap-1.5 rounded-xl border border-site-700 bg-site-800/60 px-2 py-2.5 text-center">
              <step.icon className="h-4.5 w-4.5 text-safety-400" strokeWidth={1.75} />
              <span className="text-[11px] leading-tight text-site-400">{step.label}</span>
            </div>
            {index < PIPELINE_STEPS.length - 1 && (
              <ArrowRight className="h-4 w-4 shrink-0 text-site-600" />
            )}
          </div>
        ))}
      </div>

      <button
        type="button"
        disabled
        title="Появится после подключения сервера, ML-модели и RAG-системы"
        className="mt-5 inline-flex cursor-not-allowed items-center gap-2 rounded-lg border border-site-700 px-4 py-2.5 text-sm font-medium text-site-500 opacity-70"
      >
        <Sparkles className="h-4 w-4" />
        Запросить анализ стадии
      </button>
    </section>
  )
}
