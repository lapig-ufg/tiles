export type SpectralIndexType = 'rgb' | 'index' | 'false_color' | 'savi' | 'agri' | 'geo';

export interface SpectralIndex {
  id: string;
  label: string;
  formula: string;
  type: SpectralIndexType;
  bandA?: string;
  bandB?: string;
  bands?: string[];
  bandMapping: Record<string, string>;
  style: any;
  legend?: LegendStop[];
}

export interface LegendStop {
  value: number;
  color: string;
  label?: string;
}

// ─── Band mapping ────────────────────────────────────────────────
// Sentinel-2 band common names → Earth Search STAC asset keys
const S2_BAND_MAP: Record<string, string> = {
  blue: 'blue',          // B02, 490nm, 10m
  green: 'green',        // B03, 560nm, 10m
  red: 'red',            // B04, 665nm, 10m
  rededge1: 'rededge1',  // B05, 705nm, 20m
  rededge2: 'rededge2',  // B06, 740nm, 20m
  rededge3: 'rededge3',  // B07, 783nm, 20m
  nir: 'nir',            // B08, 842nm, 10m
  nir08: 'nir08',        // B8A, 865nm, 20m
  swir16: 'swir16',      // B11, 1610nm, 20m
  swir22: 'swir22',      // B12, 2190nm, 20m
};

// ─── Style definitions ──────────────────────────────────────────
// Replicated from earth-search-explorer.html which is proven to work.
//
// TCI (rgb): Uses the pre-rendered 'visual' asset (8-bit, max:255).
//            Style: divide by 255, multiply by 1.15 for contrast, gamma 0.9.
//
// false_color / agri / geo: Individual bands with max:10000.
//            Style: percent stretch [lo, hi] with clamp + gamma.
//
// index / savi: Two bands with max:10000.
//            Style: normalized difference with interpolated color ramp.

// ─── Shared ND color ramp (from working HTML) ───────────────────
const ND_COLOR_RAMP = [
  -1.0, [8, 29, 88, 1],
  -0.5, [34, 94, 168, 1],
  -0.3, [65, 152, 196, 1],
  -0.1, [161, 218, 180, 1],
   0,   [255, 255, 204, 1],
   0.05,[245, 245, 175, 1],
   0.1, [224, 232, 148, 1],
   0.15,[200, 218, 120, 1],
   0.2, [170, 200, 95, 1],
   0.25,[145, 191, 82, 1],
   0.3, [120, 175, 68, 1],
   0.35,[97, 158, 54, 1],
   0.4, [75, 140, 42, 1],
   0.45,[55, 122, 30, 1],
   0.5, [38, 104, 20, 1],
   0.6, [20, 85, 10, 1],
   0.7, [8, 68, 4, 1],
   0.8, [0, 52, 0, 1],
   1.0, [0, 35, 0, 1],
];

// ─── Spectral Indices ────────────────────────────────────────────

export const SPECTRAL_INDICES: SpectralIndex[] = [
  {
    id: 'TCI',
    label: 'True Color (TCI)',
    formula: 'RGB: Red, Green, Blue',
    type: 'rgb',
    bands: [], // TCI uses 'visual' asset, not individual bands
    bandMapping: S2_BAND_MAP,
    // From HTML: visual asset (8-bit), divide by 255, *1.15 contrast, gamma 0.9
    style: {
      color: [
        'array',
        ['clamp', ['*', ['/', ['band', 1], 255], 1.15], 0, 1],
        ['clamp', ['*', ['/', ['band', 2], 255], 1.15], 0, 1],
        ['clamp', ['*', ['/', ['band', 3], 255], 1.15], 0, 1],
        1,
      ],
      gamma: 0.9,
    },
  },
  {
    id: 'NDVI',
    label: 'NDVI',
    formula: '(NIR - Red) / (NIR + Red)',
    type: 'index',
    bandA: 'nir',
    bandB: 'red',
    bandMapping: S2_BAND_MAP,
    // From HTML: band2=NIR(bandA), band1=Red(bandB)
    // ND = (band2 - band1) / (band2 + band1)
    style: {
      color: [
        'interpolate', ['linear'],
        ['/', ['-', ['band', 2], ['band', 1]], ['+', ['band', 2], ['band', 1]]],
        ...ND_COLOR_RAMP,
      ],
    },
    legend: [
      { value: -0.3, color: '#4198C4', label: '-0.3' },
      { value: 0.0, color: '#FFFFCC', label: '0.0' },
      { value: 0.2, color: '#AAC85F', label: '0.2' },
      { value: 0.4, color: '#4B8C2A', label: '0.4' },
      { value: 0.7, color: '#084404', label: '0.7' },
    ],
  },
  {
    id: 'NDWI',
    label: 'NDWI',
    formula: '(Green - NIR) / (Green + NIR)',
    type: 'index',
    bandA: 'green',
    bandB: 'nir',
    bandMapping: S2_BAND_MAP,
    style: {
      color: [
        'interpolate', ['linear'],
        ['/', ['-', ['band', 2], ['band', 1]], ['+', ['band', 2], ['band', 1]]],
        ...ND_COLOR_RAMP,
      ],
    },
    legend: [
      { value: -0.3, color: '#4198C4', label: '-0.3' },
      { value: 0.0, color: '#FFFFCC', label: '0.0' },
      { value: 0.4, color: '#4B8C2A', label: '0.4' },
      { value: 0.8, color: '#003400', label: '0.8' },
    ],
  },
  {
    id: 'NDMI',
    label: 'NDMI',
    formula: '(NIR - SWIR1.6) / (NIR + SWIR1.6)',
    type: 'index',
    bandA: 'nir',
    bandB: 'swir16',
    bandMapping: S2_BAND_MAP,
    style: {
      color: [
        'interpolate', ['linear'],
        ['/', ['-', ['band', 2], ['band', 1]], ['+', ['band', 2], ['band', 1]]],
        ...ND_COLOR_RAMP,
      ],
    },
    legend: [
      { value: -0.3, color: '#4198C4', label: '-0.3' },
      { value: 0.0, color: '#FFFFCC', label: '0.0' },
      { value: 0.4, color: '#4B8C2A', label: '0.4' },
      { value: 0.8, color: '#003400', label: '0.8' },
    ],
  },
  {
    id: 'FalseIR',
    label: 'False Color IR',
    formula: 'RGB: NIR, Red, Green',
    type: 'false_color',
    bands: ['nir', 'red', 'green'],
    bandMapping: S2_BAND_MAP,
    // From HTML: percent stretch lo=200, hi=3800, gamma=0.85
    style: (() => {
      const lo = 200, hi = 3800, range = hi - lo;
      return {
        color: [
          'array',
          ['clamp', ['/', ['-', ['band', 1], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 2], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 3], lo], range], 0, 1],
          1,
        ],
        gamma: 0.85,
      };
    })(),
  },
  {
    id: 'SAVI',
    label: 'SAVI',
    formula: '((NIR - Red) / (NIR + Red + 0.5)) x 1.5',
    type: 'savi',
    bandA: 'nir',
    bandB: 'red',
    bandMapping: S2_BAND_MAP,
    // From HTML: band2=NIR, band1=RED, SAVI = 1.5*(NIR-RED)/(NIR+RED+5000)
    style: {
      color: [
        'interpolate', ['linear'],
        ['*', 1.5, ['/', ['-', ['band', 2], ['band', 1]], ['+', ['+', ['band', 2], ['band', 1]], 5000]]],
        ...ND_COLOR_RAMP,
      ],
    },
    legend: [
      { value: -0.2, color: '#4198C4', label: '-0.2' },
      { value: 0.0, color: '#FFFFCC', label: '0.0' },
      { value: 0.3, color: '#78A636', label: '0.3' },
      { value: 0.6, color: '#003400', label: '0.6' },
    ],
  },
  {
    id: 'NBR',
    label: 'NBR',
    formula: '(NIR - SWIR2.2) / (NIR + SWIR2.2)',
    type: 'index',
    bandA: 'nir',
    bandB: 'swir22',
    bandMapping: S2_BAND_MAP,
    style: {
      color: [
        'interpolate', ['linear'],
        ['/', ['-', ['band', 2], ['band', 1]], ['+', ['band', 2], ['band', 1]]],
        ...ND_COLOR_RAMP,
      ],
    },
    legend: [
      { value: -0.3, color: '#4198C4', label: '-0.3' },
      { value: 0.0, color: '#FFFFCC', label: '0.0' },
      { value: 0.4, color: '#4B8C2A', label: '0.4' },
      { value: 0.7, color: '#084404', label: '0.7' },
    ],
  },
  {
    id: 'AGRI',
    label: 'Agriculture',
    formula: 'RGB: SWIR1.6, NIR, Blue',
    type: 'agri',
    bands: ['swir16', 'nir', 'blue'],
    bandMapping: S2_BAND_MAP,
    // From HTML: agri/geo stretch lo=150, hi=4200, gamma=0.8
    style: (() => {
      const lo = 150, hi = 4200, range = hi - lo;
      return {
        color: [
          'array',
          ['clamp', ['/', ['-', ['band', 1], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 2], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 3], lo], range], 0, 1],
          1,
        ],
        gamma: 0.8,
      };
    })(),
  },
  {
    id: 'GEO',
    label: 'Geology',
    formula: 'RGB: SWIR2.2, SWIR1.6, Blue',
    type: 'geo',
    bands: ['swir22', 'swir16', 'blue'],
    bandMapping: S2_BAND_MAP,
    style: (() => {
      const lo = 150, hi = 4200, range = hi - lo;
      return {
        color: [
          'array',
          ['clamp', ['/', ['-', ['band', 1], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 2], lo], range], 0, 1],
          ['clamp', ['/', ['-', ['band', 3], lo], range], 0, 1],
          1,
        ],
        gamma: 0.8,
      };
    })(),
  },
];

/**
 * Returns the STAC asset keys needed to render a given spectral index.
 *
 * IMPORTANT for 'index' and 'savi' types:
 * The HTML reference loads [bandB, bandA] so that in the style expression:
 *   band 1 = bandB (denominator)
 *   band 2 = bandA (numerator)
 * ND = (band2 - band1) / (band2 + band1)
 */
export function getAssetKeysForIndex(index: SpectralIndex): string[] {
  if (index.type === 'rgb') {
    // TCI uses 'visual' asset — handled separately in the card
    return [];
  }
  if (index.bands && index.bands.length > 0) {
    return index.bands.map(b => index.bandMapping[b] || b);
  }
  if (index.bandA && index.bandB) {
    // Order: [bandB, bandA] so band1=B, band2=A in the style
    // ND = (band2 - band1) / (band2 + band1) = (A - B) / (A + B)
    return [
      index.bandMapping[index.bandB] || index.bandB,
      index.bandMapping[index.bandA] || index.bandA,
    ];
  }
  return [];
}

/**
 * Checks if a collection supports spectral index rendering.
 */
/**
 * Coleções com assets COG acessíveis via HTTPS para renderização por bandas.
 * Excluídas:
 *   - sentinel-2-l1c: assets JP2, não COG
 *   - landsat-c2-l2: bucket requester-pays (403)
 *   - naip: bucket requester-pays (403)
 */
export function collectionSupportsBands(collectionId: string): boolean {
  const supported = [
    'sentinel-2-l2a', 'sentinel-2-c1-l2a',
  ];
  return supported.includes(collectionId);
}
