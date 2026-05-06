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
}

export function ResearchInput({ input, setInput, onSubmit, isLoading, onStop }: ResearchInputProps) {
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
      <div className="mb-3 flex items-end justify-between gap-3 px-1">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-primary">Research Workspace</div>
          <div className="mt-1 text-xs text-muted-foreground">Describe the research question, scope, constraints, preferred sources, and expected report format.</div>
        </div>
        <div className="hidden rounded-full border bg-muted/30 px-3 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground sm:block">
          Deep Research
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
          placeholder="Enter a deep research task..."
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
        <span>Research sessions use web search, evidence extraction, quality checks, and report generation.</span>
        <span>Review citations before relying on conclusions.</span>
      </div>
    </div>
  )
}
