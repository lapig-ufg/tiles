import { BdcComparisonComponent } from './bdc-comparison.component';
import { Component, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Subscription } from 'rxjs';
import { BdcLabApiService } from './bdc-lab-api.service';
import { BdcLabMapComponent } from './bdc-lab-map.component';
import { Bbox, PointQualityResult, BdcCollection, BdcItem, BdcProfile, ImageFormat, ItemDetail, RenderSelection, SearchFilters, TileStats, emptyStats, safeBdcUrl } from './bdc-lab.models';

@Component({ selector: 'app-bdc-lab', templateUrl: './bdc-lab.component.html', styleUrls: ['./bdc-lab.component.scss'] })
export class BdcLabComponent implements OnInit, OnDestroy {
    @ViewChild(BdcComparisonComponent) pairedMaps?: BdcComparisonComponent;
    @ViewChild(BdcLabMapComponent) map?: BdcLabMapComponent;
    collections: BdcCollection[] = [];
    collectionId = 'landsat-2';
    catalogFilter = '';
    family = '';
    start = '2024-08-01';
    end = '2024-08-31';
    maxCloud = 100;
    bbox: Bbox = [-49.55, -16.85, -49.25, -16.55];
    items: BdcItem[] = [];
    detail: ItemDetail | null = null;
    selection: RenderSelection | null = null;
    profileId = '';
    format: ImageFormat = 'jpeg';
    quality = 90;
    background: 'white' | 'black' = 'white';
    catalogLoading = false;
    loading = false;
    detailLoading = false;
    online = false;
    searched = false;
    paused = false;
    error = '';
    page = 1;
    nextPage: number | null = null;
    matched: number | null = null;
    stats: TileStats = emptyStats();
    pointMode = false;
    pointLon = -49.4;
    pointLat = -16.7;
    radius = 1000;
    criterion = 'cloud';
    comparison: PointQualityResult | null = null;
    get busyMessage(): string {
        if (this.catalogLoading) return 'Carregando o catálogo do INPE…';
        if (this.loading) return this.pointMode ? 'Analisando as imagens do período no servidor…' : 'Buscando imagens no catálogo…';
        if (this.detailLoading) return 'Carregando a aquisição selecionada…';
        return '';
    }
    pointMarked(point: [number, number]): void {
        this.pointMode = true; this.pointLon = point[0]; this.pointLat = point[1]; this.filtersChanged();
    }
    get supportsPoint(): boolean { return ['landsat-2', 'S2_L2A-1'].includes(this.collectionId); }
    get pointValidation(): string {
        return [this.pointLon, this.pointLat, this.radius].every(v => typeof v === 'number' && Number.isFinite(v)) &&
            Math.abs(this.pointLon) <= 180 && Math.abs(this.pointLat) <= 80 && this.radius >= 100 && this.radius <= 5000 ? '' : 'Informe coordenadas válidas e raio entre 100 e 5.000 metros.';
    }
    useMapCenter(): void {
        const box = (this.map ?? this.pairedMaps?.map)?.currentBbox();
        if (box) { this.pointLon = (box[0] + box[2]) / 2; this.pointLat = (box[1] + box[3]) / 2; this.filtersChanged(); }
    }
    repeatComparison(parameters: {start: string; end: string; radius: number}): void {
        this.start = parameters.start; this.end = parameters.end; this.radius = parameters.radius;
        this.comparePoint(true);
    }
    comparePoint(preserveComparison = false): void {
        if (this.validation || this.pointValidation || !this.supportsPoint || !this.online) return;
        if (this.loading) return;
        if (preserveComparison) {
            this.generation++; this.searchRequest?.unsubscribe(); this.detailRequest?.unsubscribe();
            this.detailLoading = false; this.error = '';
        } else this.invalidate();
        this.loading = true; this.items = []; this.searched = true; this.nextPage = null;
        const generation = this.generation;
        this.searchRequest = this.api.bestImages(this.collectionId, this.pointLon, this.pointLat, this.radius, this.start, this.end, this.criterion).subscribe({
            next: result => {
                if (generation !== this.generation) return;
                this.comparison = result; this.loading = false;
                this.items = result.rows.map(row => row.item);
                const best = result.rows.find(row => row.id === result.best_local_id);
                this.map?.focusPoint(this.pointLon, this.pointLat);
                if (best) this.select(best.item, preserveComparison);
            },
            error: error => { if (generation === this.generation) { this.loading = false; this.error = this.message(error, 'Não foi possível concluir a comparação. Reduza o período e tente novamente.'); } },
        });
    }
    showCompared(id: string | null): void {
        const row = this.comparison?.rows.find(row => row.id === id);
        if (row) this.select(row.item);
    }
    localDescription(item: BdcItem): string {
        const row = this.comparison?.rows.find(row => row.id === item.id);
        if (!row) return '';
        if (!row.eligible) return row.reason || 'Qualidade local indisponível';
        return `No buffer: ${row.cloud_pct?.toFixed(1)}% de nuvens; ${row.shadow_pct?.toFixed(1)}% de sombras`;
    }
    private catalogRequest?: Subscription;
    private searchRequest?: Subscription;
    private detailRequest?: Subscription;
    private generation = 0;
    private lastFilters: SearchFilters | null = null;

    constructor(private api: BdcLabApiService) {}
    ngOnInit(): void { this.loadCatalog(); }
    ngOnDestroy(): void {
        this.catalogRequest?.unsubscribe(); this.searchRequest?.unsubscribe(); this.detailRequest?.unsubscribe();
    }
    get collection(): BdcCollection | undefined { return this.collections.find(c => c.id === this.collectionId); }
    get families(): string[] { return [...new Set(this.collections.map(c => c.family))].sort(); }
    get filteredCollections(): BdcCollection[] {
        const term = this.catalogFilter.toLocaleLowerCase('pt-BR');
        return this.collections.filter(c => (!this.family || c.family === this.family) && `${c.id} ${c.title}`.toLocaleLowerCase('pt-BR').includes(term));
    }
    get profile(): BdcProfile | undefined { return this.detail?.profiles.find(p => p.id === this.profileId); }
    get assets(): [string, { href: string; type?: string; title?: string }][] { return Object.entries(this.detail?.item.assets ?? {}); }
    get providers(): string { return this.collection?.providers.map(p => p.name).join(', ') || 'Não informados'; }
    get validation(): string {
        if (!this.collection) return 'Selecione uma coleção.';
        if (!this.start || !this.end || this.start > this.end) return 'Informe um intervalo de datas válido.';
        if ((Date.parse(this.end) - Date.parse(this.start)) / 86400000 > 366) return 'O intervalo máximo é de 366 dias.';
        if (!this.pointMode && (this.bbox[2] - this.bbox[0]) * (this.bbox[3] - this.bbox[1]) > 100) return 'Aproxime o mapa para pesquisar uma área menor.';
        return '';
    }
    loadCatalog(): void {
        this.catalogRequest?.unsubscribe(); this.catalogLoading = true; this.error = '';
        this.catalogRequest = this.api.collections().subscribe({
            next: result => {
                this.collections = result.collections; this.online = true; this.paused = result.paused; this.catalogLoading = false;
                if (!this.collection) this.collectionId = this.collections[0]?.id ?? '';
            },
            error: error => { this.catalogLoading = false; this.online = false; this.error = this.message(error, 'Não foi possível carregar o catálogo do BDC. Verifique a conexão e tente novamente.'); },
        });
    }
    chooseCollection(): void {
        this.invalidate(); this.items = []; this.searched = false; this.nextPage = null; this.lastFilters = null;
        this.maxCloud = 100; if (!this.supportsPoint) this.pointMode = false;
    }
    filtersChanged(): void { this.invalidate(); this.items = []; this.searched = false; this.nextPage = null; this.lastFilters = null; }
    invalidate(): void {
        this.comparison = null; this.generation++; this.searchRequest?.unsubscribe(); this.detailRequest?.unsubscribe();
        this.loading = false; this.detailLoading = false; this.detail = null; this.selection = null; this.error = ''; this.stats = emptyStats();
    }
    search(page = 1): void {
        if (this.validation || !this.online) return;
        const filters: SearchFilters = page !== 1 && this.lastFilters ? { ...this.lastFilters, page } : {
            collection: this.collectionId, bbox: [...this.bbox] as Bbox, start: this.start, end: this.end, max_cloud: this.maxCloud, page, limit: 12,
        };
        this.invalidate(); this.loading = true; this.items = []; this.searched = true; this.nextPage = null; this.lastFilters = filters;
        const generation = this.generation;
        this.searchRequest = this.api.search(filters).subscribe({
            next: result => {
                if (generation !== this.generation) return;
                this.items = result.items; this.page = result.page; this.nextPage = result.next_page; this.matched = result.matched; this.loading = false;
            },
            error: error => { if (generation === this.generation) { this.loading = false; this.error = this.message(error, 'Não foi possível pesquisar imagens. Ajuste a área ou tente novamente mais tarde.'); } },
        });
    }
    select(item: BdcItem, preserveProfile = false): void {
        this.detailRequest?.unsubscribe(); this.detailLoading = true; this.selection = null; this.detail = null; this.error = '';
        const generation = ++this.generation;
        this.detailRequest = this.api.detail(this.collectionId, item.id).subscribe({
            next: detail => {
                if (generation !== this.generation) return;
                this.detail = detail; this.detailLoading = false; this.profileId = preserveProfile && detail.profiles.some(profile => profile.id === this.profileId) ? this.profileId : detail.profiles[0]?.id ?? '';
                this.updateRender();
            },
            error: error => { if (generation === this.generation) { this.detailLoading = false; this.error = this.message(error, 'Não foi possível abrir esta aquisição.'); } },
        });
    }
    updateRender(): void {
        this.stats = emptyStats();
        this.selection = this.detail && this.profile ? { collection: this.collectionId, item: this.detail.item, profile: this.profile,
            format: this.format, quality: this.quality, background: this.background } : null;
    }
    useMapArea(): void { const bbox = this.map?.currentBbox(); if (bbox) { this.bbox = bbox; this.filtersChanged(); } }
    preset(region: 'goias' | 'amazonia'): void {
        this.bbox = region === 'goias' ? [-49.55, -16.85, -49.25, -16.55] : [-55.05, -3.25, -54.75, -2.95];
        this.map?.fitBbox(this.bbox); this.filtersChanged();
    }
    cloud(item: BdcItem): string { const value = item.properties['eo:cloud_cover']; return typeof value === 'number' ? `${value.toFixed(1)}%` : 'Não informada'; }
    thumbnail(item: BdcItem): string | null {
        return safeBdcUrl(Object.values(item.assets).find(a => a.roles?.includes('thumbnail'))?.href);
    }
    safeUrl = safeBdcUrl;
    trackItem(_: number, item: BdcItem): string { return item.id; }
    private message(error: unknown, fallback: string): string {
        if (error instanceof HttpErrorResponse && error.status === 503) {
            this.paused = true;
            return 'As leituras estão pausadas ou o serviço está ocupado. Não haverá novas tentativas automáticas.';
        }
        return error instanceof HttpErrorResponse && typeof error.error?.detail === 'string' ? error.error.detail : fallback;
    }
}
