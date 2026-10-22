import { XCircle, Loader2, Terminal } from 'lucide-react'
import { useState, useEffect } from 'react'
import { MermaidWorkflow } from './MermaidWorkflow'
import type { RavenWorkflowEvent } from '../types'

interface Props {
  onStop?: () => void
  topic?: string
  isReconnecting?: boolean
  events?: RavenWorkflowEvent[]
}

export function LoadingState({ onStop, topic, isReconnecting, events: externalEvents }: Props) {
  const [activeStep, setActiveStep] = useState<string>('analyzer')
  const [events, setEvents] = useState<RavenWorkflowEvent[]>(externalEvents || [])
  const [round, setRound] = useState<number>(1)
  const [coverageScore, setCoverageScore] = useState<number | undefined>(undefined)

  useEffect(() => {
    if (externalEvents && externalEvents.length > events.length) {
      setEvents(externalEvents)
      const last = externalEvents[externalEvents.length - 1]
      if (last?.step) setActiveStep(last.step)
    }
  }, [externalEvents])

  useEffect(() => {
    const handleProgress = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (detail && detail.step) {
        setActiveStep(detail.step)
      }
    }

    const handleWorkflowEvent = (e: Event) => {
      const ev = (e as CustomEvent).detail as RavenWorkflowEvent
      if (ev) {
        setEvents((prev) => [...prev, ev])
        if (ev.step) setActiveStep(ev.step)
        if (ev.details?.coverage_score !== undefined) {
          setCoverageScore(ev.details.coverage_score)
        }
        if (ev.details?.round) {
          setRound(ev.details.round)
        }
      }
    }

    window.addEventListener('research-progress', handleProgress)
    window.addEventListener('raven-workflow-event', handleWorkflowEvent)
    return () => {
      window.removeEventListener('research-progress', handleProgress)
      window.removeEventListener('raven-workflow-event', handleWorkflowEvent)
    }
  }, [])

  return (
    <div className="py-6 space-y-5 max-w-4xl mx-auto">
      {/* Header Bar */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2.5">
          <Loader2 className={`w-4 h-4 ${isReconnecting ? 'text-amber-400' : 'text-sky-400'} animate-spin`} />
          <span className="text-sm font-semibold tracking-wide text-slate-100">
            {isReconnecting ? 'Reconnecting to Research Stream...' : 'RAVEN Orchestration Active'}
          </span>
          <span className="text-xs font-mono px-2 py-0.5 rounded bg-sky-950/60 border border-sky-800/60 text-sky-300">
            Node: {activeStep}
          </span>
          {topic && (
            <span className="hidden md:inline-block text-xs font-medium text-slate-400 truncate max-w-[280px]">
              “{topic}”
            </span>
          )}
        </div>

        {onStop && (
          <button
            onClick={onStop}
            className="flex items-center gap-1.5 text-xs text-rose-400 hover:text-rose-300 transition-colors px-3 py-1.5 rounded-lg border border-rose-900/60 bg-rose-950/20 hover:bg-rose-950/40"
          >
            <XCircle className="w-3.5 h-3.5" />
            Cancel Run
          </button>
        )}
      </div>

      {/* Live Mermaid Flowchart */}
      <MermaidWorkflow
        events={events}
        activeStep={activeStep}
        isRunning={true}
        coverageScore={coverageScore}
        roundCount={round}
      />

      {/* Real-time Backend Event Ledger */}
      <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4 font-mono text-xs">
        <div className="flex items-center gap-2 text-slate-400 border-b border-slate-800/80 pb-2 mb-2">
          <Terminal className="w-3.5 h-3.5" />
          <span className="text-[11px] uppercase tracking-wider font-semibold">
            Persisted Event Stream Journal ({events.length} events)
          </span>
        </div>
        <div className="max-h-48 overflow-y-auto space-y-1.5 pr-2">
          {events.length === 0 ? (
            <p className="text-slate-600 italic">Initializing LangGraph workflow...</p>
          ) : (
            events.map((ev, idx) => (
              <div key={idx} className="flex items-start gap-2 text-[11px]">
                <span className="text-slate-500 shrink-0">#{ev.seq || idx + 1}</span>
                <span
                  className={`px-1.5 py-0.2 rounded text-[10px] shrink-0 font-semibold ${
                    ev.status === 'completed'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/50'
                      : ev.status === 'running'
                      ? 'bg-sky-950 text-sky-400 border border-sky-800/50'
                      : 'bg-slate-900 text-slate-400'
                  }`}
                >
                  {ev.step}
                </span>
                <span className="text-slate-300 break-all">
                  {JSON.stringify(ev.details)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
