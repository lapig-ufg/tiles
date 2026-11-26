import { AfterViewInit, Component, ElementRef, EventEmitter, Input, NgZone, OnChanges, OnDestroy, Output, ViewChild } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { EMPTY, Subject, Subscription, catchError, concatMap, takeUntil, tap } from 'rxjs';
import OlMap from 'ol/Map';
import View from 'ol/View';
import ImageTile from 'ol/ImageTile';
import TileState from 'ol/TileState';
import TileLayer from 'ol/layer/Tile';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import XYZ from 'ol/source/XYZ';
import OSM from 'ol/source/OSM';
import Attribution from 'ol/control/Attribution';
import GeoJSON from 'ol/format/GeoJSON';
import { fromLonLat, transformExtent } from 'ol/proj';
import { Fill, Stroke, Style } from 'ol/style';
import { BdcLabApiService } from './bdc-lab-api.service';
import { Bbox, BdcItem, RenderSelection, TileStats, emptyStats } from './bdc-lab.models';

@Component({
    selector: 'app-bdc-lab-map',
    template: `
        <div class="map-shell">
            <div #mapHost class="map-host" role="region" aria-label="Mapa de imagens BDC"></div>
            <div class="map-badge"><i class="pi pi-map-marker" aria-hidden="true"></i> {{ selection ? 'Imagem BDC' : 'Área de interesse' }} <span>z{{ zoom }}</span></div>
            <div class="map-empty" *ngIf="!selection"><i class="pi pi-images" aria-hidden="true"></i><strong>Explore o acervo brasileiro</strong><span>Pesquise uma área e selecione uma aquisição para visualizar suas bandas.</span></div>
            <div class="map-tools">
                <button pButton class="p-button-sm p-button-outlined" icon="pi pi-search-plus" aria-label="Aproximar mapa" (click)="changeZoom(1)"></button>
                <button pButton class="p-button-sm p-button-outlined" icon="pi pi-search-minus" aria-label="Afastar mapa" (click)="changeZoom(-1)"></button>
                <button pButton class="p-button-sm p-button-outlined" icon="pi pi-expand" aria-label="Ajustar à imagem" [disabled]="!selection" (click)="fitSelected()"></button>
            </div>
        </div>
        <div class="map-actions">
            <span><i class="pi pi-info-circle" aria-hidden="true"></i> {{ selection?.format === 'jpeg' ? 'JPEG: áreas sem dados usam o fundo escolhido.' : 'PNG: áreas sem dados permanecem transparentes.' }}</span>
            <div>
                <button pButton class="p-button-sm p-button-outlined" icon="pi pi-bolt" [label]="warming ? 'Preparando…' : 'Preparar 3 × 3'" [disabled]="!selection || warming || paused" (click)="warm()"></button>
                <button pButton class="p-button-sm p-button-outlined" icon="pi pi-download" label="Baixar tile central" [disabled]="!selection || downloading || paused" (click)="download()"></button>
            </div>
        </div>
        <p class="map-message" role="status" *ngIf="notice">{{ notice }}</p>
    `,
    styles: [`
        :host{display:block;min-width:0}.map-shell{position:relative;border-radius:12px;overflow:hidden;border:1px solid var(--surface-border);background:var(--surface-ground)}
        .map-host{height:490px;width:100%}.map-badge{position:absolute;top:16px;left:16px;display:flex;gap:10px;align-items:center;background:var(--surface-card);color:var(--text-color);padding:10px 14px;border-radius:7px;box-shadow:0 2px 8px #0002;font-weight:600}.map-badge span{color:var(--text-color-secondary);font-weight:400}
        .map-empty{position:absolute;top:35%;left:50%;transform:translateX(-50%);display:grid;gap:12px;text-align:center;background:var(--surface-card);padding:24px;max-width:330px;width:85%;border-radius:12px;pointer-events:none;box-shadow:0 3px 20px #0001}.map-empty i{font-size:28px;color:var(--primary-color)}.map-empty span{color:var(--text-color-secondary);line-height:1.5}
        .map-tools{position:absolute;right:16px;top:16px;display:grid;gap:6px;background:var(--surface-card);border-radius:8px;padding:4px}.map-actions{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:14px 0}.map-actions>span{color:var(--text-color-secondary);font-size:12px;max-width:300px;line-height:1.5}.map-actions>div{display:flex;gap:8px;flex-wrap:wrap}.map-message{padding:10px 12px;background:var(--surface-ground);border-radius:6px;line-height:1.5}button:focus-visible{outline:3px solid var(--primary-color);outline-offset:2px}
        @media(max-width:700px){.map-host{height:360px}.map-actions{align-items:flex-start;flex-direction:column}}
    `],
})
export class BdcLabMapComponent implements AfterViewInit, OnChanges, OnDestroy {
    @Input() selection: RenderSelection | null = null;
    @Output() statsChange = new EventEmitter<TileStats>();
    @ViewChild('mapHost', { static: true }) mapHost!: ElementRef<HTMLDivElement>;
    zoom = 10;
    paused = false;
    warming = false;
    downloading = false;
    notice = '';
    private map?: OlMap;
    private imagery = new TileLayer<XYZ>();
    private footprint = new VectorSource();
    private jobs = new Subject<{ tile: ImageTile; url: string; generation: number }>();
    private reset = new Subject<void>();
    private subscriptions = new Subscription();
    private queueSubscription?: Subscription;
    private generation = 0;
    private stats = emptyStats();
    private urls = new Set<string>();
    private resize?: ResizeObserver;

    constructor(private api: BdcLabApiService, private zone: NgZone) {}
    ngAfterViewInit(): void {
        this.zone.runOutsideAngular(() => {
            this.map = new OlMap({ target: this.mapHost.nativeElement, maxTilesLoading: 1,
                layers: [new TileLayer({ source: new OSM() }), this.imagery, new VectorLayer({ source: this.footprint,
                    style: new Style({ stroke: new Stroke({ color: '#2563eb', width: 2 }), fill: new Fill({ color: 'rgba(37,99,235,0.025)' }) }) })],
                view: new View({ center: fromLonLat([-49.4, -16.7]), zoom: 10, minZoom: 6, maxZoom: 18 }), controls: [new Attribution({ collapsible: false })], });
            this.map.on('moveend', () => this.zone.run(() => { this.zoom = Math.round(this.map?.getView().getZoom() ?? 10); }));
            this.resize = new ResizeObserver(() => this.map?.updateSize());
            this.resize.observe(this.mapHost.nativeElement);
        });
        this.applySelection();
    }
    ngOnChanges(): void { if (this.map) this.applySelection(); }
    ngOnDestroy(): void {
        this.reset.next(); this.reset.complete(); this.queueSubscription?.unsubscribe(); this.jobs.complete(); this.subscriptions.unsubscribe();
        this.resize?.disconnect(); this.imagery.getSource()?.dispose(); this.map?.setTarget(undefined); this.map?.dispose(); this.releaseUrls();
    }
    private applySelection(): void {
        this.generation++; this.reset.next(); this.queueSubscription?.unsubscribe(); this.subscriptions.unsubscribe(); this.subscriptions = new Subscription();
        this.releaseUrls(); this.stats = emptyStats(); this.statsChange.emit(this.stats); this.paused = false; this.warming = false; this.downloading = false; this.notice = '';
        this.imagery.getSource()?.dispose(); this.imagery.setSource(null); this.footprint.clear();
        const selection = this.selection;
        if (!selection) return;
        this.footprint.addFeatures(new GeoJSON().readFeatures({ type: 'Feature', properties: {}, geometry: selection.item.geometry }, { featureProjection: 'EPSG:3857' }));
        this.queueSubscription = this.jobs.pipe(concatMap(job => {
            if (job.generation !== this.generation || this.paused) { job.tile.setState(TileState.ERROR); return EMPTY; }
            return this.api.tile(job.url).pipe(takeUntil(this.reset), tap(response => {
                if (job.generation !== this.generation || !response.body) return;
                const url = URL.createObjectURL(response.body); this.urls.add(url);
                const image = job.tile.getImage() as HTMLImageElement;
                const release = () => { URL.revokeObjectURL(url); this.urls.delete(url); };
                image.addEventListener('load', release, { once: true }); image.addEventListener('error', release, { once: true }); image.src = url;
                this.zone.run(() => {
                    this.stats = { ...this.stats, completed: this.stats.completed + 1,
                        hits: this.stats.hits + (response.headers.get('X-Cache') === 'HIT' ? 1 : 0),
                        transferred: this.stats.transferred + response.body!.size,
                        ranges: this.stats.ranges + Number(response.headers.get('X-Range-Requests') || 0),
                        serverMs: this.stats.serverMs + Number(response.headers.get('X-Render-Ms') || 0) };
                    this.statsChange.emit(this.stats);
                });
            }), catchError(error => {
                job.tile.setState(TileState.ERROR);
                this.zone.run(() => { this.stats = { ...this.stats, errors: this.stats.errors + 1 }; this.statsChange.emit(this.stats); this.fail(error); });
                return EMPTY;
            }));
        })).subscribe();
        const generation = this.generation;
        this.imagery.setSource(new XYZ({ minZoom: 6, maxZoom: 18, tileSize: 256, wrapX: false, transition: 0,
            attributions: 'Imagens: INPE / Brazil Data Cube',
            tileUrlFunction: coordinate => coordinate ? this.api.tileUrl(selection, coordinate[1], coordinate[2], coordinate[0]) : undefined,
            tileLoadFunction: (tile, url) => this.jobs.next({ tile: tile as ImageTile, url, generation }),
        }));
    }
    currentBbox(): Bbox | null {
        return this.map ? transformExtent(this.map.getView().calculateExtent(this.map.getSize()), 'EPSG:3857', 'EPSG:4326') as Bbox : null;
    }
    fitBbox(bbox: Bbox): void { this.map?.getView().fit(transformExtent(bbox, 'EPSG:4326', 'EPSG:3857'), { padding: [35,35,35,35], maxZoom: 12 }); }
    fitItem(item: BdcItem): void { this.fitBbox(item.bbox); }
    fitSelected(): void { if (this.selection) this.fitItem(this.selection.item); }
    changeZoom(delta: number): void { const view = this.map?.getView(); if (view) view.setZoom((view.getZoom() ?? 10) + delta); }
    centerTile(): [number, number, number] | null {
        const view = this.map?.getView(); const grid = this.imagery.getSource()?.getTileGrid(); const center = view?.getCenter();
        if (!view || !grid || !center) return null;
        const [z,x,y] = grid.getTileCoordForCoordAndZ(center, Math.round(view.getZoom() ?? 10)); return [x,y,z];
    }
    warm(): void {
        const center = this.centerTile(); if (!center || !this.selection || this.warming || this.paused) return;
        const [x,y,z] = center; const tiles: [number,number,number][] = [];
        for (let dx=-1; dx<=1; dx++) for (let dy=-1; dy<=1; dy++) if (x+dx>=0 && y+dy>=0 && x+dx<2**z && y+dy<2**z) tiles.push([x+dx,y+dy,z]);
        this.warming = true; this.notice = 'Preparando os tiles ao redor do centro, em sequência.';
        this.subscriptions.add(this.api.warm(this.selection, tiles).subscribe({
            next: result => { this.warming = false; this.notice = `${result.completed} tiles preparados para esta composição e este formato.`; },
            error: error => { this.warming = false; this.fail(error); },
        }));
    }
    download(): void {
        const tile = this.centerTile(); if (!tile || !this.selection || this.downloading || this.paused) return;
        const selection = this.selection; this.downloading = true;
        this.subscriptions.add(this.api.tile(this.api.tileUrl(selection, ...tile)).subscribe({
            next: response => {
                this.downloading = false; if (!response.body) return;
                const url = URL.createObjectURL(response.body); this.urls.add(url);
                const link = document.createElement('a'); link.href = url; link.download = `bdc-${tile[2]}-${tile[0]}-${tile[1]}.${selection.format === 'jpeg' ? 'jpg' : 'png'}`;
                link.click(); this.notice = 'Tile central disponibilizado para download.';
            }, error: error => { this.downloading = false; this.fail(error); },
        }));
    }
    private fail(error: unknown): void {
        if (error instanceof HttpErrorResponse && (error.status === 429 || error.status === 503)) {
            this.paused = true; this.notice = 'Leituras suspensas por indisponibilidade. Nenhuma tentativa automática será feita.';
        } else this.notice = 'Não foi possível carregar uma imagem. Confira a disponibilidade da origem e a composição selecionada.';
    }
    private releaseUrls(): void { this.urls.forEach(url => URL.revokeObjectURL(url)); this.urls.clear(); }
}
