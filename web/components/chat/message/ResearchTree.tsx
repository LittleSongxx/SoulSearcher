'use client'

import React from 'react'
import {
  ChevronRight,
  CheckCircle2,
  Circle,
  CircleDot,
  Loader2,
  Search,
} from 'lucide-react'

interface TreeNode {
  id: string
  name: string
  status: 'pending' | 'running' | 'completed' | 'error'
  thoroughness?: string
  result_preview?: string
  children?: TreeNode[]
}

function TreeNodeItem({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  const hasChildren = node.children && node.children.length > 0

  const statusIcon = () => {
    switch (node.status) {
      case 'running':
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
      case 'completed':
        return <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
      case 'error':
        return <Circle className="h-3.5 w-3.5 text-red-500" />
      default:
        return <CircleDot className="h-3.5 w-3.5 text-muted-foreground/40" />
    }
  }

  const thoroughnessLabel = (t: string) => {
    switch (t) {
      case 'quick':
        return 'Quick'
      case 'medium':
        return 'Standard'
      case 'very_thorough':
        return 'Deep'
      default:
        return t
    }
  }

  return (
    <div className="tree-node">
      <div
        className="flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-accent/50 transition-colors"
        style={{ marginLeft: depth * 20 }}
      >
        {statusIcon()}
        <Search className="h-3.5 w-3.5 text-muted-foreground/50 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium truncate">{node.name}</div>
          {node.thoroughness && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>{thoroughnessLabel(node.thoroughness)}</span>
              {node.result_preview && (
                <span className="truncate">
                  &mdash; {node.result_preview}
                </span>
              )}
            </div>
          )}
        </div>
        {hasChildren && (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" />
        )}
      </div>
      {hasChildren &&
        node.children!.map((child) => (
          <TreeNodeItem key={child.id} node={child} depth={depth + 1} />
        ))}
    </div>
  )
}

export function ResearchTreeView({ data }: { data: any }) {
  const tree = data?.tree

  if (!tree) {
    return (
      <div className="flex items-start gap-2 py-1">
        <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-muted-foreground/60" />
        <span className="text-sm text-muted-foreground">
          Research tree updating...
        </span>
      </div>
    )
  }

  // Flatten tree for display: root + all children in order
  const allNodes: TreeNode[] = [tree, ...(tree.children || [])]

  return (
    <div className="research-tree my-2 border rounded-lg bg-card/50 p-3">
      <div className="flex items-center gap-2 mb-2 pb-2 border-b border-border/50">
        <div
          className={`h-2 w-2 rounded-full ${
            tree.status === 'running'
              ? 'bg-blue-500 animate-pulse'
              : tree.status === 'completed'
                ? 'bg-green-500'
                : 'bg-muted-foreground/30'
          }`}
        />
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          Research Tree
        </span>
        <span className="text-xs text-muted-foreground/60">
          {allNodes.length} node{allNodes.length !== 1 ? 's' : ''}
          {tree.status === 'completed' ? ' • Complete' : ' • In Progress'}
        </span>
      </div>

      <div className="space-y-0.5">
        {/* Root node */}
        <div className="flex items-center gap-2 py-1.5 px-2">
          {tree.status === 'running' ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
          ) : tree.status === 'completed' ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          ) : (
            <CircleDot className="h-3.5 w-3.5 text-muted-foreground/40" />
          )}
          <span className="text-sm font-semibold truncate">
            {tree.name || 'Research'}
          </span>
        </div>

        {/* Divider before children */}
        {tree.children && tree.children.length > 0 && (
          <div className="ml-4 pl-4 border-l-2 border-border/60 space-y-0.5">
            {tree.children.map((child: TreeNode) => (
              <div key={child.id}>
                <TreeNodeItem node={child} depth={0} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
