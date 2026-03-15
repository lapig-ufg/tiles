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
  /** Quando definido, indica que o COG é um asset RGB único (não bandas separadas). */
  rgbAssetKey?: string;
}

export interface BdcCollectionGroup {
  label: string;
  icon: string;
  collections: string[];
}

export const BDC_COLLECTIONS: BdcCollectionConfig[] = [
  // CBERS-4A WPM PCA Fused — RGB fusionado 2m, int8 (1-255, 0=nodata)
  {
    id: 'CB4A-WPM-PCA-FUSED-1',
    label: 'CBERS-4A WPM PCA Fused',
    satellite: 'CBERS-4A',
    sensor: 'WPM',
    resolution: '2m',
    hasBands: true,
    supportsCloudFilter: false,
    temporalStart: '2023-03-01',
    temporalEnd: null,
    bandMapping: { red: 'BAND1', green: 'BAND2', blue: 'BAND3' },
    rgbAssetKey: 'RGB',
  },
];

export const BDC_COLLECTION_GROUPS: BdcCollectionGroup[] = [
  { label: 'CBERS-4A', icon: 'pi pi-globe', collections: ['CB4A-WPM-PCA-FUSED-1'] },
];

export function getBdcCollectionConfig(collectionId: string): BdcCollectionConfig | undefined {
  return BDC_COLLECTIONS.find(c => c.id === collectionId);
}

export function getTemporalLabel(config: BdcCollectionConfig): string {
  const start = config.temporalStart.substring(0, 4);
  const end = config.temporalEnd ? config.temporalEnd.substring(0, 4) : 'atual';
  return `${start}\u2013${end}`;
}
