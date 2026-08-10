import { useState } from 'react'
import type { Place, SearchPreferences } from '../types'
import { PlaceCard } from '../components/PlaceCard'
import { PlaceDetail } from '../components/PlaceDetail'

interface Props {
  places: Place[]
  prefs: SearchPreferences
  onBack: () => void
}

function exportCSV(places: Place[]) {
  const headers = [
    'Rank', 'Place', 'Type', 'State', 'Score', 'Population',
    'Snow (in)', 'Summer (°F)', 'Winter (°F)',
    'Walkability ½mi', 'Walkability 1mi',
    'Home Value', 'Gross Rent',
    'July Avg High (°F)', 'Jan Avg Low (°F)',
    'Trails (10mi)', 'Summer Warming (°F/dec)',
    'Hospital (mi)', 'Hospitals within 30mi',
    'College (mi)', 'Colleges within 30mi',
    'Grocery', 'Pharmacy', 'Medical', 'Bank', 'Post Office', 'Library',
    'Restaurant', 'Cafe', 'Bar', 'Shopping', 'Park', 'Arts', 'Transit',
  ]

  const rows = places.map((p, i) => [
    i + 1,
    p.placeName,
    p.placeType,
    p.stateName,
    p.compositeScore,
    p.population,
    p.snowBestIn ?? '',
    p.summerTempF ?? '',
    p.winterTempBestF ?? '',
    p.practical800m ?? '',
    p.practical1600m ?? '',
    p.medianHomeValue ?? '',
    p.medianGrossRent ?? '',
    p.prismJulyTmaxF ?? '',
    p.prismJanTminF ?? '',
    p.trailMiles10mi ?? '',
    p.summerTrendFDec ?? '',
    p.hospitalDistanceMiles ?? '',
    p.hospitalsWithin30mi ?? '',
    p.collegeDistanceMiles ?? '',
    p.collegesWithin30mi ?? '',
    p.amenities.grocery ? 'Yes' : 'No',
    p.amenities.pharmacy ? 'Yes' : 'No',
    p.amenities.medical ? 'Yes' : 'No',
    p.amenities.bank ? 'Yes' : 'No',
    p.amenities.postOffice ? 'Yes' : 'No',
    p.amenities.library ? 'Yes' : 'No',
    p.amenities.restaurant ? 'Yes' : 'No',
    p.amenities.cafe ? 'Yes' : 'No',
    p.amenities.bar ? 'Yes' : 'No',
    p.amenities.shopping ? 'Yes' : 'No',
    p.amenities.park ? 'Yes' : 'No',
    p.amenities.arts ? 'Yes' : 'No',
    p.amenities.transit ? 'Yes' : 'No',
  ])

  const csv = [headers, ...rows]
    .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
    .join('\n')

  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `place-picker-results-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
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
        <div className="flex gap-2">
          <button
            onClick={() => exportCSV(places)}
            className="px-4 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors text-sm"
          >
            ↓ Download CSV
          </button>
          <button
            onClick={onBack}
            className="px-4 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors text-sm"
          >
            ← New search
          </button>
        </div>
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
