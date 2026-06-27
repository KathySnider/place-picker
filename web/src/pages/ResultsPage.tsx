import { useState } from 'react'
import type { Place, SearchPreferences } from '../types'
import { PlaceCard } from '../components/PlaceCard'
import { PlaceDetail } from '../components/PlaceDetail'

interface Props {
  places: Place[]
  prefs: SearchPreferences
  onBack: () => void
}

export function ResultsPage({ places, prefs, onBack }: Props) {
  const [selected, setSelected] = useState<Place | null>(null)

  if (selected) {
    return (
      <PlaceDetail
        place={selected}
        prefs={prefs}
        onBack={() => setSelected(null)}
      />
    )
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-slate-800">Top {places.length} Places</h1>
          <p className="text-slate-500 mt-1">Click any card for full details</p>
        </div>
        <button
          onClick={onBack}
          className="px-4 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors text-sm"
        >
          ← New search
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {places.map((place, i) => (
          <PlaceCard
            key={place.geoid}
            place={place}
            rank={i + 1}
            prefs={prefs}
            onClick={() => setSelected(place)}
          />
        ))}
      </div>

      <p className="text-center text-xs text-slate-400 mt-10">
        Data sources: US Census · OpenStreetMap · PRISM · ERA5 · Daymet · CMS · NCES · IMLS
      </p>
    </div>
  )
}
