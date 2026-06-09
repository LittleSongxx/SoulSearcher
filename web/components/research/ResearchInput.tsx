'use client'

import React, { useEffect, useRef } from 'react'
import { Paperclip, Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ResearchInputProps {
  input: string
  setInput: React.Dispatch<React.SetStateAction<string>>
  onSubmit: () => void
  isLoading: boolean
  onStop: () => void
  useWebSearch: boolean
  useDeepResearch: boolean
  onWebSearchChange: (enabled: boolean) => void
  onDeepResearchChange: (enabled: boolean) => void
}

export function ResearchInput({
  input,
  setInput,
  onSubmit,
  isLoading,
  onStop,
  useWebSearch,
  useDeepResearch,
  onWebSearchChange,
  onDeepResearchChange,
}: ResearchInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'inherit'
      const scrollHeight = textareaRef.current.scrollHeight
      textareaRef.current.style.height = `${Math.min(Math.max(scrollHeight, 72), 240)}px`
    }
  }, [input])

  useEffect(() => {
    if (!isLoading) textareaRef.current?.focus()
  }, [isLoading])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 pb-6">
      <div className="mb-3 flex flex-col gap-3 px-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-primary">Weaver Chat</div>
          <div className="mt-1 text-xs text-muted-foreground">
            默认普通对话；按需开启联网搜索或深度研究。
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant={useWebSearch ? 'default' : 'outline'}
            disabled={isLoading || useDeepResearch}
            onClick={() => onWebSearchChange(!useWebSearch)}
            className="h-8 rounded-full px-3 text-xs"
          >
            联网搜索
          </Button>
          <Button
            type="button"
            size="sm"
            variant={useDeepResearch ? 'default' : 'outline'}
            disabled={isLoading}
            onClick={() => onDeepResearchChange(!useDeepResearch)}
            className="h-8 rounded-full px-3 text-xs"
          >
            深度研究
          </Button>
        </div>
      </div>

      <div className="relative rounded-3xl border border-border/60 bg-background shadow-lg shadow-black/5 transition-all focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
        <div className="absolute left-4 top-5 text-muted-foreground">
          <Paperclip className="h-4 w-4" />
        </div>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={1}
          placeholder={useDeepResearch ? '输入深度研究任务...' : useWebSearch ? '输入需要联网搜索的问题...' : '输入消息...'}
          className="min-h-[72px] max-h-[240px] w-full resize-none bg-transparent py-5 pl-12 pr-16 text-base outline-none placeholder:text-muted-foreground/50"
        />
        <div className="absolute bottom-4 right-4">
          {isLoading ? (
            <Button type="button" size="icon" variant="ghost" onClick={onStop} className="h-9 w-9 rounded-full hover:bg-destructive/10 hover:text-destructive">
              <div className="h-2.5 w-2.5 rounded-sm bg-current animate-pulse" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              onClick={onSubmit}
              disabled={!input.trim()}
              className={cn('h-9 w-9 rounded-full transition-all', input.trim() ? 'hover:scale-105' : 'bg-muted text-muted-foreground')}
            >
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      <div className="mt-2 flex justify-between px-3 text-[10px] text-muted-foreground/70">
        <span>{useDeepResearch ? '深度研究会自动启用联网、agent 工具和研究编排。' : useWebSearch ? '联网搜索会使用在线来源回答。' : '普通聊天不会联网或调用工具。'}</span>
        <span>{useDeepResearch ? '架构：Supervisor Workers' : '需要报告时开启深度研究。'}</span>
      </div>
    </div>
  )
}
