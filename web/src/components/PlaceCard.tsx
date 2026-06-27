import type { Place, SearchPreferences } from '../types'

interface Props {
  place: Place
  rank: number
  prefs: SearchPreferences
  onClick: () => void
}

function fmt(val: number | null, prefix = '', suffix = '', decimals = 0) {
  if (val == null) return '—'
  return `${prefix}${val.toLocaleString(undefined, { maximumFractionDigits: decimals })}${suffix}`
}

function fmtTemp(f: number | null, units: 'imperial' | 'metric') {
  if (f == null) return '—'
  if (units === 'metric') return `${((f - 32) * 5 / 9).toFixed(1)}°C`
  return `${f.toFixed(1)}°F`
}

function fmtSnow(inches: number | null, units: 'imperial' | 'metric') {
  if (inches == null) return '—'
  if (units === 'metric') return `${(inches * 25.4).toFixed(0)} mm`
  return `${inches.toFixed(0)}"`
}

export function PlaceCard({ place, rank, prefs, onClick }: Props) {
  const scoreColor = (s: number) => {
    if (s >= 0.6) return 'text-emerald-600'
    if (s >= 0.45) return 'text-amber-600'
    return 'text-slate-500'
  }

  return (
    <button
      onClick={onClick}
      className="text-left bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:border-slate-300 transition-all p-5 w-full"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold text-slate-400">#{rank}</span>
            <h2 className="font-bold text-slate-800 text-base leading-tight">
              {place.placeName}
              {place.placeType && (
                <span className="font-normal text-slate-500 text-sm ml-1">
                  ({place.placeType})
                </span>
              )}
            </h2>
          </div>
          <p className="text-sm text-slate-500 mt-0.5">{place.stateName}</p>
        </div>
        <div className={`text-xl font-bold ${scoreColor(place.compositeScore ?? 0)}`}>
          {((place.compositeScore ?? 0) * 100).toFixed(0)}
        </div>
      </div>

      {/* Climate row */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div className="bg-slate-50 rounded-lg p-2 text-center">
          <div className="text-xs text-slate-400 mb-0.5">Snow</div>
          <div className="font-semibold text-slate-700 text-sm">{fmtSnow(place.snowBestIn, prefs.units)}</div>
        </div>
        <div className="bg-slate-50 rounded-lg p-2 text-center">
          <div className="text-xs text-slate-400 mb-0.5">Summer</div>
          <div className="font-semibold text-slate-700 text-sm">{fmtTemp(place.summerTempF, prefs.units)}</div>
        </div>
        <div className="bg-slate-50 rounded-lg p-2 text-center">
          <div className="text-xs text-slate-400 mb-0.5">Winter</div>
          <div className="font-semibold text-slate-700 text-sm">{fmtTemp(place.winterTempBestF, prefs.units)}</div>
        </div>
      </div>

      {/* Key stats */}
      <div className="space-y-1 text-sm">
        <div className="flex justify-between text-slate-600">
          <span>Walkability (½mi / 1mi)</span>
          <span className="font-medium">
            {place.practical800m ?? '—'} / {place.practical1600m ?? '—'}
          </span>
        </div>
        <div className="flex justify-between text-slate-600">
          <span>Home value</span>
          <span className="font-medium">{fmt(place.medianHomeValue, '$')}</span>
        </div>
        {place.trailMiles10mi != null && (
          <div className="flex justify-between text-slate-600">
            <span>Trails (10mi)</span>
            <span className="font-medium">{place.trailMiles10mi.toFixed(0)} mi</span>
          </div>
        )}
        {place.summerTrendFDec != null && (
          <div className="flex justify-between text-slate-600">
            <span>Summer warming</span>
            <span className={`font-medium ${place.summerTrendFDec > 0.6 ? 'text-amber-600' : 'text-emerald-600'}`}>
              +{place.summerTrendFDec.toFixed(2)}°F/dec
            </span>
          </div>
        )}
      </div>

      <div className="mt-3 text-xs text-slate-400 text-right">tap for details →</div>
    </button>
  )
}
