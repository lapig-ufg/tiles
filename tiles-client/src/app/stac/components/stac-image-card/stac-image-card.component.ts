import { Component, Input, OnDestroy, AfterViewInit, ElementRef, ViewChild, ChangeDetectorRef, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TagModule } from 'primeng/tag';
import { TooltipModule } from 'primeng/tooltip';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import Map from 'ol/Map';
import View from 'ol/View';
import WebGLTileLayer from 'ol/layer/WebGLTile';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import GeoTIFF from 'ol/source/GeoTIFF';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { GeoJSON } from 'ol/format';
import { transformExtent } from 'ol/proj';
import { StacItem, sanitizeAssetUrl } from '../../models/stac.models';
import { SpectralIndex, getAssetKeysForIndex } from '../../models/spectral-indices';
import { CogRendererService } from '../../services/cog-renderer.service';
import { createGeometryStyleNoFill, createGeometryStyle } from '../../../shared/utils/geometry.utils';
import { Style, Stroke, Fill } from 'ol/style';

@Component({
  selector: 'app-stac-image-card',
  standalone: true,
  imports: [CommonModule, TagModule, TooltipModule, ProgressSpinnerModule],
  templateUrl: './stac-image-card.component.html',
  styleUrls: ['./stac-image-card.component.scss'],
})
export class StacImageCardComponent implements AfterViewInit, OnDestroy {
  @Input() item!: StacItem;
  @Input() spectralIndex!: SpectralIndex;
  @Input() renderMode: 'cog' | 'footprint' = 'cog';
  @Input() aoiGeometry: Feature<Geometry> | null = null;
  @Input() basemap: string = 'dark';
  @Input() initDelay: number = 0;
  @Input() viewBbox: [number, number, number, number] | null = null;

  @ViewChild('cardMap', { static: true }) cardMapRef!: ElementRef;

  loading = true;
  error = false;
  errorDetail: string = '';

  private map: Map | null = null;
  private cogLayer: WebGLTileLayer | null = null;
  private loadingTimeout: any;
  private initTimeout: any;

  constructor(
    private cogRenderer: CogRendererService,
    private cdr: ChangeDetectorRef,
    private ngZone: NgZone,
  ) {}

  ngAfterViewInit(): void {
    this.initTimeout = setTimeout(() => {
      this.ngZone.runOutsideAngular(() => this.initMap());
    }, this.initDelay);
  }

  ngOnDestroy(): void {
    if (this.initTimeout) clearTimeout(this.initTimeout);
    if (this.loadingTimeout) clearTimeout(this.loadingTimeout);
    if (this.cogLayer) {
      this.cogRenderer.destroyLayer(this.cogLayer);
      this.cogLayer = null;
    }
    if (this.map) {
      this.map.setTarget(undefined);
      this.map = null;
    }
  }

  get cloudCover(): number | null {
    return this.item?.properties?.['eo:cloud_cover'] ?? null;
  }

  get cloudSeverity(): string {
    const cloud = this.cloudCover;
    if (cloud === null) return 'info';
    if (cloud < 20) return 'success';
    if (cloud < 50) return 'warning';
    return 'danger';
  }

  get itemDate(): string {
    const dt = this.item?.properties?.datetime;
    if (!dt) return 'N/A';
    return dt.split('T')[0];
  }

  get platform(): string {
    return this.item?.properties?.platform || '';
  }

  private initMap(): void {
    if (this.renderMode === 'cog') {
      this.initCogMap();
    } else {
      this.initFootprintMap();
    }
  }

  /**
   * COG rendering.
   *
   * Uses EPSG:3857 view (not source.getView()) so that:
   * - AOI geometry (in EPSG:3857) aligns correctly
   * - viewBbox (in EPSG:4326, transformed to 3857) works directly
   * - OL handles COG reprojection from UTM to 3857 automatically
   *
   * AOI vector layer uses zIndex:10 to always render above the COG (zIndex:0).
   */
  private initCogMap(): void {
    // Determine view extent
    const bbox4326 = this.viewBbox || this.item.bbox as [number, number, number, number];
    if (!bbox4326 || bbox4326.length < 4) {
      this.setError('Bbox ausente');
      return;
    }
    const extent3857 = transformExtent(bbox4326, 'EPSG:4326', 'EPSG:3857');

    // Build COG layer
    let cogLayer: WebGLTileLayer;
    try {
      if (this.spectralIndex.type === 'rgb') {
        const rawUrl = this.item.assets['visual']?.href
                    || this.item.assets['RGB']?.href
                    || this.item.assets['rendered_preview']?.href
                    || this.item.assets['thumbnail']?.href;
        if (!rawUrl) {
          this.setError('Asset visual não encontrado');
          return;
        }
        const visualUrl = sanitizeAssetUrl(rawUrl);
        const result = this.cogRenderer.createVisualLayer(visualUrl, this.spectralIndex.style);
        cogLayer = result.layer;
      } else {
        const assetKeys = getAssetKeysForIndex(this.spectralIndex);
        const bandUrls: string[] = [];
        const missing: string[] = [];
        for (const key of assetKeys) {
          const asset = this.item.assets[key];
          if (asset?.href) {
            bandUrls.push(sanitizeAssetUrl(asset.href));
          } else {
            missing.push(key);
          }
        }
        if (bandUrls.length === 0 || missing.length > 0) {
          console.warn(`[COG] Missing bands:`, missing, 'Available:', Object.keys(this.item.assets));
          this.setError(`Bandas: ${missing.join(', ')}`);
          return;
        }
        const result = this.cogRenderer.createMultiBandLayer(bandUrls, this.spectralIndex.style);
        cogLayer = result.layer;
      }
    } catch (err) {
      console.error('[COG] Error creating layer:', err);
      this.setError('Erro ao criar layer');
      return;
    }

    this.cogLayer = cogLayer;
    cogLayer.setZIndex(0);

    // Build layers array
    const layers: any[] = [cogLayer];

    // AOI geometry overlay — ALWAYS above COG via zIndex
    if (this.aoiGeometry) {
      const aoiVectorLayer = new VectorLayer({
        source: new VectorSource({ features: [this.aoiGeometry.clone()] }),
        style: new Style({
          stroke: new Stroke({ color: '#ff6b35', width: 3 }),
          fill: new Fill({ color: 'rgba(255, 107, 53, 0.12)' }),
        }),
        zIndex: 10,
      });
      layers.push(aoiVectorLayer);
    }

    // Create map in EPSG:3857 — OL reprojects COG from UTM automatically
    this.map = new Map({
      target: this.cardMapRef.nativeElement,
      layers,
      view: new View({
        center: [(extent3857[0] + extent3857[2]) / 2, (extent3857[1] + extent3857[3]) / 2],
        zoom: 12,
        projection: 'EPSG:3857',
      }),
      controls: [],
      interactions: [],
    });

    this.map.getView().fit(extent3857, { padding: [15, 15, 15, 15], maxZoom: 16 });

    // Loading state management
    this.map.once('rendercomplete', () => {
      this.ngZone.run(() => {
        this.loading = false;
        this.cdr.detectChanges();
      });
      if (this.loadingTimeout) clearTimeout(this.loadingTimeout);
    });

    this.loadingTimeout = setTimeout(() => {
      this.ngZone.run(() => {
        if (this.loading) {
          this.loading = false;
          this.cdr.detectChanges();
        }
      });
    }, 25000);
  }

  /**
   * Footprint rendering — shows item geometry on a basemap.
   */
  private initFootprintMap(): void {
    const bbox4326 = this.viewBbox || this.item.bbox as [number, number, number, number];
    if (!bbox4326 || bbox4326.length < 4) {
      this.setError('Bbox ausente');
      return;
    }
    const extent3857 = transformExtent(bbox4326, 'EPSG:4326', 'EPSG:3857');

    const format = new GeoJSON();
    const feature = format.readFeature(
      { type: 'Feature', geometry: this.item.geometry, properties: {} },
      { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' }
    ) as Feature<Geometry>;

    const layers: any[] = [
      new TileLayer({ source: this.getBasemapSource() }),
      new VectorLayer({
        source: new VectorSource({ features: [feature] }),
        style: createGeometryStyle(),
      }),
    ];

    if (this.aoiGeometry) {
      layers.push(new VectorLayer({
        source: new VectorSource({ features: [this.aoiGeometry.clone()] }),
        style: createGeometryStyleNoFill(),
        zIndex: 10,
      }));
    }

    this.map = new Map({
      target: this.cardMapRef.nativeElement,
      layers,
      view: new View({
        center: [(extent3857[0] + extent3857[2]) / 2, (extent3857[1] + extent3857[3]) / 2],
        zoom: 10,
      }),
      controls: [],
      interactions: [],
    });

    this.map.getView().fit(extent3857, { padding: [15, 15, 15, 15], maxZoom: 16 });

    this.ngZone.run(() => {
      this.loading = false;
      this.cdr.detectChanges();
    });
  }

  private setError(detail: string): void {
    console.error(`[COG] ${this.item?.id}: ${detail}`);
    this.ngZone.run(() => {
      this.loading = false;
      this.error = true;
      this.errorDetail = detail;
      this.cdr.detectChanges();
    });
  }

  private getBasemapSource(): any {
    switch (this.basemap) {
      case 'satellite':
        return new XYZ({ url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}' });
      case 'streets':
        return new XYZ({ url: 'https://{a-c}.tile.openstreetmap.org/{z}/{x}/{y}.png' });
      default:
        return new XYZ({ url: 'https://{a-d}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png' });
    }
  }
}
