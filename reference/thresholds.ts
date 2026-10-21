/**
 * Every classification threshold used by NIWIS, with the spec section it comes from.
 * Classes are always DERIVED from values with these functions — never assigned at random.
 */

// ---------------------------------------------------------------- Drought (spec §3.4)
export type DroughtClass = 'None' | 'D0' | 'D1' | 'D2' | 'D3' | 'D4';

export const DROUGHT_CLASSES: {
  id: DroughtClass;
  label: string;
  spiMax: number;
  spiMin: number;
  probability: string;
  impact: string;
}[] = [
  {
    id: 'D0',
    label: 'Abnormally Dry',
    spiMax: -0.5,
    spiMin: -0.79,
    probability: '20–30%',
    impact: 'Pasture/crop stress starting',
  },
  {
    id: 'D1',
    label: 'Moderate Drought',
    spiMax: -0.8,
    spiMin: -1.29,
    probability: '10–20%',
    impact: 'Streams/dams low',
  },
  {
    id: 'D2',
    label: 'Severe Drought',
    spiMax: -1.3,
    spiMin: -1.59,
    probability: '5–10%',
    impact: 'Water shortages common',
  },
  {
    id: 'D3',
    label: 'Extreme Drought',
    spiMax: -1.6,
    spiMin: -1.99,
    probability: '2–5%',
    impact: 'Major water restrictions',
  },
  {
    id: 'D4',
    label: 'Exceptional Drought',
    spiMax: -2.0,
    spiMin: -Infinity,
    probability: '< 2%',
    impact: 'Water emergency declared',
  },
];

/** SPI (or SPEI / SSI / SGI) → drought class, spec §3.4. Values are rounded to 2 dp first, as published. */
export function droughtClassFromSpi(spi: number | null | undefined): DroughtClass {
  if (spi == null || Number.isNaN(spi)) return 'None';
  const v = Math.round(spi * 100) / 100;
  if (v <= -2.0) return 'D4';
  if (v <= -1.6) return 'D3';
  if (v <= -1.3) return 'D2';
  if (v <= -0.8) return 'D1';
  if (v <= -0.5) return 'D0';
  return 'None';
}

export const DROUGHT_RANK: Record<DroughtClass, number> = { None: 0, D0: 1, D1: 2, D2: 3, D3: 4, D4: 5 };

export function droughtLabel(c: DroughtClass): string {
  return c === 'None' ? 'No drought' : `${c} ${DROUGHT_CLASSES.find((d) => d.id === c)!.label}`;
}

/** Wet side of the SPI scale (McKee et al. 1993). */
export function spiCategory(spi: number): string {
  if (spi >= 2) return 'Extremely wet';
  if (spi >= 1.5) return 'Very wet';
  if (spi >= 1.0) return 'Moderately wet';
  if (spi > -0.5) return 'Near normal';
  return droughtLabel(droughtClassFromSpi(spi));
}

// ---------------------------------------------------------------- Reservoirs (spec §8.2)
export type StorageAlertLevel = 'None' | 'L1' | 'L2' | 'L3';
export const STORAGE_ALERTS = { L1: 50, L2: 30, L3: 15 } as const;

/** %FSC → critical level alert, spec §8.2: Level 1 (< 50% FSC), Level 2 (< 30%), Level 3 (< 15%). */
export function storageAlertLevel(pctFsc: number): StorageAlertLevel {
  if (pctFsc < STORAGE_ALERTS.L3) return 'L3';
  if (pctFsc < STORAGE_ALERTS.L2) return 'L2';
  if (pctFsc < STORAGE_ALERTS.L1) return 'L1';
  return 'None';
}

/** Storage band used on maps and tables (build prompt §7). */
export function storageBand(pctFsc: number): 'very-low' | 'low' | 'moderate' | 'high' | 'very-high' {
  if (pctFsc < 25) return 'very-low';
  if (pctFsc < 50) return 'low';
  if (pctFsc < 70) return 'moderate';
  if (pctFsc < 90) return 'high';
  return 'very-high';
}

/** Dam operational status from %FSC and 30-day trend (pp). */
export function damOperationalStatus(pctFsc: number, trend30: number): 'Normal' | 'Watch' | 'Warning' | 'Critical' {
  if (pctFsc < STORAGE_ALERTS.L3) return 'Critical';
  if (pctFsc < STORAGE_ALERTS.L2 || (pctFsc < STORAGE_ALERTS.L1 && trend30 < -3)) return 'Warning';
  if (pctFsc < STORAGE_ALERTS.L1) return 'Watch';
  return 'Normal';
}

/** Days until empty at the current net draw-down rate (Mm³/day). null when the dam is filling. */
export function daysToEmpty(volumeMm3: number, netDrawdownMm3PerDay: number): number | null {
  if (netDrawdownMm3PerDay <= 0) return null;
  return Math.round(volumeMm3 / netDrawdownMm3PerDay);
}

// ---------------------------------------------------------------- Drinking water, SANS 241:2015 (spec §14.1)
export interface Sans241Limit {
  id: string;
  label: string;
  unit: string;
  min?: number;
  max?: number;
  risk: 'Aesthetic' | 'Health' | 'Treatment';
}

export const SANS_241: Sans241Limit[] = [
  { id: 'turbidity', label: 'Turbidity', unit: 'NTU', max: 1, risk: 'Aesthetic' },
  { id: 'colour', label: 'Colour', unit: 'TCU', max: 15, risk: 'Aesthetic' },
  { id: 'temperature', label: 'Temperature', unit: '°C', max: 25, risk: 'Aesthetic' },
  { id: 'ecoli', label: 'E. coli', unit: 'cfu/100 mL', max: 0, risk: 'Health' },
  { id: 'totalColiforms', label: 'Total coliforms', unit: 'cfu/100 mL', max: 0, risk: 'Health' },
  { id: 'ph', label: 'pH', unit: 'pH units', min: 6.0, max: 9.0, risk: 'Aesthetic' },
  { id: 'ec', label: 'Electrical conductivity', unit: 'mS/m', max: 170, risk: 'Aesthetic' },
  { id: 'freeChlorine', label: 'Free chlorine residual', unit: 'mg/L', min: 0.2, max: 5.0, risk: 'Treatment' },
  { id: 'nitrate', label: 'Nitrate (NO₃⁻-N)', unit: 'mg/L', max: 11, risk: 'Health' },
  { id: 'fluoride', label: 'Fluoride', unit: 'mg/L', max: 1.5, risk: 'Health' },
  { id: 'arsenic', label: 'Arsenic', unit: 'mg/L', max: 0.01, risk: 'Health' },
  { id: 'lead', label: 'Lead', unit: 'mg/L', max: 0.01, risk: 'Health' },
  { id: 'cyanide', label: 'Cyanide', unit: 'mg/L', max: 0.07, risk: 'Health' },
  { id: 'thm', label: 'Total THMs', unit: 'mg/L', max: 0.2, risk: 'Health' },
];

export function sans241Limit(id: string): Sans241Limit | undefined {
  return SANS_241.find((l) => l.id === id);
}

/** Pass/fail of a single sample against SANS 241. Unknown determinands pass. */
export function sans241Pass(id: string, value: number): boolean {
  const l = sans241Limit(id);
  if (!l) return true;
  if (l.min != null && value < l.min) return false;
  if (l.max != null && value > l.max) return false;
  return true;
}

export function sans241LimitText(id: string): string {
  const l = sans241Limit(id);
  if (!l) return '—';
  if (l.min != null && l.max != null) return `${l.min}–${l.max}`;
  if (l.max != null) return `≤ ${l.max}`;
  return `≥ ${l.min}`;
}

// ---------------------------------------------------------------- Water Quality Index bands
export type WqiClass = 'Excellent' | 'Good' | 'Fair' | 'Poor' | 'Very Poor';
/** WQI bands (build prompt §5): Excellent 90–100, Good 70–90, Fair 50–70, Poor 25–50, Very Poor 0–25. */
export function wqiClass(wqi: number): WqiClass {
  if (wqi >= 90) return 'Excellent';
  if (wqi >= 70) return 'Good';
  if (wqi >= 50) return 'Fair';
  if (wqi >= 25) return 'Poor';
  return 'Very Poor';
}

// ---------------------------------------------------------------- Green Drop / Blue Drop bands (screens 35, 36)
export type ComplianceBand = 'Excellent' | 'Good' | 'Moderate' | 'Poor' | 'Critical';
export function greenDropBand(score: number): ComplianceBand {
  if (score >= 90) return 'Excellent';
  if (score >= 70) return 'Good';
  if (score >= 50) return 'Moderate';
  if (score >= 30) return 'Poor';
  return 'Critical';
}
export function blueDropBand(score: number): ComplianceBand {
  if (score >= 95) return 'Excellent';
  if (score >= 90) return 'Good';
  if (score >= 70) return 'Moderate';
  if (score >= 50) return 'Poor';
  return 'Critical';
}

/** Wastewater effluent limits (General Authorisation, DWS 2013 general limits) used for Green Drop panels. */
export const EFFLUENT_LIMITS: Record<string, { label: string; unit: string; min?: number; max?: number }> = {
  ph: { label: 'pH', unit: 'pH units', min: 5.5, max: 9.5 },
  bod: { label: 'BOD₅', unit: 'mg/L', max: 30 },
  cod: { label: 'COD', unit: 'mg/L', max: 75 },
  ammonia: { label: 'Ammonia (as N)', unit: 'mg/L', max: 6 },
  nitrate: { label: 'Nitrate/Nitrite (as N)', unit: 'mg/L', max: 15 },
  tp: { label: 'Total phosphorus', unit: 'mg/L', max: 10 },
  ortho: { label: 'Ortho-phosphate (as P)', unit: 'mg/L', max: 10 },
  ecoli: { label: 'E. coli', unit: 'cfu/100 mL', max: 1000 },
  faecal: { label: 'Faecal coliforms', unit: 'cfu/100 mL', max: 1000 },
  tss: { label: 'Suspended solids', unit: 'mg/L', max: 25 },
  ec: { label: 'Electrical conductivity', unit: 'mS/m', max: 150 },
};

export function effluentPass(id: string, value: number): boolean {
  const l = EFFLUENT_LIMITS[id];
  if (!l) return true;
  if (l.min != null && value < l.min) return false;
  if (l.max != null && value > l.max) return false;
  return true;
}

// ---------------------------------------------------------------- Vegetation
/** Vegetation health classes, spec §5.3 ("Excellent / Good / Fair / Poor / Very Poor"). */
export type VegClass = 'Excellent' | 'Good' | 'Fair' | 'Poor' | 'Very Poor';
export const VEG_CLASSES: VegClass[] = ['Excellent', 'Good', 'Fair', 'Poor', 'Very Poor'];
/** VCI / VHI (0–100%) → health class (breaks at 20 / 40 / 60 / 80 %, Kogan 1995; < 40% = drought stress). */
export function vegClass(vci: number): VegClass {
  if (vci >= 80) return 'Excellent';
  if (vci >= 60) return 'Good';
  if (vci >= 40) return 'Fair';
  if (vci >= 20) return 'Poor';
  return 'Very Poor';
}

// ---------------------------------------------------------------- Groundwater
/** Exploitation index = abstraction / recharge (spec §11.3). */
export function exploitationClass(
  ei: number,
): 'Very Low' | 'Low' | 'Moderate' | 'High' | 'Very High' | 'Over-exploited' {
  if (ei < 0.1) return 'Very Low';
  if (ei < 0.3) return 'Low';
  if (ei < 0.5) return 'Moderate';
  if (ei < 0.7) return 'High';
  if (ei <= 1.0) return 'Very High';
  return 'Over-exploited';
}
export function boreholeStatus(sgi: number): 'Normal' | 'Watch' | 'Low' | 'Critical' {
  if (sgi <= -1.6) return 'Critical';
  if (sgi <= -1.0) return 'Low';
  if (sgi <= -0.5) return 'Watch';
  return 'Normal';
}

// ---------------------------------------------------------------- Services
export const NRW_TARGET_PCT = 20;
/** RDP basic service level: 25 L/person/day within 200 m (spec §18.4 WCI). */
export const RDP_LITRES_PER_PERSON_DAY = 25;
export const FREE_BASIC_WATER_KL = 6;

// ---------------------------------------------------------------- Risk
export type RiskLevel = 'Low' | 'Moderate' | 'High' | 'Very High';
export function riskFromScore(score01: number): RiskLevel {
  if (score01 >= 0.75) return 'Very High';
  if (score01 >= 0.5) return 'High';
  if (score01 >= 0.25) return 'Moderate';
  return 'Low';
}

// ---------------------------------------------------------------- Data QA (spec §23.1)
export const QA = { maxGapDays: 3, trainingMaxMissingPct: 10 } as const;
