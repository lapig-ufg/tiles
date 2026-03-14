// GeoJSON types
export interface StacBbox {
  // [west, south, east, north]
  0: number; 1: number; 2: number; 3: number;
}

export interface StacLink {
  rel: string;
  href: string;
  type?: string;
  title?: string;
}

export interface StacAsset {
  href: string;
  type?: string;
  title?: string;
  roles?: string[];
  'eo:bands'?: EoBand[];
}

export interface EoBand {
  name: string;
  common_name?: string;
  center_wavelength?: number;
  full_width_half_max?: number;
}

export interface StacItemProperties {
  datetime: string | null;
  start_datetime?: string;
  end_datetime?: string;
  'eo:cloud_cover'?: number;
  platform?: string;
  'sat:relative_orbit'?: number;
  constellation?: string;
  'proj:epsg'?: number;
  'view:sun_azimuth'?: number;
  'view:sun_elevation'?: number;
  'sar:polarizations'?: string[];
  'sar:instrument_mode'?: string;
  created?: string;
  updated?: string;
  [key: string]: any;
}

export interface StacItem {
  type: 'Feature';
  stac_version: string;
  stac_extensions?: string[];
  id: string;
  geometry: GeoJSON.Geometry;
  bbox: number[];
  properties: StacItemProperties;
  assets: Record<string, StacAsset>;
  links: StacLink[];
  collection?: string;
}

export interface StacExtent {
  spatial: {
    bbox: number[][];
  };
  temporal: {
    interval: (string | null)[][];
  };
}

export interface StacCollection {
  type: 'Collection';
  id: string;
  title?: string;
  description: string;
  extent: StacExtent;
  license: string;
  links: StacLink[];
  summaries?: Record<string, any>;
  'item_assets'?: Record<string, StacAsset>;
  keywords?: string[];
}

export interface StacCollectionsResponse {
  collections: StacCollection[];
  links: StacLink[];
}

export interface StacSearchParams {
  collections?: string[];
  bbox?: number[];
  intersects?: GeoJSON.Geometry;
  datetime?: string;
  limit?: number;
  query?: Record<string, any>;
  filter?: any;
  'filter-lang'?: 'cql2-json' | 'cql2-text';
  sortby?: StacSortBy[];
  fields?: StacFields;
  token?: string;
}

export interface StacSortBy {
  field: string;
  direction: 'asc' | 'desc';
}

export interface StacFields {
  includes?: string[];
  excludes?: string[];
}

export interface StacSearchContext {
  matched?: number;
  returned: number;
  limit: number;
}

export interface StacSearchResponse {
  type: 'FeatureCollection';
  features: StacItem[];
  links: StacLink[];
  context?: StacSearchContext;
  numberMatched?: number;
  numberReturned?: number;
}

export interface QueryableProperty {
  title?: string;
  description?: string;
  type: string;
  enum?: any[];
  minimum?: number;
  maximum?: number;
  format?: string;
}

export interface QueryablesResponse {
  type: string;
  title?: string;
  description?: string;
  properties: Record<string, QueryableProperty>;
  '$id'?: string;
}

/**
 * Converte URLs de assets para formatos acessíveis pelo navegador.
 *
 * Transformações:
 * - s3://bucket/key → https://bucket.s3.amazonaws.com/key
 * - https://data.inpe.br/... → /api/cog-proxy?url=<encoded> (proxy CORS)
 */
export function sanitizeAssetUrl(url: string): string {
  if (!url) return url;

  // S3 scheme → HTTPS
  const s3Match = url.match(/^s3:\/\/([^/]+)\/(.+)$/);
  if (s3Match) {
    return `https://${s3Match[1]}.s3.amazonaws.com/${s3Match[2]}`;
  }

  // BDC (data.inpe.br) — servidor sem CORS, roteado via proxy backend
  if (url.includes('data.inpe.br')) {
    return `/api/cog-proxy?url=${encodeURIComponent(url)}`;
  }

  return url;
}
