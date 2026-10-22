import { useState, useEffect, useRef, useCallback } from 'react'
import type { RavenWorkflowEvent, ResearchRequest, ResearchResponse } from '../types'

export type HistoryItem = ResearchResponse & {
  id: string
  topic: string
  timestamp: number
  depth: number
  provider: string
  model?: string
}

const getApiBase = () => (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export function useResearch() {
  const [isLoading, setIsLoading] = useState(false)
  const [isReconnecting, setIsReconnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentResult, setCurrentResult] = useState<ResearchResponse | null>(null)
  const [workflowEvents, setWorkflowEvents] = useState<RavenWorkflowEvent[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeTopic, setActiveTopic] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [queueLength, setQueueLength] = useState(0)
  const [queuedTopics, setQueuedTopics] = useState<string[]>([])

  const queueRef = useRef<ResearchRequest[]>([])
  const processingRef = useRef(false)
  const abortControllerRef = useRef<AbortController | null>(null)
  const lastRequestRef = useRef<ResearchRequest | null>(null)
  const activeJobIdRef = useRef<string | null>(null)

  useEffect(() => {
    activeJobIdRef.current = activeJobId
  }, [activeJobId])

  useEffect(() => {
    const saved = localStorage.getItem('research_history')
    if (saved) {
      try {
        setHistory(JSON.parse(saved))
      } catch (e) {
        console.error('Failed to parse history', e)
      }
    }
  }, [])

  const saveToHistory = (item: HistoryItem) => {
    const newHistory = [item, ...history].slice(0, 50)
    setHistory(newHistory)
    localStorage.setItem('research_history', JSON.stringify(newHistory))
  }

  const clearHistory = () => {
    setHistory([])
    localStorage.removeItem('research_history')
  }

  const stopResearch = () => {
    const currentJid = activeJobIdRef.current
    if (currentJid) {
      fetch(`${getApiBase()}/runs/${currentJid}/cancel`, { method: 'POST' }).catch(() => {})
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setIsLoading(false)
    setIsReconnecting(false)
    processingRef.current = false
    queueRef.current = []
    setQueueLength(0)
    setQueuedTopics([])
    setError('Research stopped by user')
  }

  const resetResearch = useCallback(() => {
    setError(null)
    setCurrentResult(null)
    setActiveTopic(null)
    setActiveJobId(null)
    setWorkflowEvents([])
    setIsLoading(false)
    setIsReconnecting(false)
  }, [])

  const processStreamReader = async (
    reader: ReadableStreamDefaultReader<Uint8Array>,
    request: ResearchRequest
  ): Promise<boolean> => {
    const decoder = new TextDecoder()
    let buffer = ''
    let receivedResult = false

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''

        for (const block of chunks) {
          const lines = block.split('\n')
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))

                if (data.type === 'run_start') {
                  if (data.job_id) {
                    setActiveJobId(data.job_id)
                    activeJobIdRef.current = data.job_id
                  }
                } else if (data.type === 'progress') {
                  window.dispatchEvent(new CustomEvent('research-progress', { detail: data }))
                } else if (data.type === 'workflow_event') {
                  if (data.event) {
                    setWorkflowEvents((prev) => [...prev, data.event])
                    window.dispatchEvent(new CustomEvent('raven-workflow-event', { detail: data.event }))
                  }
                } else if (data.type === 'token') {
                  window.dispatchEvent(new CustomEvent('research-token', { detail: data }))
                } else if (data.type === 'result') {
                  const result = data.data
                  setCurrentResult(result)
                  receivedResult = true
                  if (result.events && Array.isArray(result.events)) {
                    setWorkflowEvents(result.events)
                  }
                  saveToHistory({
                    ...result,
                    id: crypto.randomUUID(),
                    topic: request.topic,
                    depth: request.max_depth || 3,
                    provider: request.provider,
                    model: request.model,
                    timestamp: Date.now(),
                  })
                } else if (data.type === 'error') {
                  throw new Error(data.message || 'Research run encountered an error')
                } else if (data.type === 'cancelled') {
                  throw new Error(data.message || 'Research run was cancelled')
                }
              } catch (e) {
                if (e instanceof Error && (e.message.includes('error') || e.message.includes('cancelled'))) {
                  throw e
                }
                console.warn('Failed to parse stream message', e)
              }
            }
          }
        }
      }
    } finally {
      try {
        reader.releaseLock()
      } catch {}
    }

    return receivedResult
  }

  const attemptReconnection = async (jobId: string, request: ResearchRequest): Promise<boolean> => {
    setIsReconnecting(true)
    console.info(`Attempting stream reconnection for job: ${jobId}`)

    // First check if the job already finished on the backend
    try {
      const statusRes = await fetch(`${getApiBase()}/runs/${jobId}/events`)
      if (statusRes.ok) {
        const journal = await statusRes.json()
        if (journal.result) {
          setCurrentResult(journal.result)
          if (journal.events) setWorkflowEvents(journal.events)
          setIsReconnecting(false)
          return true
        }
      }
    } catch {
      // Continue to stream reconnect
    }

    // Connect to the job's dedicated stream endpoint
    try {
      const res = await fetch(`${getApiBase()}/runs/${jobId}/stream`, {
        headers: { 'Accept': 'text/event-stream' },
        signal: abortControllerRef.current?.signal,
      })

      if (res.ok && res.body) {
        const reader = res.body.getReader()
        const completed = await processStreamReader(reader, request)
        setIsReconnecting(false)
        return completed
      }
    } catch (e) {
      console.warn('Reconnection failed', e)
    }

    setIsReconnecting(false)
    return false
  }

  const executeResearch = async (request: ResearchRequest) => {
    if (processingRef.current) {
      queueRef.current = [...queueRef.current, request]
      const topics = queueRef.current.map((r) => r.topic)
      setQueueLength(queueRef.current.length)
      setQueuedTopics(topics)
      return
    }

    processingRef.current = true
    setIsLoading(true)
    setIsReconnecting(false)
    setError(null)
    setCurrentResult(null)
    setWorkflowEvents([])
    setActiveTopic(request.topic)
    lastRequestRef.current = request

    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    abortControllerRef.current = new AbortController()

    try {
      const response = await fetch(`${getApiBase()}/research/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: abortControllerRef.current.signal,
      })

      if (!response.ok) throw new Error(`Backend returned status ${response.status}`)
      if (!response.body) throw new Error('ReadableStream not supported')

      const reader = response.body.getReader()
      const completed = await processStreamReader(reader, request)

      // If the stream closed without delivering a result and without cancellation:
      if (!completed && activeJobIdRef.current && !abortControllerRef.current.signal.aborted) {
        console.warn('Stream closed without result. Attempting automatic reconnection...')
        const reconnected = await attemptReconnection(activeJobIdRef.current, request)
        if (!reconnected && !currentResult) {
          throw new Error('Research stream connection was interrupted. Click Retry to continue.')
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('Research stream aborted by client')
        return
      }
      const msg = err instanceof Error ? err.message : 'Unknown error occurred'
      setError(msg)
    } finally {
      setIsLoading(false)
      setIsReconnecting(false)
      processingRef.current = false
      abortControllerRef.current = null

      if (queueRef.current.length > 0) {
        const next = queueRef.current.shift()!
        const topics = queueRef.current.map((r) => r.topic)
        setQueueLength(queueRef.current.length)
        setQueuedTopics(topics)
        executeResearch(next)
      }
    }
  }

  const retryResearch = useCallback(() => {
    if (lastRequestRef.current) {
      const req = { ...lastRequestRef.current }
      if (activeJobIdRef.current) {
        // Re-use active job_id so backend connects to existing job if alive
        req.job_id = activeJobIdRef.current
      }
      executeResearch(req)
    }
  }, [executeResearch])

  return {
    isLoading,
    isReconnecting,
    error,
    currentResult,
    workflowEvents,
    activeJobId,
    activeTopic,
    history,
    queueLength,
    queuedTopics,
    executeResearch,
    retryResearch,
    resetResearch,
    stopResearch,
    clearHistory,
    setCurrentResult,
  }
}
