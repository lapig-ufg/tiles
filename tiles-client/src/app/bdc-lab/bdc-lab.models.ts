export type Bbox = [number, number, number, number];
export type ImageFormat = 'png' | 'jpeg';
export interface BdcAsset { href: string; type?: string; title?: string; roles?: string[]; 'eo:bands'?: Record<string, unknown>[]; }
export interface BdcItem {
    id: string;
    collection: string;
    bbox: Bbox;
    geometry: GeoJSON.Geometry;
    properties: Record<string, unknown> & { datetime?: string; 'eo:cloud_cover'?: number };
    assets: Record<string, BdcAsset>;
}
export interface BdcCollection {
    id: string; title: string; description: string; family: string; license: string;
    extent: { spatial?: { bbox: number[][] }; temporal?: { interval: (string | null)[][] } };
    summaries: Record<string, unknown>;
    providers: { name: string; roles?: string[]; url?: string }[];
    bands: Record<string, unknown>[];
    formats: string[]; has_cog: boolean; public: boolean | null;
}
export interface BdcProfile {
    id: string; label: string; bands: string[]; min: number[]; max: number[];
    gamma: number; scale: number; offset: number; ndvi: boolean; fidelity: string; note: string;
}
export interface ItemDetail { item: BdcItem; collection: BdcCollection; profiles: BdcProfile[]; }
export interface SearchResult { items: BdcItem[]; page: number; next_page: number | null; matched: number | null; returned: number; }
export interface SearchFilters { collection: string; bbox: Bbox; start: string; end: string; max_cloud: number; page: number; limit: number; }
export interface RenderSelection { collection: string; item: BdcItem; profile: BdcProfile; format: ImageFormat; quality: number; background: 'white' | 'black'; }
export interface TileStats { completed: number; hits: number; transferred: number; ranges: number; serverMs: number; errors: number; }
export interface WarmResult { completed: number; results: { cache: string; ms: number; ranges: number; bytes: number }[]; }

export function safeBdcUrl(value: string | undefined): string | null {
    if (!value) return null;
    try {
        const url = new URL(value);
        return url.protocol === 'https:' && url.hostname === 'data.inpe.br' && !url.username && !url.password && (!url.port || url.port === '443') ? url.href : null;
    } catch { return null; }
}

export function emptyStats(): TileStats {
    return { completed: 0, hits: 0, transferred: 0, ranges: 0, serverMs: 0, errors: 0 };
}

export interface PointQualityRow {
    id: string; item: BdcItem; eligible: boolean; scene_cloud_pct: number | null;
    cloud_pct?: number | null; shadow_pct?: number | null; unusable_pct?: number | null;
    valid_pct?: number; reason?: string;
}
export interface PointQualityResult {
    start?: string; end?: string;
    lon: number; lat: number; radius_m: number;
    best_local_id: string | null; best_global_id: string | null; evaluated: number;
    eligible: number; seconds: number; rows: PointQualityRow[]; geometry: GeoJSON.Geometry;
}
