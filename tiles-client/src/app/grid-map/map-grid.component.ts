import {Component, OnInit, OnDestroy} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import {LayerService} from "./services/layer.service";
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
export class MapGridComponent implements OnInit, OnDestroy {
    public sentinelMaps: (PeriodMapItem | MonthMapItem)[] = [];
    public landsatMaps: (PeriodMapItem | MonthMapItem)[] = [];
    private centerCoordinates = fromLonLat([-57.149819, -21.329828]);
    private mapsInstances: { id: string, map: Map }[] = [];
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
        this.selectedSentinelYear = restored.selectedSentinelYear === String(currentYear)
            ? currentYear
            : restored.selectedSentinelYear;
        this.selectedSentinelVisParam = restored.selectedSentinelVisParam;

        const sentinelCapabilities = CAPABILITIES.collections.find(c => c.name === 's2_harmonized');
        if (sentinelCapabilities) {
            this.sentinelYears = [...sentinelCapabilities.year];
        }
        this.sentinelYears.unshift('Todos');

        this.initializeMaps();
        this.pointService.setPoint({lat: -21.329828, lon: -57.149819})
        this.pointService.point$.subscribe({
            next: point => {
                if (point.lat && point.lon) {
                    this.lat = point.lat;
                    this.lon = point.lon;
                    this.updateCenterCoordinates([point.lon, point.lat]);
                }
            }
        });
    }

    ngOnDestroy(): void {
        this.stateBinder.destroy();
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

        const vectorSource = new VectorSource({
            features: [iconFeature],
        });
        const vectorLayer = new VectorLayer({
            source: vectorSource,
        });
        map.addLayer(vectorLayer)
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
                if (this.selectedSentinelYear != 'Todos' && year !== this.selectedSentinelYear) {
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
        this.createMap(mapId, periodOrMonth, year, type, visparam, month);
    }

    private createMap(mapId: string, periodOrMonth: string, year: number, type: string, visparam: string, month?: string): void {
        setTimeout(() => {
            const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/${type === 'sentinel' ? 's2_harmonized' : 'landsat'}/{x}/{y}/{z}?period=${periodOrMonth}&year=${year}&visparam=${visparam}${periodOrMonth === 'MONTH' ? `&month=${month}` : ''}`;
            // const url = `http://127.0.0.1:8000/api/layers/${type === 'sentinel' ? 's2_harmonized' : 'landsat'}/{x}/{y}/{z}?period=${periodOrMonth}&year=${year}&visparam=${visparam}${periodOrMonth === 'MONTH' ? `&month=${month}` : ''}`;
            const map = new Map({
                target: mapId,
                layers: [
                    new TileLayer({
                        source: new XYZ({
                            url,
                            attributions: `${periodOrMonth === 'MONTH' ? `Month ${month}` : periodOrMonth} - ${year}`,
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
                            console.log(coordinates)
                            this.updateCenterCoordinates(coordinates);
                            return true;
                        }
                    })
                ])
            });
            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], map);

            this.mapsInstances.push({id: mapId, map});
        }, 0);
    }

    private updateCenterCoordinates(coordinates: [number, number]): void {
        this.centerCoordinates = fromLonLat(coordinates);
        this.updateMaps();
    }

    private updateMaps(): void {
        for (let mapInstance of this.mapsInstances) {
            const view = mapInstance.map.getView();
            view.setCenter(this.centerCoordinates);
            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], mapInstance.map);
        }
    }

    public updateSentinelMaps(): void {
        this.mapsInstances = this.mapsInstances.filter(mapInstance => !mapInstance.id.startsWith('sentinel-map'));
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
        this.mapsInstances = this.mapsInstances.filter(m => !m.id.startsWith('sentinel-map'));
        this.createMaps('sentinel', this.selectedSentinelPeriod, this.selectedSentinelVisParam);
    }
}
