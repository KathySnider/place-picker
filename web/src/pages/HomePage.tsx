import { useState } from 'react'
import { PreferencesForm } from '../components/PreferencesForm'
import { ProgressFeed } from '../components/ProgressFeed'
import { DEFAULT_PREFERENCES } from '../types'
import type { Place, SearchPreferences } from '../types'

interface Props {
  onResults: (places: Place[], prefs: SearchPreferences) => void
}

export function HomePage({ onResults }: Props) {
  const [running, setRunning] = useState(false)
  const [prefs, setPrefs] = useState<SearchPreferences>(DEFAULT_PREFERENCES)

  const handleSearch = async (p: SearchPreferences) => {
    setPrefs(p)
    setRunning(true)
  }

  if (running) {
    return (
      <ProgressFeed
        prefs={prefs}
        onComplete={(places) => onResults(places, prefs)}
        onCancel={() => setRunning(false)}
      />
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      <div className="mb-10 text-center">
        <h1 className="text-4xl font-bold text-slate-800 mb-3">
          🏔️ place-picker
        </h1>
        <p className="text-slate-500 text-lg">
          Find your ideal small town — scored by climate, walkability, and affordability.
        </p>
      </div>
      <PreferencesForm initialPrefs={prefs} onSearch={handleSearch} />
    </div>
  )
}
