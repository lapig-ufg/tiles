// Enums alinhados 1:1 com schemas.py do backend

export type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export type ProductType =
  | 'rgb_embedding'
  | 'pca'
  | 'clusters'
  | 'magnitude'
  | 'change_detection';

export type RoiType = 'bbox' | 'polygon' | 'feature_collection';

export type ExportFormat = 'COG' | 'GeoTIFF' | 'CSV' | 'Parquet' | 'JSON';

// Request DTOs

export interface RoiConfig {
  roi_type: RoiType;
  bbox?: number[];       // [west, south, east, north]
  geojson?: any;
}

export interface ProcessingConfig {
  scale: number;
  crs: string;
  tile_scale: number;
  best_effort: boolean;
  max_pixels: number;
  sample_size: number;
}

export interface ProductConfig {
  product: ProductType;
  rgb_bands?: number[];
  pca_components?: number;
  kmeans_k?: number;
  palette?: string[];
  vis_min?: number;
  vis_max?: number;
  year_b?: number;
}

export interface JobCreateRequest {
  name: string;
  description?: string;
  year: number;
  roi: RoiConfig;
  processing?: ProcessingConfig;
  products: ProductConfig[];
}

export interface ExportRequest {
  products: ProductType[];
  formats: ExportFormat[];
  scale?: number;
  export_target?: string;
}

// Response DTOs

export interface ProductResult {
  product: ProductType;
  status: JobStatus;
  tile_url_template?: string;
  metadata: Record<string, any>;
}

export interface ArtifactInfo {
  id: string;
  filename: string;
  format: ExportFormat;
  size_bytes?: number;
  download_url?: string;
  product: ProductType;
  status: string;
  created_at: string;
}

export interface JobResponse {
  id: string;
  name: string;
  description?: string;
  config: Record<string, any>;
  status: JobStatus;
  progress: number;
  message?: string;
  products: ProductResult[];
  artifacts: ArtifactInfo[];
  created_at: string;
  updated_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface JobListResponse {
  items: JobResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface StatsResponse {
  job_id: string;
  product: ProductType;
  bands: Array<Record<string, any>>;
  total_pixels: number;
  coverage: number;
}

export interface PreviewResponse {
  job_id: string;
  product: string;
  tile_url_template: string;
  bounds?: { west: number; south: number; east: number; north: number };
}

// UI helpers

export interface ProductOption {
  label: string;
  value: ProductType;
  description: string;
  icon: string;
}

export const PRODUCT_OPTIONS: ProductOption[] = [
  {label: 'RGB Embedding',    value: 'rgb_embedding',     description: '3 bandas mapeadas em RGB', icon: 'pi-palette'},
  {label: 'PCA',              value: 'pca',               description: 'Componentes principais',   icon: 'pi-chart-line'},
  {label: 'Clusters',         value: 'clusters',          description: 'Agrupamento KMeans',       icon: 'pi-th-large'},
  {label: 'Magnitude',        value: 'magnitude',         description: 'Magnitude do vetor',       icon: 'pi-bolt'},
  {label: 'Change Detection', value: 'change_detection',  description: 'Similaridade entre anos',  icon: 'pi-sync'},
];

export const YEAR_OPTIONS = Array.from({length: 8}, (_, i) => ({
  label: String(2017 + i),
  value: 2017 + i,
}));

export const PRESET_CONFIGS = {
  FAST: {label: 'Rapido', scale: 30, sample_size: 1000, tile_scale: 4},
  STANDARD: {label: 'Padrao', scale: 10, sample_size: 5000, tile_scale: 4},
  DETAILED: {label: 'Detalhado', scale: 10, sample_size: 10000, tile_scale: 8},
};
