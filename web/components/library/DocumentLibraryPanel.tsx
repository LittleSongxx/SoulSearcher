'use client'

import React, { useEffect, useRef, useState } from 'react'
import { FileText, RefreshCcw, Search, Trash2, UploadCloud, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getApiBaseUrl } from '@/lib/api'

interface LibraryDocument {
  document_id: string
  filename: string
  status?: string
  chunk_count?: number
  created_at?: string
}

interface DocumentLibraryPanelProps {
  open: boolean
  onClose: () => void
}

export function DocumentLibraryPanel({ open, onClose }: DocumentLibraryPanelProps) {
  const [documents, setDocuments] = useState<LibraryDocument[]>([])
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [status, setStatus] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadDocuments = async () => {
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/library/documents`)
      if (!response.ok) throw new Error(await response.text())
      const data = await response.json()
      setDocuments(Array.isArray(data.documents) ? data.documents : [])
      setStatus('')
    } catch (err) {
      setStatus(err instanceof Error ? err.message : '资料库不可用')
    }
  }

  useEffect(() => {
    if (open) loadDocuments()
  }, [open])

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setStatus('正在上传并索引...')
    for (const file of Array.from(files)) {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch(`${getApiBaseUrl()}/api/library/documents`, {
        method: 'POST',
        body,
      })
      if (!response.ok) {
        setStatus(`上传失败：${file.name}`)
        return
      }
    }
    setStatus('索引完成')
    await loadDocuments()
  }

  const deleteDocument = async (id: string) => {
    const response = await fetch(`${getApiBaseUrl()}/api/library/documents/${id}`, {
      method: 'DELETE',
    })
    if (response.ok) await loadDocuments()
  }

  const reindexDocument = async (id: string) => {
    setStatus('正在重建索引...')
    const response = await fetch(`${getApiBaseUrl()}/api/library/documents/${id}/reindex`, {
      method: 'POST',
    })
    setStatus(response.ok ? '重建完成' : '重建失败')
    await loadDocuments()
  }

  const searchLibrary = async () => {
    if (!query.trim()) return
    const response = await fetch(`${getApiBaseUrl()}/api/library/search?q=${encodeURIComponent(query.trim())}`)
    if (!response.ok) {
      setStatus('搜索失败')
      return
    }
    const data = await response.json()
    setResults(Array.isArray(data.results) ? data.results : [])
  }

  if (!open) return null

  return (
    <div className="fixed inset-y-0 left-0 z-50 flex w-full max-w-[520px] flex-col border-r bg-background shadow-2xl md:left-[280px]">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-primary" />
          <div className="text-sm font-semibold">资料库</div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="border-b p-4">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".txt,.md,.html,.htm,.json,.csv,.pdf,.docx"
          className="hidden"
          onChange={event => uploadFiles(event.target.files)}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={event => event.preventDefault()}
          onDrop={event => {
            event.preventDefault()
            uploadFiles(event.dataTransfer.files)
          }}
          className="flex h-28 w-full flex-col items-center justify-center gap-2 rounded-md border border-dashed bg-muted/20 text-sm text-muted-foreground transition hover:border-primary hover:text-primary"
        >
          <UploadCloud className="h-5 w-5" />
          上传或拖拽 txt/md/html/json/csv/pdf/docx
        </button>
        {status && <div className="mt-2 text-xs text-muted-foreground">{status}</div>}
      </div>

      <div className="flex items-center gap-2 border-b p-4">
        <Input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索资料库..." />
        <Button size="icon" onClick={searchLibrary}>
          <Search className="h-4 w-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">已索引文档</div>
        <div className="space-y-2">
          {documents.map(doc => (
            <div key={doc.document_id} className="rounded-md border p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{doc.filename}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {doc.status || 'indexed'} · {doc.chunk_count || 0} chunks
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => reindexDocument(doc.document_id)}>
                    <RefreshCcw className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteDocument(doc.document_id)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
          {!documents.length && <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">暂无文档</div>}
        </div>

        {results.length > 0 && (
          <div className="mt-5">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">搜索结果</div>
            <div className="space-y-2">
              {results.map((item, index) => (
                <div key={`${item.chunk_id || index}`} className="rounded-md border bg-card p-3">
                  <div className="text-sm font-medium">{item.title || item.document_id}</div>
                  <div className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted-foreground">{item.content}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
