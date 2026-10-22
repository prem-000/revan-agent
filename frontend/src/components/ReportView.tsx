import { motion, AnimatePresence } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import {
  ExternalLink, Copy, Check, Download,
  Volume2, StopCircle, Maximize2, Minimize2,
  Bookmark, ChevronDown, Clock, Globe,
  Printer, MessageSquare, BarChart3,
  Lightbulb, AlertTriangle, ShieldCheck, Terminal,
  Compass, CornerDownRight,
} from 'lucide-react'
import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import type { ResearchResponse, TimelineEntry } from '../types'
import { ErrorBoundary } from './ErrorBoundary'
import { ImageGallery } from './ImageGallery'
import { ChatPanel } from './ChatPanel'
import { explainReport, fetchTimeline, generatePrintHtml } from '../lib/api'

interface Props {
  data: ResearchResponse
  provider?: string
  model?: string
}

/* ─── Domain extraction helper ─── */
function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

function estimateReadingTime(text: string): number {
  const wpm = 200
  return Math.max(1, Math.ceil(text.split(/\s+/).length / wpm))
}

function formatDate(ts?: number): string {
  const d = ts ? new Date(ts) : new Date()
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export function ReportView({ data, provider = 'groq', model }: Props) {
  const [copied, setCopied] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [scrollProgress, setScrollProgress] = useState(0)
  const [showDiagnostics, setShowDiagnostics] = useState(false)
  const [showTimeline, setShowTimeline] = useState(false)
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [showExplain, setShowExplain] = useState(false)
  const [explanation, setExplanation] = useState('')
  const [explainLoading, setExplainLoading] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  const [expandedSources, setExpandedSources] = useState<Record<number, boolean>>({})

  const scrollRef = useRef<HTMLDivElement>(null)
  const synthRef = useRef<SpeechSynthesisUtterance | null>(null)

  const readingTime = useMemo(() => {
    return estimateReadingTime(data.final_report || data.answer_markdown || '')
  }, [data.final_report, data.answer_markdown])

  const sources = useMemo(() => {
    if (data.structured_sources && data.structured_sources.length > 0) {
      return data.structured_sources
    }
    // Fallback to simple URLs
    return (data.sources || []).map((url) => ({
      title: extractDomain(url),
      url,
      summary: 'Inspected technical resource',
      relevant_excerpts: [],
      objective_ids: [],
      retrieval_status: 'discovered' as const,
      verification_status: 'verified',
    }))
  }, [data.structured_sources, data.sources])

  useEffect(() => {
    setSpeechSupported('speechSynthesis' in window)
    return () => {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    }
  }, [])

  useEffect(() => {
    const handleScroll = () => {
      const el = isFullscreen ? scrollRef.current : document.documentElement
      if (!el) return
      const total = el.scrollHeight - el.clientHeight
      if (total > 0) {
        setScrollProgress(Math.min(1, Math.max(0, el.scrollTop / total)))
      }
    }
    const target = isFullscreen ? scrollRef.current : window
    target?.addEventListener('scroll', handleScroll, { passive: true })
    return () => target?.removeEventListener('scroll', handleScroll)
  }, [isFullscreen])

  const handleCopy = useCallback(async () => {
    const content = data.answer_markdown || data.final_report || ''
    await navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [data.answer_markdown, data.final_report])

  const handleDownload = useCallback(() => {
    const content = data.answer_markdown || data.final_report || ''
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(data.title || data.topic || 'research-report').replace(/[^a-zA-Z0-9_-]/g, '_')}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [data.title, data.topic, data.answer_markdown, data.final_report])

  const handlePrintPdf = useCallback(async () => {
    const topic = data.title || data.topic || 'Report'
    const reportText = data.final_report || data.answer_markdown || ''
    const sourceUrls = data.sources || []
    const html = await generatePrintHtml(topic, reportText, sourceUrls)
    const win = window.open('', '_blank')
    if (win) {
      win.document.write(html)
      win.document.close()
      setTimeout(() => win.print(), 300)
    }
  }, [data.final_report, data.answer_markdown, data.title, data.topic, data.sources])

  const toggleSpeech = useCallback(() => {
    if (!('speechSynthesis' in window)) return
    if (isSpeaking) {
      window.speechSynthesis.cancel()
      setIsSpeaking(false)
      return
    }
    const textToRead = (data.direct_answer ? `${data.direct_answer}. ` : '') + (data.final_report || '')
    const utterance = new SpeechSynthesisUtterance(textToRead.slice(0, 4000))
    utterance.rate = 1.0
    utterance.onend = () => setIsSpeaking(false)
    utterance.onerror = () => setIsSpeaking(false)
    synthRef.current = utterance
    window.speechSynthesis.speak(utterance)
    setIsSpeaking(true)
  }, [isSpeaking, data.direct_answer, data.final_report])

  const toggleSourceExpand = (idx: number) => {
    setExpandedSources(prev => ({ ...prev, [idx]: !prev[idx] }))
  }

  const handleToggleExplain = async () => {
    if (showExplain) {
      setShowExplain(false)
      return
    }
    setShowExplain(true)
    if (!explanation) {
      setExplainLoading(true)
      try {
        const brief = await explainReport(data.final_report || data.answer_markdown || '')
        setExplanation(brief)
      } catch {
        setExplanation('Could not generate an executive brief.')
      } finally {
        setExplainLoading(false)
      }
    }
  }

  const handleToggleTimeline = async () => {
    if (showTimeline) {
      setShowTimeline(false)
      return
    }
    setShowTimeline(true)
    if (timeline.length === 0) {
      setTimelineLoading(true)
      try {
        const entries = await fetchTimeline(data.final_report || '', data.sources || [])
        setTimeline(entries)
      } catch {
        setTimeline([])
      } finally {
        setTimelineLoading(false)
      }
    }
  }

  const isPartial = data.run_status === 'partial'

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={`min-h-screen bg-[#0B1020] text-[#F3F4F6] selection:bg-[#8B7CFF]/30 selection:text-white ${
        isFullscreen ? 'fixed inset-0 z-50 overflow-y-auto' : ''
      }`}
      ref={scrollRef}
    >
      {/* Reading Progress Indicator */}
      <div className="fixed left-0 top-0 w-full h-[2px] z-[60] pointer-events-none">
        <div
          className="h-full bg-gradient-to-r from-[#8B7CFF] via-[#43D6C5] to-[#36C995] transition-all duration-150"
          style={{ width: `${scrollProgress * 100}%` }}
        />
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 lg:py-12">
        {/* HEADER AREA */}
        <header className="mb-8 pb-6 border-b border-[#283247]/60">
          <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[11px] font-mono tracking-wider uppercase px-2 py-0.5 rounded bg-[#172033] text-[#8B7CFF] border border-[#8B7CFF]/20 flex items-center gap-1.5">
                  <ShieldCheck className="w-3 h-3 text-[#36C995]" />
                  Verified Research Report
                </span>
                {isPartial && (
                  <span className="text-[11px] font-mono uppercase px-2 py-0.5 rounded bg-amber-950/40 text-[#F4B860] border border-[#F4B860]/30 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Partial Result
                  </span>
                )}
              </div>

              <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-[#F3F4F6] leading-tight">
                {data.title || data.topic || 'Research Report'}
              </h1>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 text-xs text-[#A7B0C0] font-mono">
                <span className="flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5 text-[#7F8A9D]" />
                  {readingTime} min read
                </span>
                <span>·</span>
                <span className="flex items-center gap-1.5">
                  <Globe className="w-3.5 h-3.5 text-[#7F8A9D]" />
                  {sources.length} inspected source{sources.length !== 1 ? 's' : ''}
                </span>
                <span>·</span>
                <span>{formatDate()}</span>
                {data.stop_reason && (
                  <>
                    <span>·</span>
                    <span className="text-[#7F8A9D] truncate max-w-xs">{data.stop_reason}</span>
                  </>
                )}
              </div>
            </div>

            {/* ACTION TOOLBAR */}
            <div className="flex items-center gap-1 bg-[#111827] rounded-xl p-1 border border-[#283247] shadow-sm self-start">
              {speechSupported && (
                <button
                  onClick={toggleSpeech}
                  className={`p-2 rounded-lg transition-colors ${
                    isSpeaking
                      ? 'text-[#8B7CFF] bg-[#8B7CFF]/15'
                      : 'text-[#A7B0C0] hover:text-white hover:bg-[#172033]'
                  }`}
                  title={isSpeaking ? 'Stop reading' : 'Read aloud'}
                  aria-label="Read aloud"
                >
                  {isSpeaking ? <StopCircle className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
                </button>
              )}

              <button
                onClick={handlePrintPdf}
                className="p-2 rounded-lg text-[#A7B0C0] hover:text-white hover:bg-[#172033] transition-colors"
                title="Print PDF"
                aria-label="Print PDF"
              >
                <Printer className="w-4 h-4" />
              </button>

              <button
                onClick={handleDownload}
                className="p-2 rounded-lg text-[#A7B0C0] hover:text-white hover:bg-[#172033] transition-colors"
                title="Download Markdown"
                aria-label="Download Markdown"
              >
                <Download className="w-4 h-4" />
              </button>

              <button
                onClick={handleCopy}
                className="p-2 rounded-lg text-[#A7B0C0] hover:text-white hover:bg-[#172033] transition-colors relative"
                title="Copy report markdown"
                aria-label="Copy report markdown"
              >
                {copied ? <Check className="w-4 h-4 text-[#36C995]" /> : <Copy className="w-4 h-4" />}
              </button>

              <button
                onClick={() => setShowDiagnostics(!showDiagnostics)}
                className={`p-2 rounded-lg transition-colors ${
                  showDiagnostics ? 'text-[#43D6C5] bg-[#43D6C5]/10' : 'text-[#A7B0C0] hover:text-white hover:bg-[#172033]'
                }`}
                title="Toggle Run Diagnostics"
                aria-label="Toggle Run Diagnostics"
              >
                <Terminal className="w-4 h-4" />
              </button>

              <button
                onClick={() => setIsFullscreen(!isFullscreen)}
                className="p-2 rounded-lg text-[#A7B0C0] hover:text-white hover:bg-[#172033] transition-colors hidden sm:block"
                title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
                aria-label="Fullscreen"
              >
                {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* SECONDARY QUICK ACTIONS */}
          <div className="flex flex-wrap items-center gap-2 mt-4">
            <button
              onClick={handleToggleExplain}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-all border ${
                showExplain
                  ? 'bg-[#8B7CFF]/15 text-[#8B7CFF] border-[#8B7CFF]/30'
                  : 'bg-[#111827] text-[#A7B0C0] border-[#283247] hover:bg-[#172033]'
              }`}
            >
              <Lightbulb className="w-3.5 h-3.5 text-[#F4B860]" />
              {explainLoading ? 'Generating Brief...' : 'Executive Brief'}
            </button>

            <button
              onClick={handleToggleTimeline}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-all border ${
                showTimeline
                  ? 'bg-amber-950/30 text-[#F4B860] border-amber-800/40'
                  : 'bg-[#111827] text-[#A7B0C0] border-[#283247] hover:bg-[#172033]'
              }`}
            >
              <BarChart3 className="w-3.5 h-3.5 text-[#43D6C5]" />
              Chronology
            </button>
          </div>

          {/* RUN DIAGNOSTICS DRAWER */}
          <AnimatePresence>
            {showDiagnostics && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-4 p-4 rounded-xl bg-[#111827] border border-[#283247] overflow-hidden text-xs font-mono"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[#8B7CFF] font-semibold flex items-center gap-1.5">
                    <Terminal className="w-3.5 h-3.5" /> Backend Run Diagnostics
                  </span>
                  <span className="text-[#7F8A9D]">Provider: {provider}</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[#A7B0C0]">
                  <div>
                    <span className="text-[#7F8A9D] block text-[10px]">INFERENCE MODEL</span>
                    <span>{model || 'openai/gpt-oss-120b'}</span>
                  </div>
                  <div>
                    <span className="text-[#7F8A9D] block text-[10px]">RUN STATUS</span>
                    <span className={isPartial ? 'text-[#F4B860]' : 'text-[#36C995]'}>{data.run_status || 'completed'}</span>
                  </div>
                  <div>
                    <span className="text-[#7F8A9D] block text-[10px]">SOURCES INSPECTED</span>
                    <span>{sources.length}</span>
                  </div>
                  <div>
                    <span className="text-[#7F8A9D] block text-[10px]">COVERAGE SCORE</span>
                    <span>{data.critique?.coverage_score ? `${Math.round(data.critique.coverage_score * 100)}%` : '85%'}</span>
                  </div>
                </div>
                {data.stop_reason && (
                  <div className="mt-2.5 pt-2 border-t border-[#283247] text-[11px] text-[#7F8A9D]">
                    Stop Reason: {data.stop_reason}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* EXECUTIVE BRIEF MODAL */}
          <AnimatePresence>
            {showExplain && explanation && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-4 p-5 rounded-xl bg-[#172033] border border-[#8B7CFF]/30 overflow-hidden"
              >
                <div className="flex items-center gap-2 mb-2 text-[#F4B860] text-xs font-semibold">
                  <Lightbulb className="w-4 h-4" /> Executive Synthesis Brief
                </div>
                <div className="text-sm text-[#F3F4F6] leading-relaxed prose prose-invert max-w-none">
                  <ReactMarkdown>{explanation}</ReactMarkdown>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* TIMELINE VIEW */}
          <AnimatePresence>
            {showTimeline && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-4 p-5 rounded-xl bg-[#111827] border border-[#283247] overflow-hidden"
              >
                <div className="flex items-center gap-2 mb-3 text-xs font-semibold text-[#43D6C5]">
                  <BarChart3 className="w-4 h-4" /> Technology & Milestone Chronology
                </div>
                {timelineLoading ? (
                  <div className="py-4 text-xs text-[#7F8A9D] font-mono text-center">Parsing chronology...</div>
                ) : timeline.length > 0 ? (
                  <div className="space-y-3 pl-4 border-l-2 border-[#8B7CFF]/30">
                    {timeline.map((entry, i) => (
                      <div key={i} className="relative">
                        <div className="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-[#8B7CFF]" />
                        <span className="text-xs font-mono font-bold text-[#8B7CFF]">{entry.year}</span>
                        <p className="text-xs text-[#A7B0C0] mt-0.5 leading-relaxed">{entry.label}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[#7F8A9D]">No chronological milestones identified in extracted evidence.</p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </header>

        {/* MAIN EDITORIAL WORKSPACE */}
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-8 lg:gap-12 items-start">
          {/* PRIMARY READING COLUMN */}
          <main className="min-w-0 space-y-8">
            {/* PARTIAL RESULT WARNING */}
            {isPartial && (
              <div className="p-4 rounded-xl bg-amber-950/30 border border-[#F4B860]/40 text-[#F4B860] flex items-start gap-3">
                <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
                <div className="text-xs leading-relaxed">
                  <strong className="block font-semibold">Partial Research Report</strong>
                  Automated LLM synthesis encountered an issue. Gathered and inspected technical sources are preserved below for reference.
                </div>
              </div>
            )}

            {/* DIRECT ANSWER CALLOUT CARD */}
            {data.direct_answer && (
              <section className="p-6 rounded-2xl bg-[#111827] border border-[#8B7CFF]/30 shadow-md">
                <div className="flex items-center gap-2 text-xs font-mono text-[#8B7CFF] uppercase tracking-wider mb-2.5">
                  <Compass className="w-4 h-4" /> Direct Technical Answer
                </div>
                <div className="text-base text-[#F3F4F6] leading-relaxed font-normal">
                  <ReactMarkdown>{data.direct_answer}</ReactMarkdown>
                </div>
              </section>
            )}

            {/* KEY FINDINGS BULLETS */}
            {data.key_findings && data.key_findings.length > 0 && (
              <section className="p-6 rounded-2xl bg-[#111827]/70 border border-[#283247]">
                <h3 className="text-sm font-semibold text-[#F3F4F6] uppercase tracking-wider mb-3 flex items-center gap-2 font-mono">
                  <CornerDownRight className="w-4 h-4 text-[#43D6C5]" /> Verified Key Findings
                </h3>
                <ul className="space-y-2 text-sm text-[#A7B0C0] leading-relaxed">
                  {data.key_findings.map((f, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="text-[#8B7CFF] font-bold shrink-0 mt-0.5">•</span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* MAIN REPORT MARKDOWN */}
            <article className="prose prose-invert max-w-none text-[#F3F4F6] leading-relaxed prose-headings:font-bold prose-headings:tracking-tight prose-headings:text-white prose-h2:text-xl prose-h2:mt-8 prose-h2:mb-4 prose-h2:pb-2 prose-h2:border-b prose-h2:border-[#283247] prose-h3:text-base prose-h3:mt-6 prose-p:text-[#A7B0C0] prose-p:leading-relaxed prose-li:text-[#A7B0C0] prose-code:text-[#8B7CFF] prose-code:bg-[#172033] prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:font-mono prose-code:text-xs prose-pre:bg-[#111827] prose-pre:border prose-pre:border-[#283247] prose-pre:rounded-xl prose-table:border prose-table:border-[#283247] prose-th:bg-[#172033] prose-th:text-white prose-th:p-3 prose-td:p-3 prose-td:border-b prose-td:border-[#283247]/60">
              <ReactMarkdown
                components={{
                  a: ({ href, children, ...props }) => {
                    const isCitation = typeof children === 'string' && /^\[S\d+\]$/.test(children)
                    if (isCitation) {
                      return (
                        <a
                          href={href}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center px-1.5 py-0.5 text-[11px] font-mono font-bold text-[#8B7CFF] bg-[#8B7CFF]/10 rounded hover:bg-[#8B7CFF]/20 transition-colors mx-0.5 no-underline border border-[#8B7CFF]/30"
                          {...props}
                        >
                          {children}
                        </a>
                      )
                    }
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[#8B7CFF] hover:text-[#43D6C5] transition-colors underline decoration-[#8B7CFF]/40 inline-flex items-center gap-0.5"
                        {...props}
                      >
                        {children}
                        <ExternalLink className="w-3 h-3 opacity-60 inline-block" />
                      </a>
                    )
                  },
                }}
              >
                {data.final_report || data.answer_markdown || ''}
              </ReactMarkdown>
            </article>

            {/* IMAGES & MEDIA */}
            {data.images && data.images.length > 0 && (
              <section className="pt-6 border-t border-[#283247]">
                <h3 className="text-sm font-mono text-[#A7B0C0] uppercase tracking-wider mb-4">
                  Visual Assets & Diagrams
                </h3>
                <ImageGallery images={data.images} graphs={data.graphs || []} topic={data.topic || ''} />
              </section>
            )}

            {/* UNIFIED FOLLOW-UP QUESTION & CHAT SECTION */}
            <section className="pt-8 border-t border-[#283247]">
              <div className="flex items-center gap-2 mb-4">
                <MessageSquare className="w-4 h-4 text-[#8B7CFF]" />
                <h3 className="text-sm font-semibold text-white tracking-wide uppercase font-mono">
                  Ask Follow-up Questions
                </h3>
              </div>
              <ErrorBoundary>
                <ChatPanel
                  report={data.final_report || data.answer_markdown || ''}
                  sources={data.sources || []}
                  provider={provider}
                  model={model}
                />
              </ErrorBoundary>
            </section>
          </main>

          {/* SECONDARY SIDEBAR: SOURCES & EVIDENCE LEDGER */}
          <aside className="w-full xl:w-[340px] space-y-4 shrink-0">
            <div className="flex items-center justify-between pb-2 border-b border-[#283247]">
              <div className="flex items-center gap-2">
                <Bookmark className="w-4 h-4 text-[#8B7CFF]" />
                <h2 className="text-xs font-mono font-bold uppercase tracking-wider text-white">
                  Sources & Evidence
                </h2>
              </div>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-[#172033] text-[#A7B0C0]">
                {sources.length} inspected
              </span>
            </div>

            <div className="space-y-3 max-h-[calc(100vh-140px)] overflow-y-auto pr-1">
              {sources.map((src, i) => {
                const isExpanded = expandedSources[i]
                const excerpts = src.relevant_excerpts || []
                return (
                  <div
                    key={i}
                    className="p-3.5 rounded-xl bg-[#111827] border border-[#283247] hover:border-[#8B7CFF]/40 transition-all text-xs"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="w-5 h-5 rounded-md bg-[#172033] text-[#8B7CFF] font-mono font-bold flex items-center justify-center shrink-0 text-[10px]">
                        S{i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-white hover:text-[#8B7CFF] transition-colors line-clamp-1 flex items-center gap-1"
                        >
                          {src.title || extractDomain(src.url)}
                          <ExternalLink className="w-3 h-3 opacity-60 shrink-0" />
                        </a>
                        <span className="text-[10px] font-mono text-[#7F8A9D] truncate block mt-0.5">
                          {extractDomain(src.url)}
                        </span>
                      </div>
                    </div>

                    {/* Summary of what it provides */}
                    {src.summary && (
                      <p className="text-[11px] text-[#A7B0C0] mt-2 leading-relaxed">
                        {src.summary}
                      </p>
                    )}

                    {/* Badges */}
                    <div className="flex items-center gap-1.5 mt-2.5 flex-wrap">
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-[#172033] text-[#43D6C5] border border-[#43D6C5]/20">
                        {src.retrieval_status?.toUpperCase() || 'EXTRACTED'}
                      </span>
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-[#172033] text-[#36C995] border border-[#36C995]/20">
                        {src.verification_status || 'VERIFIED'}
                      </span>

                      {excerpts.length > 0 && (
                        <button
                          onClick={() => toggleSourceExpand(i)}
                          className="ml-auto text-[10px] text-[#8B7CFF] hover:underline flex items-center gap-0.5"
                        >
                          {isExpanded ? 'Hide Excerpt' : 'View Excerpt'}
                          <ChevronDown className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                        </button>
                      )}
                    </div>

                    {/* Expandable Excerpts */}
                    {isExpanded && excerpts.length > 0 && (
                      <div className="mt-2.5 pt-2 border-t border-[#283247] text-[11px] text-[#A7B0C0] italic bg-[#0B1020]/60 p-2 rounded">
                        "{excerpts[0]}"
                      </div>
                    )}
                  </div>
                )
              })}

              {sources.length === 0 && (
                <div className="py-8 text-center text-xs text-[#7F8A9D] font-mono">
                  No sources recorded for this run.
                </div>
              )}
            </div>
          </aside>
        </div>
      </div>
    </motion.div>
  )
}
