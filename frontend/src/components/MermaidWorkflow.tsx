import { useEffect, useRef, useState, useMemo } from 'react'
import mermaid from 'mermaid'
import type { RavenWorkflowEvent } from '../types'
import { Activity, AlertTriangle } from 'lucide-react'

interface Props {
  events?: RavenWorkflowEvent[]
  activeStep?: string
  isRunning?: boolean
  coverageScore?: number
  roundCount?: number
}

export function MermaidWorkflow({
  events = [],
  activeStep,
  isRunning = false,
  coverageScore,
  roundCount = 1,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svgContent, setSvgContent] = useState<string>('')
  const [renderError, setRenderError] = useState<string | null>(null)

  // Derive node states from persisted backend events
  const nodeStates = useMemo(() => {
    const states: Record<string, 'idle' | 'running' | 'completed' | 'failed'> = {
      analyzer: 'idle',
      memory_retriever: 'idle',
      planner: 'idle',
      source_tunnels: 'idle',
      critic: 'idle',
      gap_planner: 'idle',
      synthesizer: 'idle',
      finalizer: 'idle',
    }

    for (const ev of events) {
      const step = ev.step
      if (states[step] !== undefined) {
        if (ev.status === 'running') states[step] = 'running'
        else if (ev.status === 'completed') states[step] = 'completed'
        else if (ev.status === 'failed') states[step] = 'failed'
      }
    }

    if (isRunning && activeStep && states[activeStep] !== undefined) {
      states[activeStep] = 'running'
    }

    return states
  }, [events, activeStep, isRunning])

  // Generate dynamic Mermaid diagram definition
  const mermaidDefinition = useMemo(() => {
    const getClass = (name: string) => {
      const st = nodeStates[name] || 'idle'
      if (st === 'running') return 'stRunning'
      if (st === 'completed') return 'stDone'
      if (st === 'failed') return 'stFailed'
      return 'stIdle'
    }

    return `
flowchart TD
    classDef stIdle fill:#121520,stroke:#2a3147,stroke-width:1px,color:#94a3b8;
    classDef stRunning fill:#0f2744,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;
    classDef stDone fill:#0d281e,stroke:#10b981,stroke-width:2px,color:#f8fafc;
    classDef stFailed fill:#361414,stroke:#ef4444,stroke-width:2px,color:#f8fafc;

    A["1. Analyzer: Intent & Goals"]:::${getClass('analyzer')}
    B["2. Safe Memory Retriever"]:::${getClass('memory_retriever')}
    C["3. Planner: Objectives & Criteria"]:::${getClass('planner')}
    D["4. Parallel Source Tunnels"]:::${getClass('source_tunnels')}
    E["5. Critic: Coverage & Gaps"]:::${getClass('critic')}
    F{"Gaps & Budget Remains?"}:::${getClass('critic')}
    G["6. Synthesizer: Evidence Report"]:::${getClass('synthesizer')}
    H["7. Finalizer & Memory Update"]:::${getClass('finalizer')}

    A --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F -- "Material Gaps (Round ${roundCount})" --> D
    F -- "Budget / Score Met" --> G
    G --> H
`
  }, [nodeStates, roundCount])

  useEffect(() => {
    let isMounted = true

    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'strict',
      flowchart: {
        curve: 'basis',
        htmlLabels: true,
        useMaxWidth: true,
      },
    })

    const uniqueId = `mermaid-workflow-${Date.now()}`

    mermaid
      .render(uniqueId, mermaidDefinition)
      .then(({ svg }) => {
        if (isMounted) {
          setSvgContent(svg)
          setRenderError(null)
        }
      })
      .catch((err) => {
        if (isMounted) {
          console.error('Mermaid render error:', err)
          setRenderError(String(err))
        }
      })

    return () => {
      isMounted = false
    }
  }, [mermaidDefinition])

  return (
    <div className="rounded-xl border border-border/80 bg-surface/90 backdrop-blur-md p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between border-b border-border/60 pb-3">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-accent" />
          <span className="text-xs font-mono font-semibold tracking-wider text-muted uppercase">
            Live Agent Workflow Architecture
          </span>
        </div>
        <div className="flex items-center gap-4 text-xs font-mono text-muted">
          {isRunning && (
            <span className="flex items-center gap-1.5 text-accent animate-pulse">
              <span className="w-2 h-2 rounded-full bg-accent animate-ping" />
              Active: {activeStep || 'orchestrating'}
            </span>
          )}
          {coverageScore !== undefined && (
            <span className="text-emerald-400">
              Coverage: {Math.round(coverageScore * 100)}%
            </span>
          )}
          <span>Round {roundCount}/3</span>
        </div>
      </div>

      <div
        ref={containerRef}
        className="relative min-h-[300px] w-full flex items-center justify-center overflow-x-auto py-2"
      >
        {renderError ? (
          <div className="flex items-center gap-2 text-xs font-mono text-amber-400">
            <AlertTriangle className="w-4 h-4" />
            <span>Workflow diagram render notice: diagram fallback active</span>
          </div>
        ) : (
          <div
            className="w-full flex justify-center [&>svg]:max-w-full [&>svg]:h-auto"
            dangerouslySetInnerHTML={{ __html: svgContent }}
          />
        )}
      </div>

      <div className="flex items-center justify-between pt-2 border-t border-border/40 text-[11px] font-mono text-muted">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-slate-600" /> Idle
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-sky-400" /> Running
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400" /> Completed
          </span>
        </div>
        <span>Mermaid Engine v11 · Event-Driven State</span>
      </div>
    </div>
  )
}
