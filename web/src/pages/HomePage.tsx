import { useState } from 'react'
import { PreferencesForm } from '../components/PreferencesForm'
import { ProgressFeed } from '../components/ProgressFeed'
import { DEFAULT_PREFERENCES } from '../types'
import type { Place, SearchPreferences } from '../types'

const STORAGE_KEY = 'place-picker-prefs'

function loadPrefs(): SearchPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULT_PREFERENCES, ...JSON.parse(raw) }
  } catch {}
  return DEFAULT_PREFERENCES
}

function savePrefs(p: SearchPreferences) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p))
  } catch {}
}

interface Props {
  onResults: (places: Place[], prefs: SearchPreferences) => void
}

export function HomePage({ onResults }: Props) {
  const [running, setRunning] = useState(false)
  const [prefs, setPrefs] = useState<SearchPreferences>(loadPrefs)

  const handleSearch = async (p: SearchPreferences) => {
    setPrefs(p)
    savePrefs(p)
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
