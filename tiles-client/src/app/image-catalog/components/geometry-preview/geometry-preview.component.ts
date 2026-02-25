import { Component, Input, OnChanges, SimpleChanges, AfterViewInit, OnDestroy, ElementRef, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Feature } from 'ol';
import { Geometry, Point } from 'ol/geom';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { fromLonLat } from 'ol/proj';
import { Style, Icon, Stroke, Fill, Circle as CircleStyle } from 'ol/style';
import { ButtonModule } from 'primeng/button';
import { TooltipModule } from 'primeng/tooltip';
import { createGeometryStyle } from '../../../shared/utils/geometry.utils';
import { marker } from '../../../../assets/layout/images/marker';

export interface PreviewRepresentativePoint {
    lat: number;
    lon: number;
}

@Component({
    selector: 'app-geometry-preview',
    standalone: true,
    imports: [CommonModule, ButtonModule, TooltipModule],
    template: `
        <div class="flex align-items-center justify-content-between mb-2">
            <span class="font-semibold text-sm">Preview da Geometria</span>
            <div>
                <button pButton
                    icon="pi pi-search-plus"
                    class="p-button-text p-button-sm p-button-rounded mr-1"
                    (click)="zoomToFit()"
                    pTooltip="Zoom to fit"
                    tooltipPosition="top">
                </button>
                <button pButton
                    [icon]="geometryVisible ? 'pi pi-eye' : 'pi pi-eye-slash'"
                    class="p-button-text p-button-sm p-button-rounded"
                    (click)="toggleGeometry()"
                    [pTooltip]="geometryVisible ? 'Ocultar geometria' : 'Mostrar geometria'"
                    tooltipPosition="top">
                </button>
            </div>
        </div>
        <div #mapContainer style="width: 100%; height: 250px; border-radius: 6px; overflow: hidden;"></div>
    `,
})
export class GeometryPreviewComponent implements AfterViewInit, OnChanges, OnDestroy {
    @Input() feature: Feature<Geometry> | null = null;
    @Input() representativePoint: PreviewRepresentativePoint | null = null;

    @ViewChild('mapContainer', { static: false }) mapContainer!: ElementRef;

    geometryVisible = true;
    private map: Map | null = null;
    private geometryLayer: VectorLayer<Feature<Geometry>> | null = null;
    private markerLayer: VectorLayer<Feature<Geometry>> | null = null;
    private initialized = false;

    ngAfterViewInit(): void {
        setTimeout(() => this.initMap(), 0);
    }

    ngOnChanges(changes: SimpleChanges): void {
        if (this.initialized && (changes['feature'] || changes['representativePoint'])) {
            this.updateLayers();
        }
    }

    ngOnDestroy(): void {
        if (this.map) {
            this.map.setTarget(undefined);
            this.map = null;
        }
    }

    zoomToFit(): void {
        if (!this.map || !this.geometryLayer) return;
        const source = this.geometryLayer.getSource();
        if (!source || source.isEmpty()) return;
        this.map.getView().fit(source.getExtent(), {
            padding: [50, 50, 50, 50],
            maxZoom: 16,
            duration: 300,
        });
    }

    toggleGeometry(): void {
        this.geometryVisible = !this.geometryVisible;
        if (this.geometryLayer) {
            this.geometryLayer.setVisible(this.geometryVisible);
        }
    }

    private initMap(): void {
        if (!this.mapContainer) return;

        this.map = new Map({
            target: this.mapContainer.nativeElement,
            layers: [
                new TileLayer({ source: new OSM() }),
            ],
            view: new View({
                center: this.representativePoint
                    ? fromLonLat([this.representativePoint.lon, this.representativePoint.lat])
                    : fromLonLat([-49.2646, -16.6799]),
                zoom: 13,
            }),
        });

        this.initialized = true;
        this.updateLayers();
    }

    private updateLayers(): void {
        if (!this.map) return;

        // Remove old layers
        if (this.geometryLayer) {
            this.map.removeLayer(this.geometryLayer);
            this.geometryLayer = null;
        }
        if (this.markerLayer) {
            this.map.removeLayer(this.markerLayer);
            this.markerLayer = null;
        }

        if (!this.feature) return;

        // Geometry layer
        const geomClone = this.feature.clone();
        const gLayer = new VectorLayer({
            source: new VectorSource({ features: [geomClone] }),
            style: createGeometryStyle(),
            visible: this.geometryVisible,
        });
        this.geometryLayer = gLayer;
        this.map.addLayer(gLayer);

        // Marker on representative point
        if (this.representativePoint) {
            const markerFeature = new Feature({
                geometry: new Point(fromLonLat([this.representativePoint.lon, this.representativePoint.lat])),
            }) as Feature<Geometry>;
            markerFeature.setStyle(new Style({
                image: new Icon({
                    anchor: [0.5, 0.5],
                    src: marker,
                    scale: 1,
                }),
            }));
            const mLayer = new VectorLayer({
                source: new VectorSource({ features: [markerFeature] }),
            });
            this.markerLayer = mLayer;
            this.map.addLayer(mLayer);
        }

        // Zoom to fit geometry
        this.zoomToFit();
    }
}
