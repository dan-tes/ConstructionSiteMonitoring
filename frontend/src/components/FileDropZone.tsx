import { UploadCloud } from 'lucide-react'
import { useRef, useState } from 'react'

export function FileDropZone({
  accept,
  multiple,
  label,
  hint,
  onFiles,
}: {
  accept?: string
  multiple?: boolean
  label: string
  hint?: string
  onFiles: (files: File[]) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return
    onFiles(Array.from(fileList))
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        setIsDragOver(true)
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setIsDragOver(false)
        handleFiles(e.dataTransfer.files)
      }}
      className={
        'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition ' +
        (isDragOver
          ? 'border-safety-400 bg-safety-400/10'
          : 'border-site-700 bg-site-900/40 hover:border-site-500')
      }
    >
      <UploadCloud className="h-7 w-7 text-safety-400" strokeWidth={1.75} />
      <p className="text-sm font-medium text-site-200">{label}</p>
      {hint && <p className="text-xs text-site-500">{hint}</p>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          handleFiles(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
