'use client'

import React, { useState, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import dynamic from 'next/dynamic'
import { cn } from '@/lib/utils'
import { Check, Copy, Pencil, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DataTableView } from './DataTableView'
import { ErrorBoundary } from 'react-error-boundary'
import { toast } from 'sonner'
import { Message } from '@/types/chat'
import { ThinkingProcess } from './message/ThinkingProcess'
import { CodeBlock } from './message/CodeBlock'
import { CitationBadge } from './message/CitationBadge'

// Lazy load MermaidBlock as it's a heavy dependency
const MermaidBlock = dynamic(() => import('./MermaidBlock').then(mod => mod.MermaidBlock), {
  loading: () => (
    <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center border rounded-lg bg-muted/10">
      <Loader2 className="h-4 w-4 animate-spin" />
      <span>Loading visualization...</span>
    </div>
  ),
  ssr: false
})

function ErrorFallback({ error }: { error: Error }) {
  return (
    <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-xs">
      <p className="font-semibold">Visualization Error</p>
      <pre className="mt-1 opacity-70">{error.message}</pre>
    </div>
  )
}

// Helper to normalize LaTeX delimiters
const preprocessContent = (content: string) => {
  if (!content) return ''
  return content
    .replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$') // \( ... \) -> $ ... $
    .replace(/\\\[([\s\S]*?)\\\]/g, '$$$$$1$$$$') // \[ ... \] -> $$ ... $$
}

interface MessageItemProps {
  message: Message
  onEdit?: (id: string, newContent: string) => void
}

const MessageItemBase = ({ message, onEdit }: MessageItemProps) => {
  const isUser = (message.role || '').toLowerCase() === 'user'
  const [copied, setCopied] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editContent, setEditContent] = useState(message.content)

  // Preprocess content for Math rendering
  const displayContent = preprocessContent(message.content || '')

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content)
    setCopied(true)
    toast.success('Message copied')
    setTimeout(() => setCopied(false), 2000)
  }

  const handleSaveEdit = () => {
    if (onEdit && editContent.trim() !== message.content) {
      onEdit(message.id, editContent)
    }
    setIsEditing(false)
  }

  const tools = message.toolInvocations || []
  const processEvents = message.processEvents || []
  const isThinking = Boolean(message.isStreaming) || tools.some(t => t.state === 'running')
  const showThinking = !isUser && !isEditing && (isThinking || tools.length > 0 || processEvents.length > 0)
  const hideAssistantBubble =
    !isUser && !isEditing && !displayContent.trim() && isThinking

  return (
    <div
      className={cn(
        'group flex w-full gap-2 py-4 animate-in fade-in slide-in-from-bottom-1 duration-300',
        isUser ? 'justify-end' : 'justify-start'
      )}
    >
      {/* Bot Avatar - Removed */}
      <div className={cn(
        'flex flex-col',
        isUser ? "max-w-[90%] md:max-w-[85%] items-end ml-auto" : "w-full max-w-full items-start mr-auto",
      )}>

        {/* Thinking Process */}
        {showThinking && (
          <ThinkingProcess
            tools={tools}
            events={processEvents}
            metrics={message.metrics}
            startedAt={message.createdAt}
            completedAt={message.completedAt}
            isThinking={isThinking}
          />
        )}

        {/* Message Bubble OR Edit Mode */}
        {isEditing ? (
          <div className="w-full bg-muted/30 p-4 rounded-xl border border-primary/20 shadow-sm animate-in fade-in zoom-in-95">
            <textarea
              value={editContent}
              onChange={e => setEditContent(e.target.value)}
              className="w-full bg-transparent resize-none focus:outline-none min-h-[100px] text-sm leading-relaxed"
            />
            <div className="flex justify-end gap-2 mt-3">
              <Button size="sm" variant="ghost" onClick={() => setIsEditing(false)}>Cancel</Button>
              <Button size="sm" onClick={handleSaveEdit}>Save & Submit</Button>
            </div>
          </div>
        ) : hideAssistantBubble ? null : (
          <div className={cn(
            "relative px-5 py-3.5 shadow-sm",
            isUser
              ? "bg-primary text-primary-foreground rounded-2xl rounded-tr-sm"
              : "bg-muted/30 border text-foreground rounded-2xl rounded-tl-sm backdrop-blur-sm"
          )}
          >
            <div className={cn(
              "prose prose-neutral dark:prose-invert max-w-none break-words",
              "text-[15px] md:text-base leading-[1.8]"
            )}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={{
                  pre: ({ children }) => <>{children}</>,
                  h1: ({ node, children, ...props }) => (
                    <h1 className="text-xl font-bold mt-8 mb-4 first:mt-0 pb-2 border-b border-border/40" {...props}>{children}</h1>
                  ),
                  h2: ({ node, children, ...props }) => (
                    <h2 className="text-lg font-bold mt-7 mb-3 first:mt-0" {...props}>{children}</h2>
                  ),
                  h3: ({ node, children, ...props }) => (
                    <h3 className="text-base font-semibold mt-6 mb-2 first:mt-0" {...props}>{children}</h3>
                  ),
                  h4: ({ node, children, ...props }) => (
                    <h4 className="text-sm font-semibold mt-5 mb-2 first:mt-0" {...props}>{children}</h4>
                  ),
                  p: ({ node, children, ...props }) => (
                    <p className="mb-4 last:mb-0 leading-[1.8]" {...props}>
                      {React.Children.map(children, child => {
                        if (typeof child === 'string') {
                          const parts = child.split(/(\[\d+\])/g)
                          return parts.map((part, i) => {
                            const match = part.match(/^\[(\d+)\]$/)
                            if (match) {
                              return <CitationBadge key={i} num={match[1]} />
                            }
                            return part
                          })
                        }
                        return child
                      })}
                    </p>
                  ),
                  code: ({ node, className, children, ...props }: any) => {
                    const match = /language-(\w+)/.exec(className || '')
                    const isInline = !match && !String(children).includes('\n')
                    const content = String(children).replace(/\n$/, '')

                    // Check for Mermaid
                    if (match && match[1] === 'mermaid') {
                      return (
                        <ErrorBoundary FallbackComponent={ErrorFallback}>
                          <MermaidBlock code={content} />
                        </ErrorBoundary>
                      )
                    }

                    // Check for JSON/CSV
                    if (match && (match[1] === 'json' || match[1] === 'csv')) {
                      return (
                        <div className="flex flex-col gap-2">
                          <ErrorBoundary FallbackComponent={() => null}>
                            <DataTableView data={content} type={match[1] as 'json' | 'csv'} />
                          </ErrorBoundary>
                          <CodeBlock language={match[1]} value={content} />
                        </div>
                      )
                    }

                    if (isInline) {
                      return (
                        <code className="bg-black/10 dark:bg-black/30 px-1.5 py-0.5 rounded text-sm font-mono break-words whitespace-pre-wrap" {...props}>
                          {children}
                        </code>
                      )
                    }

                    return (
                      <CodeBlock language={match ? match[1] : 'text'} value={content} />
                    )
                  },
                  ul: ({ node, children, ...props }) => (
                    <ul className="my-4 pl-6 space-y-2 list-disc marker:text-muted-foreground/60" {...props}>{children}</ul>
                  ),
                  ol: ({ node, children, ...props }) => (
                    <ol className="my-4 pl-6 space-y-2 list-decimal marker:text-muted-foreground/60" {...props}>{children}</ol>
                  ),
                  li: ({ node, children, ...props }) => (
                    <li className="leading-[1.8] pl-1" {...props}>{children}</li>
                  ),
                  blockquote: ({ node, children, ...props }) => (
                    <blockquote className="my-4 border-l-3 border-primary/30 pl-4 py-1 text-muted-foreground italic bg-muted/20 rounded-r-md" {...props}>{children}</blockquote>
                  ),
                  hr: ({ node, ...props }) => (
                    <hr className="my-6 border-border/50" {...props} />
                  ),
                  strong: ({ node, children, ...props }) => (
                    <strong className="font-semibold text-foreground" {...props}>{children}</strong>
                  ),
                  a: ({ node, ...props }) => (
                    <a className={cn("underline underline-offset-2 font-medium", isUser ? "text-white" : "text-primary hover:text-primary/80")} {...props} />
                  ),
                  table: ({ node, children, ...props }) => (
                    <div className="my-4 overflow-x-auto rounded-lg border border-border/50">
                      <table className="w-full text-sm" {...props}>{children}</table>
                    </div>
                  ),
                  th: ({ node, children, ...props }) => (
                    <th className="bg-muted/40 px-3 py-2 text-left font-semibold border-b border-border/50" {...props}>{children}</th>
                  ),
                  td: ({ node, children, ...props }) => (
                    <td className="px-3 py-2 border-b border-border/30" {...props}>{children}</td>
                  )
                }}
              >
                {displayContent}
              </ReactMarkdown>

              {message.attachments && message.attachments.length > 0 && (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {message.attachments.map((att, idx) => (
                    <div key={idx} className="rounded-md overflow-hidden border bg-background/60">
                      {att.preview ? (
                        <img
                          src={att.preview}
                          alt={att.name || `attachment-${idx}`}
                          className="w-full h-auto max-h-40 object-cover bg-white"
                        />
                      ) : (
                        <div className="p-3 text-xs text-muted-foreground">
                          {att.name || 'Image attachment'}
                        </div>
                      )}
                      <div className="px-2 py-1 text-[10px] text-muted-foreground truncate border-t border-border/50">
                        {att.name || att.mime || 'Image'}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Intentionally no typing indicator here; the Thinking row covers streaming state. */}
            </div>

            {/* Actions: Copy & Edit */}
            <div className="absolute -bottom-6 right-0 opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
              {/* Copy Button - Available for both roles */}
              {message.content && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-foreground"
                  onClick={handleCopy}
                >
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                </Button>
              )}

              {isUser && onEdit && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-foreground ml-auto"
                  onClick={() => setIsEditing(true)}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export const MessageItem = memo(MessageItemBase)
