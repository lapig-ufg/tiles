import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { StacService } from '../stac/services/stac.service';
import { StacItem, StacSearchParams } from '../stac/models/stac.models';
import { SpectralIndex, SPECTRAL_INDICES, collectionSupportsBands } from '../stac/models/spectral-indices';
import { ScreenStateConfig, ScreenStateBinder } from '../screen-state/interfaces/screen-state.interfaces';
import { ScreenStateService } from '../screen-state/services/screen-state.service';
import { bindState } from '../screen-state/helpers/manual-state.helper';
import { PointService } from '../grid-map/services/point.service';

const EARTH_SEARCH_BASE_URL = 'https://earth-search.aws.element84.com/v1';

interface CollectionOption {
  id: string;
  label: string;
  resolution: string;
  free: boolean;
  hasBands: boolean;
  supportsCloudFilter: boolean;
}

const COLLECTIONS: CollectionOption[] = [
  { id: 'sentinel-2-l2a', label: 'Sentinel-2 L2A', resolution: '10m', free: true, hasBands: true, supportsCloudFilter: true },
  { id: 'sentinel-2-l1c', label: 'Sentinel-2 L1C', resolution: '10m', free: true, hasBands: true, supportsCloudFilter: true },
  { id: 'sentinel-2-c1-l2a', label: 'Sentinel-2 C1 L2A', resolution: '10m', free: true, hasBands: true, supportsCloudFilter: true },
  { id: 'landsat-c2-l2', label: 'Landsat C2 L2', resolution: '30m', free: false, hasBands: true, supportsCloudFilter: true },
  { id: 'sentinel-1-grd', label: 'Sentinel-1 GRD', resolution: '10m', free: true, hasBands: false, supportsCloudFilter: false },
  { id: 'naip', label: 'NAIP', resolution: '0.6m', free: true, hasBands: false, supportsCloudFilter: false },
  { id: 'cop-dem-glo-30', label: 'Copernicus DEM 30m', resolution: '30m', free: true, hasBands: false, supportsCloudFilter: false },
  { id: 'cop-dem-glo-90', label: 'Copernicus DEM 90m', resolution: '90m', free: true, hasBands: false, supportsCloudFilter: false },
];

const PERIOD_PRESETS = [
  { label: '16d', days: 16 },
  { label: '1m', days: 30 },
  { label: '3m', days: 90 },
  { label: '6m', days: 180 },
  { label: '1a', days: 365 },
  { label: '2a', days: 730 },
];

const EARTH_SEARCH_STATE_CONFIG: ScreenStateConfig = {
  screenKey: 'stac-earth-search',
  group: 'stac',
  strategy: 'storage-only',
  debounceMs: 400,
  ttlMs: 7 * 24 * 60 * 60 * 1000,
  schemaVersion: 2,
  fields: {
    selectedCollection: { type: 'string', defaultValue: 'sentinel-2-l2a' },
    startDate: { type: 'date' },
    endDate: { type: 'date' },
    maxCloud: { type: 'number', defaultValue: 15 },
    limit: { type: 'number', defaultValue: 6 },
    selectedIndex: { type: 'string', defaultValue: 'NDVI' },
    gridSize: { type: 'number', defaultValue: 2 },
    basemap: { type: 'string', defaultValue: 'dark' },
  }
};

@Component({
  selector: 'app-stac-earth-search',
  templateUrl: './stac-earth-search.component.html',
  styleUrls: ['./stac-earth-search.component.scss'],
})
export class StacEarthSearchComponent implements OnInit, OnDestroy {
  collections = COLLECTIONS;
  selectedCollection: string = 'sentinel-2-l2a';

  startDate: Date | null = null;
  endDate: Date | null = null;
  periodPresets = PERIOD_PRESETS;

  maxCloud: number = 15;
  limit: number = 6;

  spectralIndices = SPECTRAL_INDICES;
  selectedIndex: string = 'NDVI';

  gridSize: 1 | 2 | 3 = 2;
  basemap: string = 'dark';

  currentBbox: [number, number, number, number] | null = null;
  aoiGeometry: GeoJSON.Geometry | null = null;
  activeFeature: Feature<Geometry> | null = null;

  items: StacItem[] = [];
  totalResults: number = 0;
  loading: boolean = false;
  searched: boolean = false;
  errorMessage: string | null = null;

  private subscriptions: Subscription[] = [];
  private stateBinder!: ScreenStateBinder<any>;

  constructor(
    private stacService: StacService,
    private pointService: PointService,
    private route: ActivatedRoute,
    private router: Router,
    private screenStateService: ScreenStateService,
    private cdr: ChangeDetectorRef,
  ) {}

  ngOnInit(): void {
    const now = new Date();
    const defaultEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const defaultStart = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());

    this.stateBinder = bindState(
      EARTH_SEARCH_STATE_CONFIG,
      this.route, this.router, this.screenStateService,
      {
        selectedCollection: 'sentinel-2-l2a',
        startDate: defaultStart,
        endDate: defaultEnd,
        maxCloud: 15,
        limit: 6,
        selectedIndex: 'NDVI',
        gridSize: 2,
        basemap: 'dark',
      }
    );

    const restored = this.stateBinder.restore();
    this.selectedCollection = restored.selectedCollection;
    this.startDate = restored.startDate;
    this.endDate = restored.endDate;
    this.maxCloud = restored.maxCloud;
    this.limit = restored.limit;
    this.selectedIndex = restored.selectedIndex;
    this.gridSize = restored.gridSize;
    this.basemap = restored.basemap;

    const featureSub = this.pointService.activeFeature$.subscribe(feature => {
      this.activeFeature = feature;
    });
    this.subscriptions.push(featureSub);
  }

  ngOnDestroy(): void {
    this.stateBinder.destroy();
    this.subscriptions.forEach(s => s.unsubscribe());
  }

  get currentSpectralIndex(): SpectralIndex {
    return this.spectralIndices.find(i => i.id === this.selectedIndex) || this.spectralIndices[0];
  }

  get selectedCollectionOption(): CollectionOption {
    return this.collections.find(c => c.id === this.selectedCollection) || this.collections[0];
  }

  get hasBands(): boolean {
    return this.selectedCollectionOption.hasBands;
  }

  get supportsCloudFilter(): boolean {
    return this.selectedCollectionOption.supportsCloudFilter;
  }

  get renderMode(): 'cog' | 'footprint' {
    return this.hasBands ? 'cog' : 'footprint';
  }

  get canSearch(): boolean {
    return !!this.currentBbox && !!this.startDate && !!this.endDate;
  }

  get missingRequirements(): string[] {
    const missing: string[] = [];
    if (!this.currentBbox) missing.push('Área de interesse');
    if (!this.startDate) missing.push('Data de início');
    if (!this.endDate) missing.push('Data de fim');
    return missing;
  }

  applyPeriodPreset(preset: { label: string; days: number }): void {
    const now = new Date();
    this.endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    this.startDate = new Date(now.getTime() - preset.days * 24 * 60 * 60 * 1000);
    this.persistFilters();
  }

  onBboxChange(bbox: [number, number, number, number]): void {
    this.currentBbox = bbox;
  }

  onGeometryChange(geometry: GeoJSON.Geometry | null): void {
    this.aoiGeometry = geometry;
  }

  onCollectionChange(): void {
    if (!this.selectedCollectionOption.hasBands) {
      this.selectedIndex = 'TCI';
    }
    this.persistFilters();
  }

  onFilterChange(): void {
    this.persistFilters();
  }

  onIndexChange(): void {
    this.persistFilters();
  }

  searchImages(): void {
    if (!this.canSearch) return;

    this.loading = true;
    this.searched = true;
    this.errorMessage = null;

    const datetime = `${this.formatDate(this.startDate!)}T00:00:00Z/${this.formatDate(this.endDate!)}T23:59:59Z`;

    const params: StacSearchParams = {
      collections: [this.selectedCollection],
      datetime,
      limit: this.limit,
    };

    // Use bbox or intersects
    if (this.aoiGeometry) {
      params.intersects = this.aoiGeometry;
    } else {
      params.bbox = this.currentBbox!;
    }

    // Only add cloud filter for collections that support it
    if (this.supportsCloudFilter) {
      params.query = { 'eo:cloud_cover': { lte: this.maxCloud } };
    }

    // Add sortby — Earth Search v1 supports the STAC Sort Extension
    params.sortby = [{ field: 'properties.datetime', direction: 'desc' }];

    this.stacService.search(EARTH_SEARCH_BASE_URL, params).subscribe({
      next: (response) => {
        this.items = response.features;
        this.totalResults = response.context?.matched ?? response.numberMatched ?? response.features.length;
        // Sort client-side as fallback (ensures correct order even if sortby is ignored)
        this.items.sort((a, b) => {
          const dateA = a.properties.datetime || '';
          const dateB = b.properties.datetime || '';
          return dateB.localeCompare(dateA);
        });
        this.loading = false;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('STAC search error:', err);
        this.loading = false;
        this.items = [];
        this.totalResults = 0;
        // Try to extract a meaningful error message
        const detail = err.error?.description || err.error?.detail || err.message || '';
        this.errorMessage = detail
          ? `Erro na busca: ${detail}`
          : 'Erro ao buscar imagens. Verifique sua conexão e tente novamente.';
        this.cdr.detectChanges();
      }
    });
  }

  clearResults(): void {
    this.items = [];
    this.totalResults = 0;
    this.searched = false;
    this.errorMessage = null;
  }

  private persistFilters(): void {
    this.stateBinder.patchAndPersist({
      selectedCollection: this.selectedCollection,
      startDate: this.startDate,
      endDate: this.endDate,
      maxCloud: this.maxCloud,
      limit: this.limit,
      selectedIndex: this.selectedIndex,
      gridSize: this.gridSize,
      basemap: this.basemap,
    });
  }

  private formatDate(date: Date): string {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
}
