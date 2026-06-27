import type { Place, SearchPreferences } from '../types'

interface Props {
  place: Place
  prefs: SearchPreferences
  onBack: () => void
}

function fmtTemp(f: number | null, units: 'imperial' | 'metric') {
  if (f == null) return '—'
  if (units === 'metric') return `${((f - 32) * 5 / 9).toFixed(1)}°C`
  return `${f.toFixed(1)}°F`
}

function fmtSnow(inches: number | null, units: 'imperial' | 'metric') {
  if (inches == null) return '—'
  if (units === 'metric') return `~${(inches * 25.4).toFixed(0)} mm/yr`
  return `~${inches.toFixed(0)} in/yr`
}

function fmtPrecip(mm: number | null, units: 'imperial' | 'metric') {
  if (mm == null) return '—'
  if (units === 'imperial') return `${(mm / 25.4).toFixed(1)} in/yr`
  return `${mm.toFixed(0)} mm/yr`
}

function fmtTrend(f: number | null) {
  if (f == null) return '—'
  const sign = f >= 0 ? '+' : ''
  return `${sign}${f.toFixed(2)}°F/dec`
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">{title}</h3>
      {children}
    </div>
  )
}

function Row({ label, value, highlight }: { label: string; value: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={`flex justify-between py-1.5 border-b border-slate-100 text-sm ${highlight ? 'font-medium' : ''}`}>
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-800">{value}</span>
    </div>
  )
}

function AmenityBadge({ label, present }: { label: string; present: boolean | null }) {
  if (present == null) return null
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${
      present ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-400 line-through'
    }`}>
      {present ? '✓' : '✗'} {label}
    </span>
  )
}

export function PlaceDetail({ place, prefs, onBack }: Props) {
  const { amenities } = place
  const imperial = prefs.units === 'imperial'

  const ssLabel = { yes: 'SS taxed', no: 'SS exempt', partial: 'SS partial' }[place.ssTaxed ?? ''] ?? ''

  return (
    <div className="max-w-2xl mx-auto px-4 py-10">
      <button
        onClick={onBack}
        className="text-sm text-slate-400 hover:text-slate-600 mb-6 flex items-center gap-1"
      >
        ← Back to results
      </button>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-8">
        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">
              {place.placeName}
              {place.placeType && (
                <span className="text-slate-400 font-normal text-lg ml-2">({place.placeType})</span>
              )}
            </h1>
            <p className="text-slate-500">{place.stateName}</p>
          </div>
          <div className="text-right">
            <div className="text-3xl font-bold text-emerald-600">
              {((place.compositeScore ?? 0) * 100).toFixed(0)}
            </div>
            <div className="text-xs text-slate-400">score</div>
          </div>
        </div>

        {/* Basics */}
        <Section title="Demographics & Cost">
          <Row label="Population" value={place.population?.toLocaleString() ?? '—'} />
          <Row label="Median home value" value={place.medianHomeValue != null ? `$${place.medianHomeValue.toLocaleString()}` : '—'} highlight />
          <Row label="Median rent" value={place.medianGrossRent != null ? `$${place.medianGrossRent.toLocaleString()}/mo` : '—'} />
          <Row label="Median property tax" value={place.medianReTaxes != null ? `$${place.medianReTaxes.toLocaleString()}/yr` : '—'} />
          {prefs.showStateTax && (
            <>
              <Row label="State income tax" value={place.stateIncomeTaxRate != null ? `${place.stateIncomeTaxRate.toFixed(2)}%` : '—'} />
              <Row label="Social Security" value={ssLabel || '—'} />
            </>
          )}
        </Section>

        {/* Climate */}
        <Section title="Climate">
          <Row label={`Snowfall (${place.snowSource ?? ''})`}       value={fmtSnow(place.snowBestIn, prefs.units)} highlight />
          <Row label={`Summer mean (${place.summerSource ?? ''})`}  value={fmtTemp(place.summerTempF, prefs.units)} />
          <Row label={`Winter mean (${place.winterSource ?? ''})`}  value={fmtTemp(place.winterTempBestF, prefs.units)} />
          <Row label="Annual precip"                                 value={fmtPrecip(place.annualPrecipMm, prefs.units)} />
        </Section>

        {/* Warming trends */}
        {place.summerTrendFDec != null && (
          <Section title="ERA5 Warming Trends (daily mean)">
            <Row
              label="Summer trend"
              value={`${fmtTemp(place.summerF1980s, prefs.units)} → ${fmtTemp(place.summerFRecent, prefs.units)} (${fmtTrend(place.summerTrendFDec)})`}
            />
            <Row
              label="Winter trend"
              value={`${fmtTemp(place.winterF1980s, prefs.units)} → ${fmtTemp(place.winterFRecent, prefs.units)} (${fmtTrend(place.winterTrendFDec)})`}
            />
          </Section>
        )}

        {/* Walkability */}
        <Section title="Walkability">
          <Row label="Practical amenities (½mi / 1mi)" value={`${place.practical800m ?? '—'} / ${place.practical1600m ?? '—'}`} highlight />
          <Row label="Lifestyle amenities (½mi / 1mi)" value={`${place.lifestyle800m ?? '—'} / ${place.lifestyle1600m ?? '—'}`} />
          {place.trailMiles10mi != null && (
            <Row label="Trails within 10mi"  value={`${place.trailMiles10mi.toFixed(1)} mi`} />
          )}
          {place.footwayMiles1mi != null && (
            <Row label="Footways within 1mi" value={`${place.footwayMiles1mi.toFixed(1)} mi`} />
          )}
        </Section>

        {/* Amenity checklist */}
        {amenities && (
          <Section title="Amenities within 1 mile (OSM)">
            <div className="flex flex-wrap gap-2">
              <AmenityBadge label="Grocery"     present={amenities.grocery} />
              <AmenityBadge label="Pharmacy"    present={amenities.pharmacy} />
              <AmenityBadge label="Medical"     present={amenities.medical} />
              <AmenityBadge label="Bank"        present={amenities.bank} />
              <AmenityBadge label="Post office" present={amenities.postOffice} />
              <AmenityBadge label="Library"     present={amenities.library} />
              <AmenityBadge label="Restaurant"  present={amenities.restaurant} />
              <AmenityBadge label="Cafe"        present={amenities.cafe} />
              <AmenityBadge label="Bar"         present={amenities.bar} />
              <AmenityBadge label="Shopping"    present={amenities.shopping} />
              <AmenityBadge label="Park"        present={amenities.park} />
              <AmenityBadge label="Arts"        present={amenities.arts} />
              <AmenityBadge label="Transit"     present={amenities.transit} />
            </div>
            <p className="text-xs text-slate-400 mt-2">OSM data may be incomplete for small towns.</p>
          </Section>
        )}

        {/* Facilities */}
        <Section title="Nearby Facilities">
          <Row label="Nearest hospital"    value={place.hospitalDistanceMiles != null ? `${place.hospitalDistanceMiles.toFixed(1)} mi (${place.hospitalsWithin30mi} within 30mi)` : '—'} />
          <Row label="Nearest college"     value={place.collegeDistanceMiles  != null ? `${place.collegeDistanceMiles.toFixed(1)} mi (${place.collegesWithin30mi} within 30mi)` : '—'} />
          <Row label="Nearest library"     value={place.libraryDistanceMiles  != null ? `${place.libraryDistanceMiles.toFixed(1)} mi (${place.librariesWithin10mi} within 10mi)` : '—'} />
        </Section>

        {/* State taxes (if user wants it) — already shown above if prefs.showStateTax */}
        {!prefs.showStateTax && (place.stateIncomeTaxRate != null || place.ssTaxed) && (
          <Section title="State Taxes">
            <Row label="State income tax" value={place.stateIncomeTaxRate != null ? `${place.stateIncomeTaxRate.toFixed(2)}%` : '—'} />
            <Row label="Social Security"  value={ssLabel || '—'} />
          </Section>
        )}
      </div>
    </div>
  )
}
