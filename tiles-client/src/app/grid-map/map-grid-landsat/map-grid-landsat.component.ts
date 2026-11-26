import {AfterViewInit, Component, ElementRef, OnDestroy, OnInit, QueryList, ViewChildren} from '@angular/core';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import {defaults as defaultInteractions} from 'ol/interaction';
import Pointer from 'ol/interaction/Pointer';
import {MonthMapItem, PeriodMapItem} from "../../interfaces/map.interface";
import {PointService} from "../services/point.service";
import {Feature} from "ol";
import {Point} from "ol/geom";
import {Icon, Style} from "ol/style";
import {marker} from "../../../assets/layout/images/marker";
import VectorSource from "ol/source/Vector";
import VectorLayer from "ol/layer/Vector";
import {Observable, Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";
import {EventsKey} from "ol/events";
import {unByKey} from "ol/Observable";

import {TileRequestQueue} from '../tile-request-queue';
import {VisibleMapScheduler} from '../visible-map-scheduler';

interface MapDefinition {
    id: string;
    period: string;
    year: number;
    visparam: string;
    month?: string;
}

const currentYear = new Date().getFullYear();
const currentMonth = new Date().getMonth() + 1;

const CAPABILITIES = {
    "collections": [
        {
            "name": "landsat",
            "visparam": ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"],
            "period": ["WET", "DRY", "MONTH"],
            "year": Array.from({length: currentYear - 1985 + 1}, (_, i) => 1985 + i),
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        }
    ]
};

@Component({
    selector: 'app-map-grid-landsat',
    templateUrl: './map-grid-landsat.component.html',
    styleUrls: ['./map-grid-landsat.component.scss']
})
export class MapGridLandsatComponent implements OnInit, AfterViewInit, OnDestroy {
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

    public landsatYears: any[] = [];
    public selectedLandsatYear: number | string = currentYear;

    public landsatPeriods = ["WET", "DRY", "MONTH"];
    public landsatVisParams = ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"];
    public selectedLandsatPeriod = this.landsatPeriods[0];
    public selectedLandsatVisParam = this.landsatVisParams[0];

    public yearTypes = ['TRADITIONAL', 'PRODES-YEAR'];
    public selectedYearType: string = this.yearTypes[0];
    public pointInfo$: Observable<any> = this.pointService.pointInfo$;

    constructor(public pointService: PointService) {
    }

    ngOnInit(): void {
        this.initializeMaps();
        this.pointService.point$
            .pipe(takeUntil(this.destroy$))
            .subscribe(point => {
                if (Number.isFinite(point.lat) && Number.isFinite(point.lon)) {
                    this.updateCenterCoordinates([point.lon!, point.lat!]);
                }
            });
        this.pointService.setPoint({lat: -21.329828, lon: -57.149819});
        const landsatCapabilities = CAPABILITIES.collections.find(c => c.name === 'landsat');
        if (landsatCapabilities) {
            this.landsatYears = [...landsatCapabilities.year];
        }
        this.landsatYears.unshift('Todos');
    }

    ngAfterViewInit(): void {
        this.mapContainers.changes.pipe(takeUntil(this.destroy$)).subscribe(() => this.observeMapContainers());
        this.observeMapContainers();
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

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
        this.mapVisibility.destroy();
        this.disposeAllMaps();
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
        this.createMaps('landsat', this.selectedLandsatPeriod, this.selectedLandsatVisParam);
    }

    private createMaps(type: 'landsat', selectedPeriod: string, visparam: string): void {
        this.landsatMaps = [];

        const capabilities = CAPABILITIES.collections.find(c => c.name === 'landsat');

        if (capabilities) {
            for (let year of capabilities.year) {
                if (this.selectedYearType === 'PRODES-YEAR') {
                     if (this.selectedLandsatYear != 'Todos' && year !== Number(this.selectedLandsatYear)) {
                        continue;
                    }
                    const startDate = new Date(year - 1, 8, 1); // September 1 of previous year
                    const endDate = new Date(year, 7, 31); // August 31 of the current year
                    this.loadProdesYearMaps(type, year, visparam, startDate, endDate, selectedPeriod);
                } else {
                    if (this.selectedLandsatYear != 'Todos' && year !== Number(this.selectedLandsatYear)) {
                        continue;
                    }
                    this.loadTraditionalYearMaps(type, year, visparam, selectedPeriod);
                }
            }
        }
    }

private loadProdesYearMaps(type: 'landsat', year: number, visparam: string, startDate: Date, endDate: Date, selectedPeriod: string): void {
    // Se o selectedPeriod for 'MONTH', carregar os meses entre startDate e endDate
    if (selectedPeriod === 'MONTH') {
        let startMonth = startDate.getMonth() + 1; // meses em JavaScript são baseados em 0
        let startYear = startDate.getFullYear();
        let endMonth = endDate.getMonth() + 1;
        let endYear = endDate.getFullYear();

        // Loop para cada mês entre startDate e endDate
        for (let y = startYear; y <= endYear; y++) {
            let fromMonth = (y === startYear) ? startMonth : 1;
            let toMonth = (y === endYear) ? endMonth : 12;

            for (let m = fromMonth; m <= toMonth; m++) {
                let month = m < 10 ? `0${m}` : `${m}`; // Formata o mês com dois dígitos
                if (y === currentYear && parseInt(month, 10) > currentMonth) {
                    break;
                }

                // Gera o ID do mapa e adiciona à lista
                let mapId = `${type}-map-${month}-${y}`;
                this.landsatMaps.push({ month, year: y, id: mapId });

                // Adiciona o mapa
                this.addMap(type, mapId, 'MONTH', y, visparam, month);
            }
        }
    } else {
        // Se não for 'MONTH', carrega o mapa do período selecionado
        let mapId = `${type}-map-${selectedPeriod}-${year}`;
        this.landsatMaps.push({ period: selectedPeriod, year, id: mapId });
        this.addMap(type, mapId, selectedPeriod, year, visparam);
    }
}

    private loadTraditionalYearMaps(type: 'landsat', year: number, visparam: string, selectedPeriod: string): void {
        if (selectedPeriod === 'MONTH') {
            for (let month of CAPABILITIES.collections[0].month) {
                if (year === currentYear && parseInt(month, 10) > currentMonth) {
                    break;
                }
                let mapId = `${type}-map-${month}-${year}`;

                // Adiciona o mapa à lista de mapas Landsat
                this.landsatMaps.push({month, year, id: mapId});

                this.addMap(type, mapId, 'MONTH', year, visparam, month);
            }
        } else {
            let mapId = `${type}-map-${selectedPeriod}-${year}`;

            // Adiciona o mapa à lista de mapas Landsat
            this.landsatMaps.push({period: selectedPeriod, year, id: mapId});

            this.addMap(type, mapId, selectedPeriod, year, visparam);
        }
    }

    private addMap(type: 'landsat', mapId: string, period: string, year: number, visparam: string, month?: string): void {
        this.mapDefinitions.set(mapId, {id: mapId, period, year, visparam, month});
    }

    private createMap(definition: MapDefinition, target: HTMLElement): void {
        const {id: mapId, period, year, visparam, month} = definition;
        const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/landsat/{x}/{y}/{z}?period=${period}&year=${year}&visparam=${visparam}${period === 'MONTH' ? `&month=${month}` : ''}`;
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

    public updateLandsatMaps(): void {
        this.mapVisibility.reset();
        this.disposeAllMaps();
        this.mapDefinitions.clear();
        this.createMaps('landsat', this.selectedLandsatPeriod, this.selectedLandsatVisParam);
    }

    public getObjectKeys(object: any): string[] {
    return Object.keys(object);
  }
}
