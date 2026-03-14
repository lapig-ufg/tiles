export interface BdcCollectionConfig {
  id: string;
  label: string;
  satellite: string;
  sensor: string;
  resolution: string;
  hasBands: boolean;
  supportsCloudFilter: boolean;
  temporalStart: string;
  temporalEnd: string | null;
  bandMapping: Record<string, string>;
}

export interface BdcCollectionGroup {
  label: string;
  icon: string;
  collections: string[];
}

export const BDC_COLLECTIONS: BdcCollectionConfig[] = [
  // CBERS-4A
  {
    id: 'CB4A-WFI-L4-SR-1',
    label: 'CBERS-4A WFI SR',
    satellite: 'CBERS-4A',
    sensor: 'WFI',
    resolution: '64m',
    hasBands: true,
    supportsCloudFilter: true,
    temporalStart: '2020-01-01',
    temporalEnd: '2026-01-01',
    bandMapping: { blue: 'BAND13', green: 'BAND14', red: 'BAND15', nir: 'BAND16' },
  },
  // CBERS-4
  {
    id: 'CB4-WFI-L4-SR-1',
    label: 'CBERS-4 WFI SR',
    satellite: 'CBERS-4',
    sensor: 'WFI',
    resolution: '64m',
    hasBands: true,
    supportsCloudFilter: true,
    temporalStart: '2016-01-01',
    temporalEnd: '2026-01-01',
    bandMapping: { blue: 'BAND13', green: 'BAND14', red: 'BAND15', nir: 'BAND16' },
  },
  {
    id: 'CB4-MUX-L4-SR-1',
    label: 'CBERS-4 MUX SR',
    satellite: 'CBERS-4',
    sensor: 'MUX',
    resolution: '20m',
    hasBands: true,
    supportsCloudFilter: true,
    temporalStart: '2016-01-01',
    temporalEnd: '2026-02-01',
    bandMapping: { blue: 'BAND5', green: 'BAND6', red: 'BAND7', nir: 'BAND8' },
  },
  // AMAZONIA-1
  {
    id: 'AMZ1-WFI-L4-SR-1',
    label: 'AMAZONIA-1 WFI SR',
    satellite: 'AMAZONIA-1',
    sensor: 'WFI',
    resolution: '64m',
    hasBands: true,
    supportsCloudFilter: true,
    temporalStart: '2024-01-01',
    temporalEnd: null,
    bandMapping: { blue: 'BAND1', green: 'BAND2', red: 'BAND3', nir: 'BAND4' },
  },
  // Sentinel-2 Cube (compostos 16 dias — sem eo:cloud_cover)
  {
    id: 'S2-16D-2',
    label: 'Sentinel-2 Cube 16D',
    satellite: 'Sentinel-2',
    sensor: 'MSI',
    resolution: '10m',
    hasBands: true,
    supportsCloudFilter: false,
    temporalStart: '2017-01-01',
    temporalEnd: null,
    bandMapping: {
      blue: 'B02', green: 'B03', red: 'B04', nir: 'B08',
      rededge1: 'B05', rededge2: 'B06', rededge3: 'B07',
      nir08: 'B8A', swir16: 'B11', swir22: 'B12',
    },
  },
  // Landsat Cube (compostos 16 dias — sem eo:cloud_cover)
  {
    id: 'LANDSAT-16D-1',
    label: 'Landsat Cube 16D',
    satellite: 'Landsat',
    sensor: 'OLI/TM',
    resolution: '30m',
    hasBands: true,
    supportsCloudFilter: false,
    temporalStart: '1990-01-01',
    temporalEnd: null,
    bandMapping: {
      blue: 'blue', green: 'green', red: 'red', nir: 'nir08',
      swir16: 'swir16', swir22: 'swir22',
    },
  },
  // Footprint-only (sem COG individual)
  {
    id: 'AMZ1-WFI-L2-DN-1',
    label: 'AMAZONIA-1 WFI DN',
    satellite: 'AMAZONIA-1',
    sensor: 'WFI',
    resolution: '64m',
    hasBands: false,
    supportsCloudFilter: false,
    temporalStart: '2021-01-01',
    temporalEnd: null,
    bandMapping: {},
  },
  {
    id: 'S2_L2A_BUNDLE-1',
    label: 'Sentinel-2 L2A Bundle',
    satellite: 'Sentinel-2',
    sensor: 'MSI',
    resolution: '10m',
    hasBands: false,
    supportsCloudFilter: false,
    temporalStart: '2017-01-01',
    temporalEnd: null,
    bandMapping: {},
  },
];

export const BDC_COLLECTION_GROUPS: BdcCollectionGroup[] = [
  { label: 'CBERS-4A', icon: 'pi pi-globe', collections: ['CB4A-WFI-L4-SR-1'] },
  { label: 'CBERS-4', icon: 'pi pi-globe', collections: ['CB4-WFI-L4-SR-1', 'CB4-MUX-L4-SR-1'] },
  { label: 'AMAZONIA-1', icon: 'pi pi-globe', collections: ['AMZ1-WFI-L4-SR-1'] },
  { label: 'Sentinel-2', icon: 'pi pi-image', collections: ['S2-16D-2'] },
  { label: 'Landsat', icon: 'pi pi-image', collections: ['LANDSAT-16D-1'] },
  { label: 'Outros', icon: 'pi pi-folder', collections: ['AMZ1-WFI-L2-DN-1', 'S2_L2A_BUNDLE-1'] },
];

export function getBdcCollectionConfig(collectionId: string): BdcCollectionConfig | undefined {
  return BDC_COLLECTIONS.find(c => c.id === collectionId);
}

export function getTemporalLabel(config: BdcCollectionConfig): string {
  const start = config.temporalStart.substring(0, 4);
  const end = config.temporalEnd ? config.temporalEnd.substring(0, 4) : 'atual';
  return `${start}\u2013${end}`;
}
