import { SpectralIndex, LegendStop } from '../../stac/models/spectral-indices';
import { BdcCollectionConfig } from './bdc-collections';

// ─── Shared ND color ramp ───────────────────────────────────────
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

const ND_LEGEND: LegendStop[] = [
  { value: -0.3, color: '#4198C4', label: '-0.3' },
  { value: 0.0, color: '#FFFFCC', label: '0.0' },
  { value: 0.2, color: '#AAC85F', label: '0.2' },
  { value: 0.4, color: '#4B8C2A', label: '0.4' },
  { value: 0.7, color: '#084404', label: '0.7' },
];

// ─── Style builders ─────────────────────────────────────────────

function buildPercentStretchStyle(lo: number, hi: number, gamma: number) {
  const range = hi - lo;
  return {
    color: [
      'array',
      ['clamp', ['/', ['-', ['band', 1], lo], range], 0, 1],
      ['clamp', ['/', ['-', ['band', 2], lo], range], 0, 1],
      ['clamp', ['/', ['-', ['band', 3], lo], range], 0, 1],
      1,
    ],
    gamma,
  };
}

function buildNdStyle() {
  return {
    color: [
      'interpolate', ['linear'],
      ['/', ['-', ['band', 2], ['band', 1]], ['+', ['band', 2], ['band', 1]]],
      ...ND_COLOR_RAMP,
    ],
  };
}

function buildSaviStyle() {
  return {
    color: [
      'interpolate', ['linear'],
      ['*', 1.5, ['/', ['-', ['band', 2], ['band', 1]], ['+', ['+', ['band', 2], ['band', 1]], 5000]]],
      ...ND_COLOR_RAMP,
    ],
  };
}

// ─── Helpers ────────────────────────────────────────────────────

function hasBand(config: BdcCollectionConfig, band: string): boolean {
  return band in config.bandMapping;
}

function hasAllBands(config: BdcCollectionConfig, bands: string[]): boolean {
  return bands.every(b => hasBand(config, b));
}

// ─── Factory ────────────────────────────────────────────────────

export function getBdcSpectralIndices(config: BdcCollectionConfig): SpectralIndex[] {
  const indices: SpectralIndex[] = [];
  const mapping = config.bandMapping;

  // RGB asset único (ex.: CB4A-WPM-PCA-FUSED-1) — int8, sem índices espectrais
  if (config.rgbAssetKey) {
    indices.push({
      id: 'TCI',
      label: 'True Color',
      formula: 'RGB: Red, Green, Blue',
      type: 'rgb',
      bands: [],
      bandMapping: mapping,
      style: {
        color: [
          'array',
          ['clamp', ['*', ['/', ['band', 1], 255], 1.2], 0, 1],
          ['clamp', ['*', ['/', ['band', 2], 255], 1.2], 0, 1],
          ['clamp', ['*', ['/', ['band', 3], 255], 1.2], 0, 1],
          1,
        ],
        gamma: 0.9,
      },
    });
    return indices;
  }

  // TCI — sempre disponível se tem R, G, B
  if (hasAllBands(config, ['red', 'green', 'blue'])) {
    indices.push({
      id: 'TCI',
      label: 'True Color',
      formula: 'RGB: Red, Green, Blue',
      type: 'false_color',
      bands: ['red', 'green', 'blue'],
      bandMapping: mapping,
      style: buildPercentStretchStyle(200, 3800, 0.9),
    });
  }

  // NDVI
  if (hasAllBands(config, ['nir', 'red'])) {
    indices.push({
      id: 'NDVI',
      label: 'NDVI',
      formula: '(NIR - Red) / (NIR + Red)',
      type: 'index',
      bandA: 'nir',
      bandB: 'red',
      bandMapping: mapping,
      style: buildNdStyle(),
      legend: ND_LEGEND,
    });
  }

  // NDWI
  if (hasAllBands(config, ['green', 'nir'])) {
    indices.push({
      id: 'NDWI',
      label: 'NDWI',
      formula: '(Green - NIR) / (Green + NIR)',
      type: 'index',
      bandA: 'green',
      bandB: 'nir',
      bandMapping: mapping,
      style: buildNdStyle(),
      legend: [
        { value: -0.3, color: '#4198C4', label: '-0.3' },
        { value: 0.0, color: '#FFFFCC', label: '0.0' },
        { value: 0.4, color: '#4B8C2A', label: '0.4' },
        { value: 0.8, color: '#003400', label: '0.8' },
      ],
    });
  }

  // False Color IR
  if (hasAllBands(config, ['nir', 'red', 'green'])) {
    indices.push({
      id: 'FalseIR',
      label: 'False Color IR',
      formula: 'RGB: NIR, Red, Green',
      type: 'false_color',
      bands: ['nir', 'red', 'green'],
      bandMapping: mapping,
      style: buildPercentStretchStyle(200, 3800, 0.85),
    });
  }

  // SAVI
  if (hasAllBands(config, ['nir', 'red'])) {
    indices.push({
      id: 'SAVI',
      label: 'SAVI',
      formula: '((NIR - Red) / (NIR + Red + 0.5)) x 1.5',
      type: 'savi',
      bandA: 'nir',
      bandB: 'red',
      bandMapping: mapping,
      style: buildSaviStyle(),
      legend: [
        { value: -0.2, color: '#4198C4', label: '-0.2' },
        { value: 0.0, color: '#FFFFCC', label: '0.0' },
        { value: 0.3, color: '#78A636', label: '0.3' },
        { value: 0.6, color: '#003400', label: '0.6' },
      ],
    });
  }

  // ─── Índices que requerem SWIR (Sentinel-2 Cube, Landsat Cube) ───

  // NDMI
  if (hasAllBands(config, ['nir', 'swir16'])) {
    indices.push({
      id: 'NDMI',
      label: 'NDMI',
      formula: '(NIR - SWIR1.6) / (NIR + SWIR1.6)',
      type: 'index',
      bandA: 'nir',
      bandB: 'swir16',
      bandMapping: mapping,
      style: buildNdStyle(),
      legend: [
        { value: -0.3, color: '#4198C4', label: '-0.3' },
        { value: 0.0, color: '#FFFFCC', label: '0.0' },
        { value: 0.4, color: '#4B8C2A', label: '0.4' },
        { value: 0.8, color: '#003400', label: '0.8' },
      ],
    });
  }

  // NBR
  if (hasAllBands(config, ['nir', 'swir22'])) {
    indices.push({
      id: 'NBR',
      label: 'NBR',
      formula: '(NIR - SWIR2.2) / (NIR + SWIR2.2)',
      type: 'index',
      bandA: 'nir',
      bandB: 'swir22',
      bandMapping: mapping,
      style: buildNdStyle(),
      legend: [
        { value: -0.3, color: '#4198C4', label: '-0.3' },
        { value: 0.0, color: '#FFFFCC', label: '0.0' },
        { value: 0.4, color: '#4B8C2A', label: '0.4' },
        { value: 0.7, color: '#084404', label: '0.7' },
      ],
    });
  }

  // AGRI
  if (hasAllBands(config, ['swir16', 'nir', 'blue'])) {
    indices.push({
      id: 'AGRI',
      label: 'Agriculture',
      formula: 'RGB: SWIR1.6, NIR, Blue',
      type: 'agri',
      bands: ['swir16', 'nir', 'blue'],
      bandMapping: mapping,
      style: buildPercentStretchStyle(150, 4200, 0.8),
    });
  }

  // GEO
  if (hasAllBands(config, ['swir22', 'swir16', 'blue'])) {
    indices.push({
      id: 'GEO',
      label: 'Geology',
      formula: 'RGB: SWIR2.2, SWIR1.6, Blue',
      type: 'geo',
      bands: ['swir22', 'swir16', 'blue'],
      bandMapping: mapping,
      style: buildPercentStretchStyle(150, 4200, 0.8),
    });
  }

  return indices;
}
