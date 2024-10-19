import {Component, OnInit} from '@angular/core';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import {fromLonLat, toLonLat, transform} from 'ol/proj';
import {defaults as defaultInteractions} from 'ol/interaction';
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
import {Observable} from "rxjs";

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
export class MapGridLandsatComponent implements OnInit {
    public landsatMaps: (PeriodMapItem | MonthMapItem)[] = [];
    private centerCoordinates = fromLonLat([-57.149819, -21.329828]);
    private mapsInstances: { id: string, map: Map }[] = [];
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
        this.pointService.setPoint({lat: -21.329828, lon: -57.149819});
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
        this.landsatYears.unshift('Todos');
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
        map.addLayer(vectorLayer);
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
                     if (this.selectedLandsatYear != 'Todos' && year !== this.selectedLandsatYear) {
                        continue;
                    }
                    const startDate = new Date(year - 1, 8, 1); // September 1 of previous year
                    const endDate = new Date(year, 7, 31); // August 31 of the current year
                    this.loadProdesYearMaps(type, year, visparam, startDate, endDate, selectedPeriod);
                } else {
                    if (this.selectedLandsatYear != 'Todos' && year !== this.selectedLandsatYear) {
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

    private addMap(type: 'landsat', mapId: string, periodOrMonth: string, year: number, visparam: string, month?: string): void {
        const url = `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/landsat/{x}/{y}/{z}?period=${periodOrMonth}&year=${year}&visparam=${visparam}${periodOrMonth === 'MONTH' ? `&month=${month}` : ''}`;
        setTimeout(() => {
            const map = new Map({
                target: mapId,
                layers: [
                    new TileLayer({
                        source: new XYZ({
                            url,
                            attributions: `${periodOrMonth === 'MONTH' ? `Month ${month}` : periodOrMonth} - ${year}`,
                            attributionsCollapsible: false,
                        }),
                    }),
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
                    }),
                ]),
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

    public updateLandsatMaps(): void {
        this.mapsInstances = this.mapsInstances.filter(mapInstance => !mapInstance.id.startsWith('landsat-map'));
        this.createMaps('landsat', this.selectedLandsatPeriod, this.selectedLandsatVisParam);
    }
    public getObjectKeys(object: any): string[] {
    return Object.keys(object);
  }
}
