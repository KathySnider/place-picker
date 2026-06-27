export interface SearchPreferences {
  // Geography
  regions: string[]
  states: string[]
  metroMax: number | null

  // Population
  popMin: number
  popMax: number

  // Climate
  snowMin: number | null
  snowMax: number | null
  summerMaxF: number | null
  winterMinF: number | null
  summerTrendMax: number | null

  // Budget
  homeValueMax: number | null
  rentMax: number | null

  // Walkability minimums
  walkMin800m: number
  walkMin1600m: number

  // Weights (0–1)
  weights: {
    practical800m: number
    practical1600m: number
    lifestyle800m: number
    lifestyle1600m: number
    summerTemp: number
    snowfall: number
    homeValue: number
    summerTrend: number
    winterTrend: number
  }

  // Preferences
  preferColdWinters: boolean
  preferCoolSummers: boolean
  preferSnowy: boolean
  showStateTax: boolean
  units: 'imperial' | 'metric'

  // Results
  resultCount: number
}

export interface Amenities {
  grocery: boolean | null
  pharmacy: boolean | null
  medical: boolean | null
  bank: boolean | null
  postOffice: boolean | null
  library: boolean | null
  restaurant: boolean | null
  cafe: boolean | null
  bar: boolean | null
  shopping: boolean | null
  park: boolean | null
  arts: boolean | null
  transit: boolean | null
}

export interface Place {
  geoid: string
  placeName: string
  placeType: string
  stateName: string
  compositeScore: number
  population: number
  medianHomeValue: number | null
  medianGrossRent: number | null
  medianReTaxes: number | null
  stateIncomeTaxRate: number | null
  ssTaxed: 'yes' | 'no' | 'partial' | null

  // Walkability
  practical800m: number | null
  practical1600m: number | null
  lifestyle800m: number | null
  lifestyle1600m: number | null

  // Climate
  snowBestIn: number | null
  snowSource: string | null
  summerTempF: number | null
  summerSource: string | null
  winterTempBestF: number | null
  winterSource: string | null
  annualPrecipMm: number | null

  // ERA5 trends
  summerTrendFDec: number | null
  winterTrendFDec: number | null
  summerF1980s: number | null
  summerFRecent: number | null
  winterF1980s: number | null
  winterFRecent: number | null

  // Trails
  trailMiles10mi: number | null
  footwayMiles1mi: number | null

  // Amenities
  amenities: Amenities

  // Facilities
  hospitalDistanceMiles: number | null
  hospitalsWithin30mi: number | null
  collegeDistanceMiles: number | null
  collegesWithin30mi: number | null
  libraryDistanceMiles: number | null
  librariesWithin10mi: number | null

  // Scores
  scores: Record<string, number>
}

export interface ProgressEvent {
  type: 'progress' | 'complete' | 'error'
  step: string
  message: string
  results?: Place[]
  saveId?: string
}

export const REGIONS = [
  'New England',
  'Mid-Atlantic',
  'Great Lakes',
  'Midwest',
  'Plains',
  'Mountain',
  'Pacific Coast',
  'Northeast',
  'West',
  'South',
  'Southeast',
  'South Central',
  'Pacific',
]

export const DEFAULT_PREFERENCES: SearchPreferences = {
  regions: ['Northeast', 'New England', 'Midwest', 'Great Lakes', 'Mountain'],
  states: [],
  metroMax: 250000,
  popMin: 2000,
  popMax: 50000,
  snowMin: 36,
  snowMax: null,
  summerMaxF: 80,
  winterMinF: null,
  summerTrendMax: 0.7,
  homeValueMax: null,
  rentMax: null,
  walkMin800m: 3,
  walkMin1600m: 5,
  weights: {
    practical800m: 1.0,
    practical1600m: 0.7,
    lifestyle800m: 0.3,
    lifestyle1600m: 0.2,
    summerTemp: 0.9,
    snowfall: 0.0,
    homeValue: 0.0,
    summerTrend: 0.5,
    winterTrend: 0.2,
  },
  preferColdWinters: true,
  preferCoolSummers: true,
  preferSnowy: true,
  showStateTax: false,
  units: 'imperial',
  resultCount: 25,
}
