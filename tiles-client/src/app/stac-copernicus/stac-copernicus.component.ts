import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { StacService } from '../stac/services/stac.service';
import { StacItem, StacSearchParams, StacCollection, QueryableProperty } from '../stac/models/stac.models';
import { SPECTRAL_INDICES } from '../stac/models/spectral-indices';
import { ScreenStateConfig, ScreenStateBinder } from '../screen-state/interfaces/screen-state.interfaces';
import { ScreenStateService } from '../screen-state/services/screen-state.service';
import { bindState } from '../screen-state/helpers/manual-state.helper';
import { PointService } from '../grid-map/services/point.service';
import { Cql2Filter } from './components/cql2-filter/cql2-filter.component';
import { QueryableFilterValues } from './components/queryable-filter/queryable-filter.component';

const COPERNICUS_BASE_URL = 'https://catalogue.dataspace.copernicus.eu/stac';

/** Collections most commonly used — shown as quick-pick buttons */
const POPULAR_COLLECTIONS = [
  'SENTINEL-2',
  'SENTINEL-1',
  'SENTINEL-3',
  'SENTINEL-5P',
  'LANDSAT-8',
];

export interface CollectionGroupItem {
  id: string;
  title: string;
  description: string;
  selected: boolean;
}

export interface CollectionGroup {
  label: string;
  icon: string;
  expanded: boolean;
  items: CollectionGroupItem[];
  matchCount: number; // number of items matching search filter
}

const COPERNICUS_STATE_CONFIG: ScreenStateConfig = {
  screenKey: 'stac-copernicus',
  group: 'stac',
  strategy: 'storage-only',
  debounceMs: 400,
  ttlMs: 7 * 24 * 60 * 60 * 1000,
  schemaVersion: 3,
  fields: {
    selectedCollections: { type: 'string[]', defaultValue: [] },
    startDate: { type: 'date' },
    endDate: { type: 'date' },
    maxCloud: { type: 'number', defaultValue: 100 },
    limit: { type: 'number', defaultValue: 20 },
    gridSize: { type: 'number', defaultValue: 2 },
    basemap: { type: 'string', defaultValue: 'dark' },
  }
};

@Component({
  selector: 'app-stac-copernicus',
  templateUrl: './stac-copernicus.component.html',
  styleUrls: ['./stac-copernicus.component.scss'],
})
export class StacCopernicusComponent implements OnInit, OnDestroy {
  // Collections
  allCollections: StacCollection[] = [];
  collectionGroups: CollectionGroup[] = [];
  selectedCollections: string[] = [];
  collectionsLoading: boolean = false;
  collectionSearchText: string = '';
  showCollectionBrowser: boolean = false;

  // Date range
  startDate: Date | null = null;
  endDate: Date | null = null;

  // Filters
  maxCloud: number = 100;
  limit: number = 20;

  // Queryables
  queryables: Record<string, QueryableProperty> = {};
  queryablesLoading: boolean = false;
  collectionSummaries: Record<string, any> = {};

  // CQL2 filter
  cql2Filter: Cql2Filter | null = null;
  queryableFilterValues: QueryableFilterValues = {};

  // Grid
  gridSize: 1 | 2 | 3 = 2;
  basemap: string = 'dark';

  spectralIndex = SPECTRAL_INDICES[0];

  // AOI
  currentBbox: [number, number, number, number] | null = null;
  aoiGeometry: GeoJSON.Geometry | null = null;
  activeFeature: Feature<Geometry> | null = null;

  // Results
  items: StacItem[] = [];
  totalResults: number = 0;
  loading: boolean = false;
  searched: boolean = false;
  errorMessage: string | null = null;

  // Pagination
  nextPageUrl: string | null = null;
  loadingMore: boolean = false;

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
      COPERNICUS_STATE_CONFIG,
      this.route, this.router, this.screenStateService,
      {
        selectedCollections: [],
        startDate: defaultStart,
        endDate: defaultEnd,
        maxCloud: 100,
        limit: 20,
        gridSize: 2,
        basemap: 'dark',
      }
    );

    const restored = this.stateBinder.restore();
    this.selectedCollections = restored.selectedCollections || [];
    this.startDate = restored.startDate;
    this.endDate = restored.endDate;
    this.maxCloud = restored.maxCloud;
    this.limit = restored.limit;
    this.gridSize = restored.gridSize;
    this.basemap = restored.basemap;

    this.loadCollections();

    const featureSub = this.pointService.activeFeature$.subscribe(feature => {
      this.activeFeature = feature;
    });
    this.subscriptions.push(featureSub);
  }

  ngOnDestroy(): void {
    this.stateBinder.destroy();
    this.subscriptions.forEach(s => s.unsubscribe());
  }

  // ─── Collections ───────────────────────────────────────────────

  loadCollections(): void {
    this.collectionsLoading = true;
    this.stacService.getCollections(COPERNICUS_BASE_URL).subscribe({
      next: (collections) => {
        this.allCollections = collections;
        this.buildGroups(collections);
        this.collectionsLoading = false;
        if (this.selectedCollections.length > 0) {
          this.loadQueryables();
        }
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('Error loading collections:', err);
        this.collectionsLoading = false;
        this.cdr.detectChanges();
      }
    });
  }

  private buildGroups(collections: StacCollection[]): void {
    const groupMap: Record<string, CollectionGroupItem[]> = {};

    for (const col of collections) {
      const groupKey = this.inferGroup(col.id);
      if (!groupMap[groupKey]) groupMap[groupKey] = [];
      groupMap[groupKey].push({
        id: col.id,
        title: col.title || col.id,
        description: col.description?.substring(0, 120) || '',
        selected: this.selectedCollections.includes(col.id),
      });
    }

    const iconMap: Record<string, string> = {
      'Sentinel-1': 'pi pi-wifi',
      'Sentinel-2': 'pi pi-sun',
      'Sentinel-3': 'pi pi-globe',
      'Sentinel-5P': 'pi pi-cloud',
      'Landsat': 'pi pi-image',
      'DEM': 'pi pi-chart-bar',
      'Envisat': 'pi pi-star',
      'MODIS': 'pi pi-eye',
      'Outros': 'pi pi-folder',
    };

    this.collectionGroups = Object.entries(groupMap)
      .map(([label, items]) => ({
        label,
        icon: iconMap[label] || 'pi pi-folder',
        expanded: items.some(i => i.selected),
        items: items.sort((a, b) => a.title.localeCompare(b.title)),
        matchCount: items.length,
      }))
      .sort((a, b) => {
        // Sentinel groups first, then alphabetical
        const order = ['Sentinel-1', 'Sentinel-2', 'Sentinel-3', 'Sentinel-5P', 'Landsat', 'DEM'];
        const ia = order.indexOf(a.label);
        const ib = order.indexOf(b.label);
        if (ia >= 0 && ib >= 0) return ia - ib;
        if (ia >= 0) return -1;
        if (ib >= 0) return 1;
        return a.label.localeCompare(b.label);
      });
  }

  private inferGroup(collectionId: string): string {
    const id = collectionId.toLowerCase();
    if (id.includes('sentinel-1')) return 'Sentinel-1';
    if (id.includes('sentinel-2')) return 'Sentinel-2';
    if (id.includes('sentinel-3')) return 'Sentinel-3';
    if (id.includes('sentinel-5')) return 'Sentinel-5P';
    if (id.includes('landsat')) return 'Landsat';
    if (id.includes('dem') || id.includes('cop-dem')) return 'DEM';
    if (id.includes('envisat') || id.includes('meris')) return 'Envisat';
    if (id.includes('modis')) return 'MODIS';
    return 'Outros';
  }

  // ─── Collection interaction ────────────────────────────────────

  toggleCollection(item: CollectionGroupItem): void {
    item.selected = !item.selected;
    this.syncSelectedFromGroups();
  }

  quickSelectGroup(groupLabel: string): void {
    // Find collections that match this group prefix
    const matchingIds = this.allCollections
      .filter(c => this.inferGroup(c.id) === groupLabel || c.id.toUpperCase().startsWith(groupLabel))
      .map(c => c.id);

    if (matchingIds.length === 0) return;

    // If the first match is already selected, deselect all in group. Otherwise select the first one.
    const firstMatch = matchingIds[0];
    if (this.selectedCollections.includes(firstMatch)) {
      this.selectedCollections = this.selectedCollections.filter(id => !matchingIds.includes(id));
    } else {
      // Select just the first (most common) collection from that group
      if (!this.selectedCollections.includes(firstMatch)) {
        this.selectedCollections = [...this.selectedCollections, firstMatch];
      }
    }
    this.syncGroupsFromSelected();
    this.onCollectionChange();
  }

  removeCollection(collectionId: string): void {
    this.selectedCollections = this.selectedCollections.filter(id => id !== collectionId);
    this.syncGroupsFromSelected();
    this.onCollectionChange();
  }

  isGroupQuickSelected(groupLabel: string): boolean {
    return this.selectedCollections.some(id => this.inferGroup(id) === groupLabel);
  }

  /** Called from template checkbox onChange */
  syncSelectedFromGroupsPublic(): void {
    this.syncSelectedFromGroups();
  }

  clearCollections(): void {
    this.selectedCollections = [];
    this.syncGroupsFromSelected();
    this.onCollectionChange();
  }

  selectAllInGroup(group: CollectionGroup): void {
    const visibleItems = this.getVisibleItems(group);
    const allSelected = visibleItems.every(i => i.selected);
    for (const item of visibleItems) {
      item.selected = !allSelected;
    }
    this.syncSelectedFromGroups();
  }

  getCollectionTitle(collectionId: string): string {
    const col = this.allCollections.find(c => c.id === collectionId);
    return col?.title || collectionId;
  }

  getCollectionShortTitle(collectionId: string): string {
    const title = this.getCollectionTitle(collectionId);
    return title.length > 25 ? title.substring(0, 22) + '...' : title;
  }

  // ─── Search/filter collections ─────────────────────────────────

  onSearchTextChange(): void {
    const search = this.collectionSearchText.toLowerCase().trim();
    for (const group of this.collectionGroups) {
      group.matchCount = 0;
      for (const item of group.items) {
        const matches = !search ||
          item.id.toLowerCase().includes(search) ||
          item.title.toLowerCase().includes(search);
        if (matches) group.matchCount++;
      }
      // Auto-expand groups with matches when searching
      if (search && group.matchCount > 0) {
        group.expanded = true;
      }
    }
  }

  getVisibleItems(group: CollectionGroup): CollectionGroupItem[] {
    const search = this.collectionSearchText.toLowerCase().trim();
    if (!search) return group.items;
    return group.items.filter(item =>
      item.id.toLowerCase().includes(search) ||
      item.title.toLowerCase().includes(search)
    );
  }

  isGroupVisible(group: CollectionGroup): boolean {
    return group.matchCount > 0;
  }

  toggleGroupExpand(group: CollectionGroup): void {
    group.expanded = !group.expanded;
  }

  // ─── Sync helpers ──────────────────────────────────────────────

  private syncSelectedFromGroups(): void {
    this.selectedCollections = [];
    for (const group of this.collectionGroups) {
      for (const item of group.items) {
        if (item.selected) {
          this.selectedCollections.push(item.id);
        }
      }
    }
    this.onCollectionChange();
  }

  private syncGroupsFromSelected(): void {
    for (const group of this.collectionGroups) {
      for (const item of group.items) {
        item.selected = this.selectedCollections.includes(item.id);
      }
    }
  }

  onCollectionChange(): void {
    this.persistFilters();
    if (this.selectedCollections.length === 1) {
      this.loadQueryables();
    } else {
      this.queryables = {};
      this.collectionSummaries = {};
    }
  }

  // ─── Queryables ────────────────────────────────────────────────

  loadQueryables(): void {
    if (this.selectedCollections.length !== 1) return;
    this.queryablesLoading = true;
    this.stacService.getQueryables(COPERNICUS_BASE_URL, this.selectedCollections[0]).subscribe({
      next: (response) => {
        this.queryables = response.properties || {};
        this.queryablesLoading = false;
        const col = this.allCollections.find(c => c.id === this.selectedCollections[0]);
        this.collectionSummaries = col?.summaries || {};
        this.cdr.detectChanges();
      },
      error: () => {
        this.queryables = {};
        this.queryablesLoading = false;
        this.cdr.detectChanges();
      }
    });
  }

  // ─── Filters ───────────────────────────────────────────────────

  onCql2FilterChange(filter: Cql2Filter | null): void { this.cql2Filter = filter; }
  onQueryableValuesChange(values: QueryableFilterValues): void { this.queryableFilterValues = values; }
  onFilterChange(): void { this.persistFilters(); }

  // ─── AOI ───────────────────────────────────────────────────────

  onBboxChange(bbox: [number, number, number, number]): void { this.currentBbox = bbox; }
  onGeometryChange(geometry: GeoJSON.Geometry | null): void { this.aoiGeometry = geometry; }

  // ─── Search ────────────────────────────────────────────────────

  get canSearch(): boolean {
    return this.selectedCollections.length > 0 && !!this.currentBbox && !!this.startDate && !!this.endDate;
  }

  get missingRequirements(): string[] {
    const missing: string[] = [];
    if (this.selectedCollections.length === 0) missing.push('Coleções');
    if (!this.currentBbox) missing.push('Área de interesse');
    if (!this.startDate) missing.push('Data de início');
    if (!this.endDate) missing.push('Data de fim');
    return missing;
  }

  searchImages(): void {
    if (!this.canSearch) return;

    this.loading = true;
    this.searched = true;
    this.errorMessage = null;
    this.items = [];
    this.nextPageUrl = null;

    const datetime = `${this.formatDate(this.startDate!)}T00:00:00Z/${this.formatDate(this.endDate!)}T23:59:59Z`;

    const params: StacSearchParams = {
      collections: this.selectedCollections,
      datetime,
      limit: this.limit,
    };

    if (this.aoiGeometry) {
      params.intersects = this.aoiGeometry;
    } else {
      params.bbox = this.currentBbox!;
    }

    // Build CQL2 filter
    const cql2Args: any[] = [];

    if (this.maxCloud < 100) {
      cql2Args.push({ op: '<=', args: [{ property: 'eo:cloud_cover' }, this.maxCloud] });
    }

    for (const [key, value] of Object.entries(this.queryableFilterValues)) {
      cql2Args.push({ op: '=', args: [{ property: key }, value] });
    }

    if (this.cql2Filter) {
      cql2Args.push(this.cql2Filter);
    }

    if (cql2Args.length > 0) {
      params.filter = cql2Args.length === 1 ? cql2Args[0] : { op: 'and', args: cql2Args };
      params['filter-lang'] = 'cql2-json';
    }

    this.stacService.search(COPERNICUS_BASE_URL, params).subscribe({
      next: (response) => {
        this.items = response.features;
        this.totalResults = response.context?.matched ?? response.numberMatched ?? response.features.length;
        this.nextPageUrl = this.extractNextUrl(response.links);
        this.loading = false;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('Copernicus search error:', err);
        this.loading = false;
        this.items = [];
        this.totalResults = 0;
        const detail = err.error?.description || err.error?.detail || err.message || '';
        this.errorMessage = detail
          ? `Erro na busca: ${detail}`
          : 'Erro ao buscar imagens no Copernicus. Verifique sua conexão e tente novamente.';
        this.cdr.detectChanges();
      }
    });
  }

  loadMore(): void {
    if (!this.nextPageUrl || this.loadingMore) return;
    this.loadingMore = true;
    this.stacService.getNextPage(this.nextPageUrl).subscribe({
      next: (response) => {
        this.items = [...this.items, ...response.features];
        this.nextPageUrl = this.extractNextUrl(response.links);
        this.loadingMore = false;
        this.cdr.detectChanges();
      },
      error: () => { this.loadingMore = false; this.cdr.detectChanges(); }
    });
  }

  clearResults(): void {
    this.items = [];
    this.totalResults = 0;
    this.searched = false;
    this.errorMessage = null;
    this.nextPageUrl = null;
  }

  getItemDate(item: StacItem): string {
    return item.properties.datetime?.split('T')[0] || 'N/A';
  }

  getItemCloud(item: StacItem): string {
    const cloud = item.properties['eo:cloud_cover'];
    return cloud !== undefined && cloud !== null ? cloud.toFixed(1) + '%' : 'N/A';
  }

  getItemPlatform(item: StacItem): string {
    return item.properties.platform || 'N/A';
  }

  private extractNextUrl(links: { rel: string; href: string }[]): string | null {
    return links?.find(l => l.rel === 'next')?.href || null;
  }

  private persistFilters(): void {
    this.stateBinder.patchAndPersist({
      selectedCollections: this.selectedCollections,
      startDate: this.startDate,
      endDate: this.endDate,
      maxCloud: this.maxCloud,
      limit: this.limit,
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
