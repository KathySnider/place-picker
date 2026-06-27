import { useState } from 'react'
import { REGIONS } from '../types'
import type { SearchPreferences } from '../types'

interface Props {
  initialPrefs: SearchPreferences
  onSearch: (prefs: SearchPreferences) => void
}

function Slider({
  label, value, min, max, step = 0.1,
  onChange, format = (v: number) => v.toFixed(1),
}: {
  label: string; value: number; min: number; max: number; step?: number
  onChange: (v: number) => void; format?: (v: number) => string
}) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-slate-600">{label}</span>
        <span className="font-medium text-slate-800">{format(value)}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full accent-emerald-600"
      />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-8">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-4">{title}</h2>
      <div className="space-y-4">{children}</div>
    </div>
  )
}

export function PreferencesForm({ initialPrefs, onSearch }: Props) {
  const [p, setP] = useState<SearchPreferences>(initialPrefs)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const set = (key: keyof SearchPreferences, val: any) =>
    setP(prev => ({ ...prev, [key]: val }))
  const setW = (key: keyof SearchPreferences['weights'], val: number) =>
    setP(prev => ({ ...prev, weights: { ...prev.weights, [key]: val } }))

  const toggleRegion = (r: string) =>
    set('regions', p.regions.includes(r) ? p.regions.filter(x => x !== r) : [...p.regions, r])

  return (
    <form
      onSubmit={e => { e.preventDefault(); onSearch(p) }}
      className="bg-white rounded-xl border border-slate-200 shadow-sm p-8 space-y-2"
    >
      {/* Climate style */}
      <Section title="What kind of climate?">
        <div className="grid grid-cols-2 gap-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" checked={p.preferSnowy}
              onChange={e => set('preferSnowy', e.target.checked)}
              className="w-4 h-4 accent-emerald-600" />
            <span className="text-slate-700">I want real winters with snow</span>
          </label>
          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" checked={p.preferCoolSummers}
              onChange={e => set('preferCoolSummers', e.target.checked)}
              className="w-4 h-4 accent-emerald-600" />
            <span className="text-slate-700">I want cool summers</span>
          </label>
        </div>

        {p.preferSnowy && (
          <Slider
            label="Minimum annual snowfall"
            value={p.snowMin ?? 0} min={0} max={150} step={1}
            onChange={v => set('snowMin', v || null)}
            format={v => v === 0 ? 'no minimum' : `${v}" / year`}
          />
        )}

        <Slider
          label="Maximum summer temperature"
          value={p.summerMaxF ?? 95} min={65} max={100} step={1}
          onChange={v => set('summerMaxF', v)}
          format={v => `${v}°F`}
        />

        <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-4">
          <Slider
            label="🌡️ Climate stability — I want a place that still feels the same in 20 years"
            value={p.summerTrendMax ?? 1.5} min={0.3} max={1.5} step={0.05}
            onChange={v => set('summerTrendMax', v >= 1.5 ? null : v)}
            format={v => v >= 1.5 ? 'no filter' : `eliminate places warming faster than +${v.toFixed(2)}°F/decade`}
          />
        </div>
      </Section>

      {/* Town size */}
      <Section title="Town size">
        <div className="grid grid-cols-2 gap-6">
          <Slider label="Minimum population" value={p.popMin} min={1000} max={10000} step={500}
            onChange={v => set('popMin', v)} format={v => v.toLocaleString()} />
          <Slider label="Maximum population" value={p.popMax} min={10000} max={100000} step={1000}
            onChange={v => set('popMax', v)} format={v => v.toLocaleString()} />
        </div>
      </Section>

      {/* Regions */}
      <Section title="Where?">
        <div className="flex flex-wrap gap-2">
          {REGIONS.map(r => (
            <button key={r} type="button"
              onClick={() => toggleRegion(r)}
              className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
                p.regions.includes(r)
                  ? 'bg-emerald-600 text-white border-emerald-600'
                  : 'bg-white text-slate-600 border-slate-300 hover:border-slate-400'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </Section>

      {/* Priorities */}
      <Section title="What matters most? (drag sliders)">
        <Slider label="Walkability — practical (grocery, pharmacy, bank...)"
          value={p.weights.practical800m} min={0} max={1}
          onChange={v => setW('practical800m', v)} format={v => `${(v * 100).toFixed(0)}%`} />
        <Slider label="Walkability — lifestyle (restaurants, parks, cafes...)"
          value={p.weights.lifestyle800m} min={0} max={1}
          onChange={v => setW('lifestyle800m', v)} format={v => `${(v * 100).toFixed(0)}%`} />
        <Slider label="Cool summers"
          value={p.weights.summerTemp} min={0} max={1}
          onChange={v => setW('summerTemp', v)} format={v => `${(v * 100).toFixed(0)}%`} />
        <Slider label="Climate stability (slow warming)"
          value={p.weights.summerTrend} min={0} max={1}
          onChange={v => setW('summerTrend', v)} format={v => `${(v * 100).toFixed(0)}%`} />
        <Slider label="Affordability (home value)"
          value={p.weights.homeValue} min={0} max={1}
          onChange={v => setW('homeValue', v)} format={v => `${(v * 100).toFixed(0)}%`} />
      </Section>

      {/* Options */}
      <Section title="Display options">
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" checked={p.showStateTax}
            onChange={e => set('showStateTax', e.target.checked)}
            className="w-4 h-4 accent-emerald-600" />
          <span className="text-slate-700">Show state tax info prominently</span>
        </label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input type="checkbox" checked={p.units === 'imperial'}
            onChange={e => set('units', e.target.checked ? 'imperial' : 'metric')}
            className="w-4 h-4 accent-emerald-600" />
          <span className="text-slate-700">Use imperial units (°F, inches)</span>
        </label>
      </Section>

      {/* Advanced */}
      <div className="border-t border-slate-100 pt-4">
        <button type="button" onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-sm text-slate-400 hover:text-slate-600 flex items-center gap-1">
          {showAdvanced ? '▾' : '▸'} Advanced options
        </button>

        {showAdvanced && (
          <div className="mt-4 space-y-4">
            <Section title="Budget">
              <Slider label="Max home value"
                value={p.homeValueMax ?? 800000} min={100000} max={1000000} step={25000}
                onChange={v => set('homeValueMax', v >= 1000000 ? null : v)}
                format={v => v >= 1000000 ? 'no limit' : `$${(v / 1000).toFixed(0)}k`} />
              <Slider label="Max monthly rent"
                value={p.rentMax ?? 3000} min={500} max={3000} step={100}
                onChange={v => set('rentMax', v >= 3000 ? null : v)}
                format={v => v >= 3000 ? 'no limit' : `$${v}/mo`} />
            </Section>
            <Section title="Walkability minimums">
              <Slider label="Min practical amenities within ½ mile"
                value={p.walkMin800m} min={0} max={20} step={1}
                onChange={v => set('walkMin800m', v)} format={v => `${v}`} />
              <Slider label="Min practical amenities within 1 mile"
                value={p.walkMin1600m} min={0} max={30} step={1}
                onChange={v => set('walkMin1600m', v)} format={v => `${v}`} />
            </Section>
            <Section title="Exclude large metros">
              <Slider label="Max metro population"
                value={p.metroMax ?? 2000000} min={100000} max={2000000} step={50000}
                onChange={v => set('metroMax', v >= 2000000 ? null : v)}
                format={v => v >= 2000000 ? 'no limit' : v >= 1000000 ? `${(v/1000000).toFixed(1)}M` : `${(v/1000).toFixed(0)}k`} />
            </Section>
          </div>
        )}
      </div>

      <div className="pt-4">
        <button type="submit"
          disabled={p.regions.length === 0}
          className="w-full py-3 px-6 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-semibold rounded-lg transition-colors text-lg">
          Find my places →
        </button>
        {p.regions.length === 0 && (
          <p className="text-xs text-red-500 mt-2 text-center">Please select at least one region</p>
        )}
      </div>
    </form>
  )
}
