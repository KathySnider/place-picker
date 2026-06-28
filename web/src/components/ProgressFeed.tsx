import { useEffect, useRef, useState } from 'react'
import type { Place, SearchPreferences } from '../types'

interface ProgressEvent {
  type: 'progress' | 'complete' | 'error'
  step: string
  message: string
  results?: Place[]
}

interface Props {
  prefs: SearchPreferences
  onComplete: (places: Place[]) => void
  onCancel: () => void
}

const STEP_LABELS: Record<string, string> = {
  start:      'Starting up',
  census:     'Census data',
  filter:     'Filtering candidates',
  osm:        'Walkability (OSM)',
  daymet:     'Climate (Daymet)',
  prism:      'Climate (PRISM)',
  era5:       'Warming trends (ERA5)',
  tax:        'State taxes',
  facilities: 'Hospitals, colleges, libraries',
  score:      'Scoring & ranking',
  detail:     'Amenity detail',
  done:       'Complete',
}

export function ProgressFeed({ prefs, onComplete, onCancel }: Props) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const controller = new AbortController()

    const run = async () => {
      try {
        const resp = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            regions:          prefs.regions,
            states:           prefs.states,
            metroMax:         prefs.metroMax,
            popMin:           prefs.popMin,
            popMax:           prefs.popMax,
            snowMin:          prefs.snowMin,
            snowMax:          prefs.snowMax,
            summerMaxF:       prefs.summerMaxF,
            winterMinF:       prefs.winterMinF,
            summerTrendMax:   prefs.summerTrendMax,
            homeValueMax:     prefs.homeValueMax,
            rentMax:          prefs.rentMax,
            walkMin800m:      prefs.walkMin800m,
            walkMin1600m:     prefs.walkMin1600m,
            weights:          prefs.weights,
            preferColdWinters: prefs.preferColdWinters,
            preferCoolSummers: prefs.preferCoolSummers,
            preferSnowy:      prefs.preferSnowy,
            showStateTax:     prefs.showStateTax,
            units:            prefs.units,
            resultCount:      prefs.resultCount,
          }),
          signal: controller.signal,
        })

        if (!resp.body) throw new Error('No response body')
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const ev: ProgressEvent = JSON.parse(line.slice(6))
            setEvents(prev => [...prev, ev])
            if (ev.type === 'complete' && ev.results) {
              onComplete(ev.results)
            }
            if (ev.type === 'error') {
              setError(ev.message)
            }
          }
        }
      } catch (e: any) {
        if (e.name !== 'AbortError') setError(String(e))
      }
    }

    run()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className="max-w-2xl mx-auto px-4 py-16">
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold text-slate-800 mb-2">🔍 Searching...</h1>
        <p className="text-slate-500">Grab a coffee — this takes a few minutes on the first run.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 space-y-3 font-mono text-sm">
        {events.map((ev, i) => (
          <div key={i} className={`flex gap-3 ${ev.type === 'error' ? 'text-red-600' : 'text-slate-700'}`}>
            <span className="text-slate-400 shrink-0 w-32">
              {STEP_LABELS[ev.step] ?? ev.step}
            </span>
            <span>{ev.message}</span>
          </div>
        ))}
        {!error && events.length > 0 && events[events.length - 1].type !== 'complete' && (
          <div className="flex gap-2 text-slate-400 animate-pulse">
            <span>▸</span><span>Working...</span>
          </div>
        )}
        {error && (
          <div className="text-red-600 font-sans">Error: {error}</div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="text-center mt-6">
        <button
          onClick={onCancel}
          className="text-sm text-slate-400 hover:text-slate-600 underline"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
