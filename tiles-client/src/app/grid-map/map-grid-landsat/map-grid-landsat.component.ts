import { Component, OnInit } from '@angular/core';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import { defaults as defaultInteractions } from 'ol/interaction';
import Pointer from 'ol/interaction/Pointer';
import {MonthMapItem, PeriodMapItem} from "../../interfaces/map.interface";
import {LayerService} from "../services/layer.service";
import {PointService} from "../services/point.service";
import {Feature} from "ol";
import {Point} from "ol/geom";
import {Icon, Style} from "ol/style";
import {marker} from "../../../assets/layout/images/marker";
import VectorSource from "ol/source/Vector";
import VectorLayer from "ol/layer/Vector";
const currentYear = new Date().getFullYear();
const currentMonth = new Date().getMonth() + 1;

const CAPABILITIES = {
    "collections": [
        {
            "name": "s2_harmonized",
            "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
            "period": ["WET", "DRY", "MONTH"],
            "year": Array.from({ length: currentYear - 2019 + 1 }, (_, i) => 2019 + i),
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        },
        {
            "name": "landsat",
            "visparam": ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"],
            "period": ["WET", "DRY", "MONTH"],
            "year": Array.from({ length: currentYear - 1985 + 1 }, (_, i) => 1985 + i),
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        }
    ]
};

@Component({
    selector: 'app-map-grid-landsat',
    templateUrl: './map-grid-landsat.component.html',
    styleUrls: ['./map-grid-landsat.component.scss']
})
export class MapGridLandsatComponent implements OnInit {
    public sentinelMaps: (PeriodMapItem | MonthMapItem)[] = [];
    public landsatMaps: (PeriodMapItem | MonthMapItem)[] = [];
    private centerCoordinates = fromLonLat([-57.149819, -21.329828]);
    private mapsInstances: { id: string, map: Map }[] = [];
    public lat: number | null = null;
    public lon: number | null = null;

    public landsatYears: number[] = [];
    public selectedLandsatYear: number = currentYear;

    public sentinelPeriods = ["WET", "DRY", "MONTH"];
    public landsatPeriods = ["WET", "DRY", "MONTH"];
    public sentinelVisParams = ["tvi-green", "tvi-red", "tvi-rgb"];
    public landsatVisParams = ["landsat-tvi-false", "landsat-tvi-true", "landsat-tvi-agri"];
    public selectedSentinelPeriod = this.sentinelPeriods[0];
    public selectedLandsatPeriod = this.landsatPeriods[0];
    public selectedSentinelVisParam = this.sentinelVisParams[0];
    public selectedLandsatVisParam = this.landsatVisParams[0];

    constructor(public pointService: PointService) {}

    ngOnInit(): void {
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
        const landsatCapabilities = CAPABILITIES.collections.find(c => c.name === 'landsat');
        if (landsatCapabilities) {
            this.landsatYears = landsatCapabilities.year;
        }
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
                if (year !== this.selectedLandsatYear) {
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

    private addMap(type: 'sentinel' | 'landsat', mapId: string, periodOrMonth: string, year: number, visparam: string, month?: string): void {
        if (type === 'sentinel') {
            if (periodOrMonth === 'MONTH') {
                this.sentinelMaps.push({ month: month as string, year, id: mapId });
            } else {
                this.sentinelMaps.push({ period: periodOrMonth, year, id: mapId });
            }
        } else {
            if (periodOrMonth === 'MONTH') {
                this.landsatMaps.push({ month: month as string, year, id: mapId });
            } else {
                this.landsatMaps.push({ period: periodOrMonth, year, id: mapId });
            }
        }
        this.createMap(mapId, periodOrMonth, year, type, visparam, month);
    }

    private createMap(mapId: string, periodOrMonth: string, year: number, type: string, visparam: string, month?: string): void {
        setTimeout(() => {
            const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/${type === 'sentinel' ? 's2_harmonized' : 'landsat'}/{x}/{y}/{z}?period=${periodOrMonth}&year=${year}&visparam=${visparam}${periodOrMonth === 'MONTH' ? `&month=${month}` : ''}`;
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
                            this.updateCenterCoordinates(coordinates);
                            return true; // stop event propagation
                        }
                    })
                ])
            });
            const projectedCoordinate = transform(this.centerCoordinates, 'EPSG:3857', 'EPSG:4326');
            this.addMarker(projectedCoordinate[1], projectedCoordinate[0], map);
            this.mapsInstances.push({ id: mapId, map });
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
    public updateLandsatMaps(): void {
        this.mapsInstances = this.mapsInstances.filter(mapInstance => !mapInstance.id.startsWith('landsat-map'));
        this.createMaps('landsat', this.selectedLandsatPeriod, this.selectedLandsatVisParam);
    }
}
