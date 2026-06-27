from pydantic import BaseModel
from typing import Optional

class Weights(BaseModel):
    practical800m:  float = 1.0
    practical1600m: float = 0.7
    lifestyle800m:  float = 0.3
    lifestyle1600m: float = 0.2
    summerTemp:     float = 0.9
    snowfall:       float = 0.0
    homeValue:      float = 0.0
    summerTrend:    float = 0.5
    winterTrend:    float = 0.2

class SearchRequest(BaseModel):
    regions:         list[str]       = ["Northeast", "New England", "Midwest", "Great Lakes", "Mountain"]
    states:          list[str]       = []
    metroMax:        Optional[int]   = 250_000
    popMin:          int             = 2_000
    popMax:          int             = 50_000
    snowMin:         Optional[float] = 36.0
    snowMax:         Optional[float] = None
    summerMaxF:      Optional[float] = 80.0
    winterMinF:      Optional[float] = None
    summerTrendMax:  Optional[float] = 0.7
    homeValueMax:    Optional[int]   = None
    rentMax:         Optional[int]   = None
    walkMin800m:     int             = 3
    walkMin1600m:    int             = 5
    weights:         Weights         = Weights()
    preferColdWinters: bool          = True
    preferCoolSummers: bool          = True
    preferSnowy:     bool            = True
    showStateTax:    bool            = False
    units:           str             = "imperial"
    resultCount:     int             = 25
