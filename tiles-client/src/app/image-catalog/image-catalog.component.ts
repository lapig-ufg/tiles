import {Component, OnInit, OnDestroy} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import {Subscription} from 'rxjs';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import {defaults as defaultInteractions} from 'ol/interaction';
import Pointer from 'ol/interaction/Pointer';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import {Icon, Style} from 'ol/style';
import {Point, Geometry} from 'ol/geom';
import {Feature} from 'ol';
import {Coordinate} from 'ol/coordinate';
import {marker} from '../../assets/layout/images/marker';
import {PointService} from '../grid-map/services/point.service';
import {ImageryService, CatalogItem, CatalogResponse} from './services/imagery.service';
import {ScreenStateConfig, ScreenStateBinder} from '../screen-state/interfaces/screen-state.interfaces';
import {ScreenStateService} from '../screen-state/services/screen-state.service';
import {bindState} from '../screen-state/helpers/manual-state.helper';
import {createGeometryStyleNoFill} from '../shared/utils/geometry.utils';

const IMAGE_CATALOG_STATE_CONFIG: ScreenStateConfig = {
    screenKey: 'image-catalog',
    group: 'catalog',
    strategy: 'storage-only',
    debounceMs: 400,
    ttlMs: 7 * 24 * 60 * 60 * 1000,
    schemaVersion: 1,
    fields: {
        selectedLayer:    {type: 'string', defaultValue: 's2_harmonized'},
        startDate:        {type: 'date'},
        endDate:          {type: 'date'},
        maxCloud:         {type: 'number', defaultValue: 100},
        selectedVisparam: {type: 'string', defaultValue: 'tvi-red'},
        selectedSort:     {type: 'string', defaultValue: 'date_asc'},
        bufferMeters:     {type: 'number', defaultValue: 1000},
        offset:           {type: 'number', defaultValue: 0},
        limit:            {type: 'number', defaultValue: 25},
    }
};

@Component({
    selector: 'app-image-catalog',
    templateUrl: './image-catalog.component.html',
    styleUrls: ['./image-catalog.component.scss']
})
export class ImageCatalogComponent implements OnInit, OnDestroy {
    // Form inputs
    selectedLayer: string = 's2_harmonized';
    startDate: Date | null = null;
    endDate: Date | null = null;
    maxCloud: number = 100;
    selectedVisparam: string = 'tvi-red';
    selectedSort: string = 'date_asc';
    bufferMeters: number = 1000;

    // Catalog data
    catalogItems: CatalogItem[] = [];
    selectedImages: CatalogItem[] = [];
    totalImages: number = 0;
    offset: number = 0;
    limit: number = 25;
    loading: boolean = false;
    searched: boolean = false;
    errorMessage: string | null = null;

    // Timeseries
    showTimeseries: boolean = true;

    // Active feature (geometry from GeoJSON upload)
    activeFeature: Feature<Geometry> | null = null;
    showSampleInfo: boolean = true;
    private geometryLayersByMapId: Record<string, VectorLayer<Feature<Geometry>>> = {};

    // Maps
    private mapsInstances: {id: string, map: Map}[] = [];
    private centerCoordinates: Coordinate = fromLonLat([-49.2646, -16.6799]);

    // Point coordinates
    lat: number | null = null;
    lon: number | null = null;

    // Options
    layers = [
        {label: 'Sentinel-2', value: 's2_harmonized'},
        {label: 'Landsat', value: 'landsat'}
    ];

    visparamOptions: Record<string, {label: string, value: string}[]> = {
        s2_harmonized: [
            {label: 'TVI Green', value: 'tvi-green'},
            {label: 'TVI Red', value: 'tvi-red'},
            {label: 'TVI RGB', value: 'tvi-rgb'}
        ],
        landsat: [
            {label: 'False Color', value: 'landsat-tvi-false'},
            {label: 'True Color', value: 'landsat-tvi-true'},
            {label: 'Agriculture', value: 'landsat-tvi-agri'}
        ]
    };

    sortOptions = [
        {label: 'Data (antigo)', value: 'date_asc'},
        {label: 'Nuvem (menor)', value: 'cloud_asc'}
    ];

    private subscriptions: Subscription[] = [];
    private stateBinder!: ScreenStateBinder<any>;

    constructor(
        private pointService: PointService,
        private imageryService: ImageryService,
        private route: ActivatedRoute,
        private router: Router,
        private screenStateService: ScreenStateService,
    ) {}

    ngOnInit(): void {
        const now = new Date();
        const defaultEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const defaultStart = new Date(now.getFullYear(), now.getMonth() - 6, now.getDate());

        this.stateBinder = bindState(
            IMAGE_CATALOG_STATE_CONFIG,
            this.route,
            this.router,
            this.screenStateService,
            {
                selectedLayer: 's2_harmonized',
                startDate: defaultStart,
                endDate: defaultEnd,
                maxCloud: 100,
                selectedVisparam: 'tvi-red',
                selectedSort: 'date_asc',
                bufferMeters: 1000,
                offset: 0,
                limit: 25,
            }
        );

        const restored = this.stateBinder.restore();
        this.selectedLayer = restored.selectedLayer;
        this.startDate = restored.startDate;
        this.endDate = restored.endDate;
        this.maxCloud = restored.maxCloud;
        this.selectedVisparam = restored.selectedVisparam;
        this.selectedSort = restored.selectedSort;
        this.bufferMeters = restored.bufferMeters;
        this.offset = restored.offset;
        this.limit = restored.limit;

        const sub = this.pointService.point$.subscribe({
            next: point => {
                if (point.lat && point.lon) {
                    this.lat = point.lat;
                    this.lon = point.lon;
                    this.centerCoordinates = fromLonLat([point.lon, point.lat]);
                    this.updateMaps();
                }
            }
        });
        this.subscriptions.push(sub);

        const featureSub = this.pointService.activeFeature$.subscribe({
            next: feature => {
                this.activeFeature = feature;
                this.updateGeometryOverlays();
            }
        });
        this.subscriptions.push(featureSub);
    }

    ngOnDestroy(): void {
        this.stateBinder.destroy();
        this.subscriptions.forEach(s => s.unsubscribe());
        this.destroyAllMaps();
    }

    get currentVisparamOptions(): {label: string, value: string}[] {
        return this.visparamOptions[this.selectedLayer] || [];
    }

    onLayerChange(): void {
        const defaults: Record<string, string> = {
            s2_harmonized: 'tvi-red',
            landsat: 'landsat-tvi-false'
        };
        this.selectedVisparam = defaults[this.selectedLayer] || this.currentVisparamOptions[0]?.value;
        this.clearCatalog();
        this.persistFilters();
    }

    onFilterChange(): void {
        this.persistFilters();
    }

    clearFilters(): void {
        this.stateBinder.reset();
        const s = this.stateBinder.state;
        this.selectedLayer = s.selectedLayer;
        this.startDate = s.startDate;
        this.endDate = s.endDate;
        this.maxCloud = s.maxCloud;
        this.selectedVisparam = s.selectedVisparam;
        this.selectedSort = s.selectedSort;
        this.bufferMeters = s.bufferMeters;
        this.offset = s.offset;
        this.limit = s.limit;
        this.clearCatalog();
    }

    private persistFilters(): void {
        this.stateBinder.patchAndPersist({
            selectedLayer: this.selectedLayer,
            startDate: this.startDate,
            endDate: this.endDate,
            maxCloud: this.maxCloud,
            selectedVisparam: this.selectedVisparam,
            selectedSort: this.selectedSort,
            bufferMeters: this.bufferMeters,
            offset: this.offset,
            limit: this.limit,
        });
    }

    searchCatalog(): void {
        if (!this.lat || !this.lon || !this.startDate || !this.endDate) {
            return;
        }

        this.loading = true;
        this.searched = true;

        const start = this.formatDate(this.startDate);
        const end = this.formatDate(this.endDate);

        this.imageryService.getCatalog(this.selectedLayer, {
            lat: this.lat,
            lon: this.lon,
            start,
            end,
            visparam: this.selectedVisparam,
            bufferMeters: this.bufferMeters,
            limit: this.limit,
            offset: this.offset,
            sort: this.selectedSort as 'date_asc' | 'cloud_asc',
            maxCloud: this.maxCloud
        }).subscribe({
            next: (response: CatalogResponse) => {
                this.catalogItems = response.items.map(item => ({
                    ...item,
                    selected: this.selectedImages.some(s => s.id === item.id)
                }));
                this.totalImages = response.total;
                this.loading = false;
                this.errorMessage = null;
            },
            error: () => {
                this.loading = false;
                this.catalogItems = [];
                this.totalImages = 0;
                this.errorMessage = 'Erro ao buscar imagens. Verifique sua conexão e tente novamente.';
            }
        });
    }

    onPageChange(event: any): void {
        this.offset = event.first;
        this.limit = event.rows;
        this.persistFilters();
        this.searchCatalog();
    }

    onImageSelect(item: CatalogItem): void {
        item.selected = !item.selected;

        if (item.selected) {
            this.selectedImages.push(item);
            this.createImageMap(item);
        } else {
            this.selectedImages = this.selectedImages.filter(s => s.id !== item.id);
            this.removeImageMap(item.id);
        }
    }

    get isAllPageSelected(): boolean {
        return this.catalogItems.length > 0 && this.catalogItems.every(item => item.selected);
    }

    toggleSelectAll(): void {
        const selectAll = !this.isAllPageSelected;
        for (const item of this.catalogItems) {
            if (selectAll && !item.selected) {
                item.selected = true;
                this.selectedImages.push(item);
                this.createImageMap(item);
            } else if (!selectAll && item.selected) {
                item.selected = false;
                this.selectedImages = this.selectedImages.filter(s => s.id !== item.id);
                this.removeImageMap(item.id);
            }
        }
    }

    removeSelectedImage(item: CatalogItem): void {
        this.selectedImages = this.selectedImages.filter(s => s.id !== item.id);
        this.removeImageMap(item.id);
        const catalogItem = this.catalogItems.find(c => c.id === item.id);
        if (catalogItem) {
            catalogItem.selected = false;
        }
    }

    onCalendarClose(): void {
        if (this.lat && this.lon && this.startDate && this.endDate) {
            this.offset = 0;
            this.searchCatalog();
        }
    }

    clearSelection(): void {
        this.selectedImages = [];
        this.catalogItems.forEach(item => item.selected = false);
        this.destroyAllMaps();
    }

    getCloudSeverity(cloud: number | null): string {
        if (cloud === null) return 'info';
        if (cloud < 20) return 'success';
        if (cloud < 50) return 'warning';
        return 'danger';
    }

    formatDateDisplay(datetime: string): string {
        return datetime.split('T')[0];
    }

    private addGeometryLayer(mapId: string, map: Map): void {
        if (!this.activeFeature) return;
        const geomClone = this.activeFeature.clone();
        const layer = new VectorLayer({
            source: new VectorSource({features: [geomClone]}),
            style: createGeometryStyleNoFill(),
        });
        map.addLayer(layer);
        this.geometryLayersByMapId[mapId] = layer;
    }

    private updateGeometryOverlays(): void {
        // Remove existing geometry layers
        for (const mapId of Object.keys(this.geometryLayersByMapId)) {
            const instance = this.mapsInstances.find(m => m.id === mapId);
            if (instance) {
                instance.map.removeLayer(this.geometryLayersByMapId[mapId]);
            }
        }
        this.geometryLayersByMapId = {};

        // Add new geometry layers if feature exists
        if (this.activeFeature) {
            for (const instance of this.mapsInstances) {
                this.addGeometryLayer(instance.id, instance.map);
            }
        }
    }

    private createImageMap(item: CatalogItem): void {
        const mapId = 'img-map-' + item.id.replace(/[^a-zA-Z0-9]/g, '-');

        setTimeout(() => {
            const target = document.getElementById(mapId);
            if (!target) return;

            const encodedId = encodeURIComponent(item.id);
            const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/imagery/${this.selectedLayer}/{x}/{y}/{z}?imageId=${encodedId}&visparam=${this.selectedVisparam}`;

            const cloudLabel = item.cloud !== null ? `${item.cloud.toFixed(1)}%` : 'N/A';
            const map = new Map({
                target: mapId,
                layers: [
                    new TileLayer({
                        source: new XYZ({
                            url,
                            attributions: `${this.formatDateDisplay(item.datetime)} | Cloud: ${cloudLabel}`,
                            attributionsCollapsible: false,
                        }),
                    })
                ],
                view: new View({
                    center: this.centerCoordinates,
                    zoom: 13
                }),
                interactions: defaultInteractions().extend([
                    new Pointer({
                        handleDownEvent: (event) => {
                            const coordinates = toLonLat(event.coordinate) as [number, number];
                            this.updateCenterCoordinates(coordinates);
                            return true;
                        }
                    })
                ])
            });

            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], map);
            this.addGeometryLayer(mapId, map);
            this.mapsInstances.push({id: mapId, map});
        }, 0);
    }

    private removeImageMap(imageId: string): void {
        const mapId = 'img-map-' + imageId.replace(/[^a-zA-Z0-9]/g, '-');
        const index = this.mapsInstances.findIndex(m => m.id === mapId);
        if (index >= 0) {
            this.mapsInstances[index].map.setTarget(undefined);
            this.mapsInstances.splice(index, 1);
            delete this.geometryLayersByMapId[mapId];
        }
    }

    private destroyAllMaps(): void {
        for (const instance of this.mapsInstances) {
            instance.map.setTarget(undefined);
        }
        this.mapsInstances = [];
        this.geometryLayersByMapId = {};
    }

    private addMarker(lat: number, lon: number, map: Map): void {
        const iconFeature = new Feature({
            geometry: new Point(fromLonLat([lon, lat])),
        });
        const iconStyle = new Style({
            image: new Icon({
                anchor: [0.5, 0.5],
                src: marker,
                scale: 1,
            }),
        });
        iconFeature.setStyle(iconStyle);
        const vectorSource = new VectorSource({features: [iconFeature]});
        const vectorLayer = new VectorLayer({source: vectorSource});
        map.addLayer(vectorLayer);
    }

    private updateCenterCoordinates(coordinates: [number, number]): void {
        this.centerCoordinates = fromLonLat(coordinates);
        this.updateMaps();
    }

    private updateMaps(): void {
        for (const mapInstance of this.mapsInstances) {
            const view = mapInstance.map.getView();
            view.setCenter(this.centerCoordinates);
            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], mapInstance.map);
        }
    }

    private clearCatalog(): void {
        this.catalogItems = [];
        this.selectedImages = [];
        this.totalImages = 0;
        this.offset = 0;
        this.searched = false;
        this.destroyAllMaps();
    }

    private formatDate(date: Date): string {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    }

    getMapId(item: CatalogItem): string {
        return 'img-map-' + item.id.replace(/[^a-zA-Z0-9]/g, '-');
    }
}
