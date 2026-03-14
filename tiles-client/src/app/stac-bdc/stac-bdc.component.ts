import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { StacService } from '../stac/services/stac.service';
import { StacItem, StacSearchParams } from '../stac/models/stac.models';
import { SpectralIndex } from '../stac/models/spectral-indices';
import { ScreenStateConfig, ScreenStateBinder } from '../screen-state/interfaces/screen-state.interfaces';
import { ScreenStateService } from '../screen-state/services/screen-state.service';
import { bindState } from '../screen-state/helpers/manual-state.helper';
import { PointService } from '../grid-map/services/point.service';
import {
  BdcCollectionConfig, BdcCollectionGroup,
  BDC_COLLECTIONS, BDC_COLLECTION_GROUPS,
  getBdcCollectionConfig, getTemporalLabel,
} from './models/bdc-collections';
import { getBdcSpectralIndices } from './models/bdc-spectral-indices';

const BDC_BASE_URL = 'https://data.inpe.br/bdc/stac/v1';

const PERIOD_PRESETS = [
  { label: '16d', days: 16 },
  { label: '1m', days: 30 },
  { label: '3m', days: 90 },
  { label: '6m', days: 180 },
  { label: '1a', days: 365 },
  { label: '2a', days: 730 },
];

const BDC_STATE_CONFIG: ScreenStateConfig = {
  screenKey: 'stac-bdc',
  group: 'stac',
  strategy: 'storage-only',
  debounceMs: 400,
  ttlMs: 7 * 24 * 60 * 60 * 1000,
  schemaVersion: 1,
  fields: {
    selectedCollection: { type: 'string', defaultValue: 'CB4A-WFI-L4-SR-1' },
    startDate: { type: 'date' },
    endDate: { type: 'date' },
    maxCloud: { type: 'number', defaultValue: 30 },
    limit: { type: 'number', defaultValue: 6 },
    selectedIndex: { type: 'string', defaultValue: 'TCI' },
    gridSize: { type: 'number', defaultValue: 2 },
    basemap: { type: 'string', defaultValue: 'dark' },
  }
};

@Component({
  selector: 'app-stac-bdc',
  templateUrl: './stac-bdc.component.html',
  styleUrls: ['./stac-bdc.component.scss'],
})
export class StacBdcComponent implements OnInit, OnDestroy {
  collections = BDC_COLLECTIONS;
  collectionGroups = BDC_COLLECTION_GROUPS;
  selectedCollection: string = 'CB4A-WFI-L4-SR-1';

  startDate: Date | null = null;
  endDate: Date | null = null;
  calendarMinDate: Date = new Date(2016, 0, 1);
  calendarMaxDate: Date = new Date();
  periodPresets = PERIOD_PRESETS;

  maxCloud: number = 30;
  limit: number = 6;

  spectralIndices: SpectralIndex[] = [];
  selectedIndex: string = 'TCI';

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
    this.stateBinder = bindState(
      BDC_STATE_CONFIG,
      this.route, this.router, this.screenStateService,
      {
        selectedCollection: 'CB4A-WFI-L4-SR-1',
        startDate: null,
        endDate: null,
        maxCloud: 30,
        limit: 6,
        selectedIndex: 'TCI',
        gridSize: 2,
        basemap: 'dark',
      }
    );

    const restored = this.stateBinder.restore();
    this.selectedCollection = restored.selectedCollection;
    this.maxCloud = restored.maxCloud;
    this.limit = restored.limit;
    this.selectedIndex = restored.selectedIndex;
    this.gridSize = restored.gridSize;
    this.basemap = restored.basemap;

    // Aplica auto-date baseado na coleção (sobrescreve dates restaurados)
    this.applyAutoDate();
    this.updateAvailableIndices();

    // Se havia datas salvas válidas dentro do range, restaura-as
    if (restored.startDate && restored.endDate) {
      const restoredStart = new Date(restored.startDate);
      const restoredEnd = new Date(restored.endDate);
      if (restoredStart >= this.calendarMinDate && restoredEnd <= this.calendarMaxDate) {
        this.startDate = restoredStart;
        this.endDate = restoredEnd;
      }
    }

    const featureSub = this.pointService.activeFeature$.subscribe(feature => {
      this.activeFeature = feature;
    });
    this.subscriptions.push(featureSub);
  }

  ngOnDestroy(): void {
    this.stateBinder.destroy();
    this.subscriptions.forEach(s => s.unsubscribe());
  }

  // ─── Getters ──────────────────────────────────────────────────

  get currentCollectionConfig(): BdcCollectionConfig | undefined {
    return getBdcCollectionConfig(this.selectedCollection);
  }

  get currentSpectralIndex(): SpectralIndex {
    return this.spectralIndices.find(i => i.id === this.selectedIndex) || this.spectralIndices[0];
  }

  get hasBands(): boolean {
    return this.currentCollectionConfig?.hasBands ?? false;
  }

  get supportsCloudFilter(): boolean {
    return this.currentCollectionConfig?.supportsCloudFilter ?? false;
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

  // ─── Collection helpers ───────────────────────────────────────

  getCollectionConfig(id: string): BdcCollectionConfig | undefined {
    return getBdcCollectionConfig(id);
  }

  getTemporalLabel(config: BdcCollectionConfig): string {
    return getTemporalLabel(config);
  }

  getGroupCollections(group: BdcCollectionGroup): BdcCollectionConfig[] {
    return group.collections
      .map(id => getBdcCollectionConfig(id))
      .filter((c): c is BdcCollectionConfig => !!c);
  }

  // ─── Event handlers ───────────────────────────────────────────

  onCollectionChange(): void {
    this.applyAutoDate();
    this.updateAvailableIndices();
    this.persistFilters();
  }

  onFilterChange(): void {
    this.persistFilters();
  }

  onIndexChange(): void {
    this.persistFilters();
  }

  applyPeriodPreset(preset: { label: string; days: number }): void {
    const now = new Date();
    const proposedEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const proposedStart = new Date(now.getTime() - preset.days * 24 * 60 * 60 * 1000);

    // Clamp ao range temporal da coleção
    this.startDate = proposedStart < this.calendarMinDate ? this.calendarMinDate : proposedStart;
    this.endDate = proposedEnd > this.calendarMaxDate ? this.calendarMaxDate : proposedEnd;
    this.persistFilters();
  }

  onBboxChange(bbox: [number, number, number, number]): void {
    this.currentBbox = bbox;
  }

  onGeometryChange(geometry: GeoJSON.Geometry | null): void {
    this.aoiGeometry = geometry;
  }

  // ─── Search ───────────────────────────────────────────────────

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

    if (this.aoiGeometry) {
      params.intersects = this.aoiGeometry;
    } else {
      params.bbox = this.currentBbox!;
    }

    if (this.supportsCloudFilter) {
      params.query = { 'eo:cloud_cover': { lte: this.maxCloud } };
    }

    params.sortby = [{ field: 'properties.datetime', direction: 'desc' }];

    this.stacService.search(BDC_BASE_URL, params).subscribe({
      next: (response) => {
        this.items = response.features;
        this.totalResults = response.context?.matched ?? response.numberMatched ?? response.features.length;
        this.items.sort((a, b) => {
          const dateA = a.properties.datetime || '';
          const dateB = b.properties.datetime || '';
          return dateB.localeCompare(dateA);
        });
        this.loading = false;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('BDC STAC search error:', err);
        this.loading = false;
        this.items = [];
        this.totalResults = 0;
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

  // ─── Private ──────────────────────────────────────────────────

  private applyAutoDate(): void {
    const config = this.currentCollectionConfig;
    if (!config) return;

    this.calendarMinDate = new Date(config.temporalStart);
    this.calendarMaxDate = config.temporalEnd ? new Date(config.temporalEnd) : new Date();

    const oneYearAgo = new Date();
    oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);
    this.startDate = this.calendarMinDate > oneYearAgo ? this.calendarMinDate : oneYearAgo;
    this.endDate = this.calendarMaxDate;
  }

  private updateAvailableIndices(): void {
    const config = this.currentCollectionConfig;
    if (config?.hasBands) {
      this.spectralIndices = getBdcSpectralIndices(config);
      if (!this.spectralIndices.find(i => i.id === this.selectedIndex)) {
        this.selectedIndex = 'TCI';
      }
    } else {
      this.spectralIndices = [];
      this.selectedIndex = 'TCI';
    }
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
