import { useState, useEffect, useRef } from 'react'
import type { ResearchRequest, ResearchResponse } from '../types'

export type HistoryItem = ResearchResponse & {
  id: string
  topic: string
  timestamp: number
  depth: number
  provider: string
  model?: string
}

export function useResearch() {
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentResult, setCurrentResult] = useState<ResearchResponse | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [queueLength, setQueueLength] = useState(0)
  const [queuedTopics, setQueuedTopics] = useState<string[]>([])
  const queueRef = useRef<ResearchRequest[]>([])
  const processingRef = useRef(false)
  const abortControllerRef = useRef<AbortController | null>(null)

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
    const newHistory = [item, ...history].slice(0, 50) // Keep last 50
    setHistory(newHistory)
    localStorage.setItem('research_history', JSON.stringify(newHistory))
  }

  const clearHistory = () => {
    setHistory([])
    localStorage.removeItem('research_history')
  }

  const stopResearch = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setIsLoading(false)
    }
    processingRef.current = false
    queueRef.current = []
    setQueueLength(0)
    setQueuedTopics([])
    setError('Research stopped by user')
  }

  const executeResearch = async (request: ResearchRequest) => {
    // Enqueue if a research is already running (ref for synchronous check)
    if (processingRef.current) {
      queueRef.current = [...queueRef.current, request]
      const topics = queueRef.current.map(r => r.topic)
      setQueueLength(queueRef.current.length)
      setQueuedTopics(topics)
      return
    }

    processingRef.current = true
    setIsLoading(true)
    setError(null)
    setCurrentResult(null)
    
    // Reset abort controller
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    abortControllerRef.current = new AbortController()

    try {
      // Use the streaming endpoint
      const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'}/research/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: abortControllerRef.current.signal
      })

      if (!response.ok) throw new Error('Failed to start research stream')
      if (!response.body) throw new Error('ReadableStream not supported')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n\n')
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              
              if (data.type === 'progress') {
                // Dispatch custom event for LoadingState to consume
                window.dispatchEvent(new CustomEvent('research-progress', { detail: data }))
              } else if (data.type === 'token') {
                // Dispatch token event
                window.dispatchEvent(new CustomEvent('research-token', { detail: data }))
              } else if (data.type === 'result') {
                const result = data.data
                setCurrentResult(result)
                saveToHistory({
                  ...result,
                  id: crypto.randomUUID(),
                  topic: request.topic,
                  depth: request.max_depth,
                  provider: request.provider,
                  model: request.model,
                  timestamp: Date.now(),
                })
              } else if (data.type === 'error') {
                throw new Error(data.message)
              }
            } catch (e) {
              console.warn('Failed to parse stream message', e)
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('Research aborted')
        return
      }
      const msg = err instanceof Error ? err.message : 'Unknown error occurred'
      setError(msg)
      // throw err // Don't rethrow, just set error state
    } finally {
      setIsLoading(false)
      processingRef.current = false
      abortControllerRef.current = null

      // Start next queued research if any
      if (queueRef.current.length > 0) {
        const next = queueRef.current.shift()!
        const topics = queueRef.current.map(r => r.topic)
        setQueueLength(queueRef.current.length)
        setQueuedTopics(topics)
        executeResearch(next)
      }
    }
  }

  return {
    isLoading,
    error,
    currentResult,
    history,
    queueLength,
    queuedTopics,
    executeResearch,
    stopResearch,
    clearHistory,
    setCurrentResult, // Allow setting result from history
  }
}
