import { useState } from 'react'
import { HomePage } from './pages/HomePage'
import { ResultsPage } from './pages/ResultsPage'
import type { Place, SearchPreferences } from './types'

type AppState =
  | { view: 'home' }
  | { view: 'results'; places: Place[]; prefs: SearchPreferences }

export default function App() {
  const [state, setState] = useState<AppState>({ view: 'home' })

  return (
    <div className="min-h-screen bg-slate-50">
      {state.view === 'home' && (
        <HomePage
          onResults={(places, prefs) => setState({ view: 'results', places, prefs })}
        />
      )}
      {state.view === 'results' && (
        <ResultsPage
          places={state.places}
          prefs={state.prefs}
          onBack={() => setState({ view: 'home' })}
        />
      )}
    </div>
  )
}
