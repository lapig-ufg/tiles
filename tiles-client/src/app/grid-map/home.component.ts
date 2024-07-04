import { Component, OnInit } from '@angular/core';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import { fromLonLat, toLonLat } from 'ol/proj';
import { LayerService } from "./services/layer.service";
import { defaults as defaultInteractions } from 'ol/interaction';
import { Pointer } from 'ol/interaction';
import {PointService} from "./services/point.service";

@Component({
    selector: 'app-map-grid',
    templateUrl: './home.component.html',
})
export class HomeComponent implements OnInit {
    private periods = ['WET', 'DRY'];
    private years = [2017, 2018, 2019, 2020, 2021, 2022, 2023];
    private months = ['02', '09']; // February and September
    public maps: { period: string, year: number, id: string }[] = [];
    private centerCoordinates = fromLonLat([-57.149819, -21.329828]);
    private mapsInstances: Map[] = [];

    constructor(private layerService: LayerService, public pointService: PointService) { }

    ngOnInit(): void {
        this.initializeMaps();
        this.pointService.point$.subscribe({
            next: point => {
                if(point.lat && point.lon){
                    this.updateCenterCoordinates([point.lon, point.lat]);
                }
            }
        })
    }

    private initializeMaps(): void {
        for (let year of this.years) {
            for (let month of this.months) {
                let period = month === '02' ? 'WET' : 'DRY';
                let mapId = `map-${period}-${year}`;
                this.maps.push({ period, year, id: mapId });
                this.createMap(mapId, period, year);
            }
        }
    }

    private createMap(mapId: string, period: string, year: number): void {
        setTimeout(() => {
            const map = new Map({
                target: mapId,
                layers: [
                    new TileLayer({
                        source: new XYZ({
                            url: `https://tm{1-5}.lapig.iesa.ufg.br/api/layers/s2_harmonized/${period}/${year}/{x}/{y}/{z}`,
                            attributions: `${period} - ${year}`,
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

            this.mapsInstances.push(map);
        }, 0);
    }

    private updateCenterCoordinates(coordinates: [number, number]): void {
        this.centerCoordinates = fromLonLat(coordinates);
        this.updateMaps();
    }

    private updateMaps(): void {
        for (let mapInstance of this.mapsInstances) {
            const view = mapInstance.getView();
            view.setCenter(this.centerCoordinates);
        }
    }
}
