import {AfterViewInit, Component, ElementRef, OnInit, OnDestroy, QueryList, ViewChildren} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import {defaults as defaultInteractions} from 'ol/interaction';
import Pointer from 'ol/interaction/Pointer';
import {PointService} from './services/point.service';
import {MonthMapItem, PeriodMapItem} from "../interfaces/map.interface";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import {Icon, Style} from "ol/style";
import {Point} from "ol/geom";
import {Feature} from "ol";
import {marker} from "../../assets/layout/images/marker";
import {ScreenStateConfig, ScreenStateBinder} from '../screen-state/interfaces/screen-state.interfaces';
import {ScreenStateService} from '../screen-state/services/screen-state.service';
import {bindState} from '../screen-state/helpers/manual-state.helper';
import {Subject} from 'rxjs';
import {takeUntil} from 'rxjs/operators';
import {EventsKey} from 'ol/events';
import {unByKey} from 'ol/Observable';
import {TileRequestQueue} from './tile-request-queue';
import {VisibleMapScheduler} from './visible-map-scheduler';

const currentYear = new Date().getFullYear();
const currentMonth = new Date().getMonth() + 1;

const CAPABILITIES = {
    "collections": [
        {
            "name": "s2_harmonized",
            "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
            "period": ["WET", "DRY", "MONTH"],
            "year": Array.from({length: currentYear - 2019 + 1}, (_, i) => 2019 + i),
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        },
        {
            "name": "landsat",
            "visparam": ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"],
            "period": ["WET", "DRY", "MONTH"],
            "year": Array.from({length: currentYear - 1985 + 1}, (_, i) => 1985 + i),
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        }
    ]
};

interface MapDefinition {
    id: string;
    period: string;
    year: number;
    type: 'sentinel' | 'landsat';
    visparam: string;
    month?: string;
}

const MAP_GRID_STATE_CONFIG: ScreenStateConfig = {
    screenKey: 'map-grid',
    group: 'grid-maps',
    strategy: 'storage-only',
    debounceMs: 300,
    schemaVersion: 1,
    fields: {
        selectedSentinelPeriod:   {type: 'string', defaultValue: 'WET'},
        selectedSentinelYear:     {type: 'string', defaultValue: String(currentYear)},
        selectedSentinelVisParam: {type: 'string', defaultValue: 'tvi-green'},
    }
};

@Component({
    selector: 'app-map-grid',
    templateUrl: './map-grid.component.html',
    styleUrls: ['./map-grid.component.scss']
})
export class MapGridComponent implements OnInit, AfterViewInit, OnDestroy {
    public sentinelMaps: (PeriodMapItem | MonthMapItem)[] = [];
    public landsatMaps: (PeriodMapItem | MonthMapItem)[] = [];
    private centerCoordinates = fromLonLat([-57.149819, -21.329828]);
    private mapsInstances: { id: string, map: Map, source: XYZ }[] = [];
    private readonly tileRequests = new TileRequestQueue();
    private markerLayersByMapId: { [mapId: string]: VectorLayer<any> } = {};
    private viewListenerKeys: { mapId: string, keys: EventsKey[] }[] = [];
    @ViewChildren('mapContainer') private mapContainers!: QueryList<ElementRef<HTMLElement>>;
    private mapDefinitions = new globalThis.Map<string, MapDefinition>();
    private zoom = 13;
    private readonly mapVisibility = new VisibleMapScheduler(
        target => {
            const definition = this.mapDefinitions.get(target.id);
            if (definition) this.createMap(definition, target);
        },
        id => this.hideMap(id),
    );
    private destroy$ = new Subject<void>();
    private _syncing = false;
    public pointCreationMode = false;
    public lat: number | null = null;
    public lon: number | null = null;
    public sentinelYears: any[] = [];
    public selectedSentinelYear: number | string = currentYear;

    public sentinelPeriods = ["WET", "DRY", "MONTH"];
    public landsatPeriods = ["WET", "DRY", "MONTH"];
    public sentinelVisParams = ["tvi-green", "tvi-red", "tvi-rgb"];
    public landsatVisParams = ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"];
    public selectedSentinelPeriod = this.sentinelPeriods[0];
    public selectedLandsatPeriod = this.landsatPeriods[0];
    public selectedSentinelVisParam = this.sentinelVisParams[0];
    public selectedLandsatVisParam = this.landsatVisParams[0];
    private stateBinder!: ScreenStateBinder<any>;

    constructor(
        public pointService: PointService,
        private route: ActivatedRoute,
        private router: Router,
        private screenStateService: ScreenStateService,
    ) {
    }

    ngOnInit(): void {
        this.stateBinder = bindState(
            MAP_GRID_STATE_CONFIG,
            this.route,
            this.router,
            this.screenStateService,
            {
                selectedSentinelPeriod: 'WET',
                selectedSentinelYear: String(currentYear),
                selectedSentinelVisParam: 'tvi-green',
            }
        );

        const restored = this.stateBinder.restore();
        this.selectedSentinelPeriod = restored.selectedSentinelPeriod;
        this.selectedSentinelYear = restored.selectedSentinelYear === 'Todos'
            ? 'Todos'
            : Number(restored.selectedSentinelYear);
        this.selectedSentinelVisParam = restored.selectedSentinelVisParam;

        const sentinelCapabilities = CAPABILITIES.collections.find(c => c.name === 's2_harmonized');
        if (sentinelCapabilities) {
            this.sentinelYears = [...sentinelCapabilities.year];
        }
        this.sentinelYears.unshift('Todos');

        this.initializeMaps();
        this.pointService.point$
            .pipe(takeUntil(this.destroy$))
            .subscribe(point => {
                if (Number.isFinite(point.lat) && Number.isFinite(point.lon)) {
                    this.updateCenterCoordinates([point.lon!, point.lat!]);
                }
            });
        this.pointService.setPoint({lat: -21.329828, lon: -57.149819});
    }

    ngAfterViewInit(): void {
        this.mapContainers.changes.pipe(takeUntil(this.destroy$)).subscribe(() => this.observeMapContainers());
        this.observeMapContainers();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
        this.mapVisibility.destroy();
        this.disposeAllMaps();
        this.stateBinder.destroy();
    }

    private observeMapContainers(): void {
        this.mapVisibility.observe(this.mapContainers.map(ref => ref.nativeElement));
    }

    private hideMap(mapId: string): void {
        const instance = this.mapsInstances.find(inst => inst.id === mapId);
        if (instance) {
            this.disposeMap(instance);
            this.mapsInstances = this.mapsInstances.filter(inst => inst !== instance);
        }
    }

    private disposeMap(inst: { id: string, map: Map, source: XYZ }): void {
        this.tileRequests.cancel(inst.source);
        const entry = this.viewListenerKeys.find(e => e.mapId === inst.id);
        if (entry) {
            for (const key of entry.keys) {
                unByKey(key);
            }
            this.viewListenerKeys = this.viewListenerKeys.filter(e => e !== entry);
        }
        const markerLayer = this.markerLayersByMapId[inst.id];
        if (markerLayer) {
            inst.map.removeLayer(markerLayer);
            delete this.markerLayersByMapId[inst.id];
        }
        inst.map.setTarget(undefined);
        inst.map.dispose();
        inst.source.clear();
        inst.source.dispose();
    }

    private disposeAllMaps(): void {
        for (const inst of this.mapsInstances) {
            this.disposeMap(inst);
        }
        this.mapsInstances = [];
        this.markerLayersByMapId = {};
        this.viewListenerKeys = [];
    }

    enablePointCreation(): void {
        this.pointCreationMode = true;
    }

    private addMarker(lat: number, lon: number, map: Map, mapId: string): void {
        const existing = this.markerLayersByMapId[mapId];
        if (existing) {
            map.removeLayer(existing);
        }

        const iconFeature = new Feature({
            geometry: new Point(fromLonLat([lon, lat])),
        });
        iconFeature.setStyle(new Style({
            image: new Icon({
                anchor: [0.5, 0.5],
                src: marker,
                scale: 1,
            }),
        }));

        const vectorLayer = new VectorLayer({
            source: new VectorSource({ features: [iconFeature] }),
        });
        map.addLayer(vectorLayer);
        this.markerLayersByMapId[mapId] = vectorLayer;
    }

    private initializeMaps(): void {
        this.createMaps('sentinel', this.selectedSentinelPeriod, this.selectedSentinelVisParam);
        this.createMaps('landsat', this.selectedLandsatPeriod, this.selectedLandsatVisParam);
    }

    private createMaps(type: 'sentinel' | 'landsat', selectedPeriod: string, visparam: string): void {
        if (type === 'sentinel') {
            this.sentinelMaps = [];
        } else {
            this.landsatMaps = [];
        }
        const capabilities = CAPABILITIES.collections.find(c => c.name === (type === 'sentinel' ? 's2_harmonized' : 'landsat'));
        if (capabilities) {
            for (let year of capabilities.year) {
                if (this.selectedSentinelYear != 'Todos' && year !== Number(this.selectedSentinelYear)) {
                    continue;
                }
                if (selectedPeriod === 'MONTH') {
                    for (let month of capabilities.month) {
                        if (year === currentYear && parseInt(month, 10) > currentMonth) {
                            break;
                        }
                        let mapId = `${type}-map-${month}-${year}`;
                        this.addMap(type, mapId, 'MONTH', year, visparam, month);
                    }
                } else {
                    let mapId = `${type}-map-${selectedPeriod}-${year}`;
                    this.addMap(type, mapId, selectedPeriod, year, visparam);
                }
            }
        }
    }

    private addMap(type: 'sentinel' | 'landsat', mapId: string, periodOrMonth: string, _year: number | string, visparam: string, month?: string): void {
        if (_year == 'Todos') {
            return;
        }
        const year = Number(_year);
        if (type === 'sentinel') {
            if (periodOrMonth === 'MONTH') {
                this.sentinelMaps.push({month: month as string, year, id: mapId});
            } else {
                this.sentinelMaps.push({period: periodOrMonth, year, id: mapId});
            }
        } else {
            if (periodOrMonth === 'MONTH') {
                this.landsatMaps.push({month: month as string, year, id: mapId});
            } else {
                this.landsatMaps.push({period: periodOrMonth, year, id: mapId});
            }
        }
        this.mapDefinitions.set(mapId, {id: mapId, period: periodOrMonth, year, type, visparam, month});
    }

    private createMap(definition: MapDefinition, target: HTMLElement): void {
        const {id: mapId, period, year, type, visparam, month} = definition;
        const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/${type === 'sentinel' ? 's2_harmonized' : 'landsat'}/{x}/{y}/{z}?period=${period}&year=${year}&visparam=${visparam}${period === 'MONTH' ? `&month=${month}` : ''}`;
        const source = new XYZ({
            url,
            tileLoadFunction: (tile, src) => this.tileRequests.load(source, tile, src),
            attributions: `${period === 'MONTH' ? `Month ${month}` : period} - ${year}`,
            attributionsCollapsible: false,
        });
        const map = new Map({
            target,
            maxTilesLoading: 4,
            layers: [new TileLayer({source})],
            view: new View({center: this.centerCoordinates.slice(), zoom: this.zoom}),
            interactions: defaultInteractions().extend([
                new Pointer({
                    handleDownEvent: event => {
                        if (!this.pointCreationMode) return false;
                        this.pointCreationMode = false;
                        const [lon, lat] = toLonLat(event.coordinate);
                        this.pointService.setPoint({lat, lon});
                        return true;
                    }
                })
            ])
        });
        this.addMarker(this.lat ?? -21.329828, this.lon ?? -57.149819, map, mapId);
        this.mapsInstances.push({id: mapId, map, source});
        this.addViewSyncListener(mapId, map);
    }

    private addViewSyncListener(mapId: string, sourceMap: Map): void {
        const view = sourceMap.getView();
        const k1 = view.on('change:center', () => this.syncViews(sourceMap)) as EventsKey;
        const k2 = view.on('change:resolution', () => this.syncViews(sourceMap)) as EventsKey;
        this.viewListenerKeys.push({mapId, keys: [k1, k2]});
    }

    private syncViews(sourceMap: Map): void {
        if (this._syncing) return;
        this._syncing = true;
        try {
            const sourceView = sourceMap.getView();
            const center = sourceView.getCenter();
            const zoom = sourceView.getZoom();
            if (center) this.centerCoordinates = center.slice();
            if (zoom !== undefined) this.zoom = zoom;
            for (const inst of this.mapsInstances) {
                if (inst.map === sourceMap) continue;
                const view = inst.map.getView();
                if (center) view.setCenter(center.slice());
                if (zoom !== undefined) view.setZoom(zoom);
            }
        } finally {
            this._syncing = false;
        }
    }

    private updateCenterCoordinates(coordinates: [number, number]): void {
        const center = fromLonLat(coordinates);
        if (this.lon === coordinates[0] && this.lat === coordinates[1] &&
            center.every((value, index) => value === this.centerCoordinates[index])) return;
        this.lon = coordinates[0];
        this.lat = coordinates[1];
        this.centerCoordinates = center;
        this.updateMaps();
    }

    private updateMaps(): void {
        for (let mapInstance of this.mapsInstances) {
            this.tileRequests.cancel(mapInstance.source);
            mapInstance.source.refresh();
            const view = mapInstance.map.getView();
            view.setCenter(this.centerCoordinates);
            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], mapInstance.map, mapInstance.id);
        }
    }

    private disposeSentinelMaps(): void {
        this.mapVisibility.reset();
        for (const [id, definition] of this.mapDefinitions) {
            if (definition.type === 'sentinel') this.mapDefinitions.delete(id);
        }
        const toDispose = this.mapsInstances.filter(m => m.id.startsWith('sentinel-map'));
        for (const inst of toDispose) {
            this.disposeMap(inst);
        }
        this.mapsInstances = this.mapsInstances.filter(m => !m.id.startsWith('sentinel-map'));
    }

    public updateSentinelMaps(): void {
        this.disposeSentinelMaps();
        this.createMaps('sentinel', this.selectedSentinelPeriod, this.selectedSentinelVisParam);
        this.stateBinder.patchAndPersist({
            selectedSentinelPeriod: this.selectedSentinelPeriod,
            selectedSentinelYear: String(this.selectedSentinelYear),
            selectedSentinelVisParam: this.selectedSentinelVisParam,
        });
    }

    public clearFilters(): void {
        this.stateBinder.reset();
        const s = this.stateBinder.state;
        this.selectedSentinelPeriod = s.selectedSentinelPeriod;
        this.selectedSentinelYear = currentYear;
        this.selectedSentinelVisParam = s.selectedSentinelVisParam;
        this.disposeSentinelMaps();
        this.createMaps('sentinel', this.selectedSentinelPeriod, this.selectedSentinelVisParam);
    }
}
