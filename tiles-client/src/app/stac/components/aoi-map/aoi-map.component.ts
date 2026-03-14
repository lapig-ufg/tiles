import { Component, OnInit, OnDestroy, Input, Output, EventEmitter, ElementRef, ViewChild, AfterViewInit, ChangeDetectorRef, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { ButtonModule } from 'primeng/button';
import { InputNumberModule } from 'primeng/inputnumber';
import { FileUploadModule } from 'primeng/fileupload';
import { TooltipModule } from 'primeng/tooltip';
import { SelectButtonModule } from 'primeng/selectbutton';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import OSM from 'ol/source/OSM';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Draw, { createBox } from 'ol/interaction/Draw';
import { fromLonLat, transformExtent } from 'ol/proj';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { GeoJSON } from 'ol/format';
import { Style, Stroke, Fill } from 'ol/style';
import { PointService } from '../../../grid-map/services/point.service';
import { createGeometryStyle } from '../../../shared/utils/geometry.utils';

@Component({
  selector: 'app-aoi-map',
  standalone: true,
  imports: [CommonModule, FormsModule, ButtonModule, InputNumberModule, FileUploadModule, TooltipModule, SelectButtonModule],
  templateUrl: './aoi-map.component.html',
  styleUrls: ['./aoi-map.component.scss'],
})
export class AoiMapComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() basemap: string = 'dark';
  @Input() center: [number, number] = [-49.2646, -16.6799];

  @Output() bboxChange = new EventEmitter<[number, number, number, number]>();
  @Output() geometryChange = new EventEmitter<GeoJSON.Geometry | null>();

  @ViewChild('mapContainer', { static: true }) mapContainer!: ElementRef;

  map!: Map;
  lng: number = -49.2646;
  lat: number = -16.6799;

  currentBbox: [number, number, number, number] | null = null;
  drawingMode: boolean = false;

  // GeoJSON upload
  geoJsonFeatures: Feature<Geometry>[] = [];
  currentFeatureIndex: number = 0;
  geoJsonFileName: string | null = null;

  basemapOptions = [
    { label: 'Dark', value: 'dark', icon: 'pi pi-moon' },
    { label: 'Sat', value: 'satellite', icon: 'pi pi-image' },
    { label: 'Ruas', value: 'streets', icon: 'pi pi-map' },
  ];

  private baseTileLayer!: TileLayer<any>;
  private bboxLayer!: VectorLayer<Feature<Geometry>>;
  private bboxSource!: VectorSource<Feature<Geometry>>;
  private geojsonLayer!: VectorLayer<Feature<Geometry>>;
  private geojsonSource!: VectorSource<Feature<Geometry>>;
  private drawInteraction: Draw | null = null;
  private subscriptions: Subscription[] = [];

  constructor(
    private pointService: PointService,
    private cdr: ChangeDetectorRef,
    private ngZone: NgZone,
  ) {}

  ngOnInit(): void {
    this.lng = this.center[0];
    this.lat = this.center[1];
  }

  ngAfterViewInit(): void {
    this.initMap();
  }

  ngOnDestroy(): void {
    this.subscriptions.forEach(s => s.unsubscribe());
    this.removeDrawInteraction();
    if (this.map) {
      this.map.setTarget(undefined);
    }
  }

  private initMap(): void {
    this.baseTileLayer = new TileLayer({
      source: this.getBasemapSource(this.basemap),
    });

    this.bboxSource = new VectorSource();
    this.bboxLayer = new VectorLayer({
      source: this.bboxSource,
      style: new Style({
        stroke: new Stroke({ color: '#f59e0b', width: 2.5, lineDash: [6, 4] }),
        fill: new Fill({ color: 'rgba(245, 158, 11, 0.08)' }),
      }),
    });

    this.geojsonSource = new VectorSource();
    this.geojsonLayer = new VectorLayer({
      source: this.geojsonSource,
      style: createGeometryStyle(),
    });

    this.map = new Map({
      target: this.mapContainer.nativeElement,
      layers: [this.baseTileLayer, this.bboxLayer, this.geojsonLayer],
      view: new View({
        center: fromLonLat([this.lng, this.lat]),
        zoom: 5,
      }),
      controls: [],
    });

    // Subscribe to external feature changes — wrapped in NgZone + setTimeout to avoid NG0100
    const sub = this.pointService.activeFeature$.subscribe(feature => {
      if (feature) {
        this.ngZone.run(() => {
          this.geojsonSource.clear();
          this.geojsonSource.addFeature(feature.clone());
          const extent = feature.getGeometry()?.getExtent();
          if (extent) {
            this.map.getView().fit(extent, { padding: [30, 30, 30, 30], maxZoom: 14 });
            const bbox4326 = transformExtent(extent, 'EPSG:3857', 'EPSG:4326') as [number, number, number, number];
            setTimeout(() => this.setBbox(bbox4326));
          }
        });
      }
    });
    this.subscriptions.push(sub);
  }

  private setBbox(bbox: [number, number, number, number]): void {
    this.currentBbox = bbox;
    this.bboxChange.emit(bbox);

    this.bboxSource.clear();
    const format = new GeoJSON();
    const geojsonBbox = {
      type: 'Feature' as const,
      geometry: {
        type: 'Polygon' as const,
        coordinates: [[
          [bbox[0], bbox[1]], [bbox[2], bbox[1]],
          [bbox[2], bbox[3]], [bbox[0], bbox[3]],
          [bbox[0], bbox[1]],
        ]],
      },
      properties: {},
    };
    const feature = format.readFeature(geojsonBbox, {
      dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857',
    });
    this.bboxSource.addFeature(feature as Feature<Geometry>);
    this.cdr.detectChanges();
  }

  // --- Draw mode ---

  toggleDrawMode(): void {
    this.drawingMode = !this.drawingMode;
    if (this.drawingMode) {
      this.addDrawInteraction();
    } else {
      this.removeDrawInteraction();
    }
  }

  private addDrawInteraction(): void {
    this.removeDrawInteraction();
    this.drawInteraction = new Draw({
      source: this.bboxSource,
      type: 'Circle',
      geometryFunction: createBox(),
    });

    this.drawInteraction.on('drawend', (event: any) => {
      this.ngZone.run(() => {
        const extent3857 = event.feature.getGeometry().getExtent();
        const extent4326 = transformExtent(extent3857, 'EPSG:3857', 'EPSG:4326') as [number, number, number, number];
        // Clear the drawn feature (we redraw it in setBbox with our style)
        setTimeout(() => {
          this.bboxSource.clear();
          this.setBbox(extent4326);
          this.drawingMode = false;
          this.removeDrawInteraction();
        });
      });
    });

    this.map.addInteraction(this.drawInteraction);
  }

  private removeDrawInteraction(): void {
    if (this.drawInteraction) {
      this.map.removeInteraction(this.drawInteraction);
      this.drawInteraction = null;
    }
  }

  // --- Navigation ---

  goToCenter(): void {
    this.map.getView().animate({
      center: fromLonLat([this.lng, this.lat]),
      zoom: 12,
      duration: 500,
    });
  }

  onBasemapChange(): void {
    this.baseTileLayer.setSource(this.getBasemapSource(this.basemap));
  }

  // --- GeoJSON ---

  handleGeoJsonUpload(event: any): void {
    const file = event.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      this.ngZone.run(() => {
        try {
          const geojson = JSON.parse(e.target?.result as string);
          const format = new GeoJSON();
          const features = format.readFeatures(geojson, {
            dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857',
          }) as Feature<Geometry>[];

          if (features.length === 0) return;

          this.geoJsonFeatures = features;
          this.geoJsonFileName = file.name;
          this.currentFeatureIndex = 0;
          this.navigateToFeature(0);
        } catch (err) {
          console.error('Erro ao processar GeoJSON:', err);
        }
      });
    };
    reader.readAsText(file);
  }

  navigateToFeature(index: number): void {
    if (index < 0 || index >= this.geoJsonFeatures.length) return;

    this.currentFeatureIndex = index;
    const feature = this.geoJsonFeatures[index];

    this.geojsonSource.clear();
    this.geojsonSource.addFeature(feature.clone());

    const extent = feature.getGeometry()?.getExtent();
    if (extent) {
      this.map.getView().fit(extent, { padding: [30, 30, 30, 30], maxZoom: 14 });
      const bbox4326 = transformExtent(extent, 'EPSG:3857', 'EPSG:4326') as [number, number, number, number];
      this.setBbox(bbox4326);

      const format = new GeoJSON();
      const geometry = format.writeGeometryObject(feature.getGeometry()!, {
        dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857',
      });
      this.geometryChange.emit(geometry as GeoJSON.Geometry);
    }

    this.pointService.setActiveFeature(feature);
  }

  prevFeature(): void {
    if (this.currentFeatureIndex > 0) this.navigateToFeature(this.currentFeatureIndex - 1);
  }

  nextFeature(): void {
    if (this.currentFeatureIndex < this.geoJsonFeatures.length - 1) this.navigateToFeature(this.currentFeatureIndex + 1);
  }

  clearGeoJson(): void {
    this.geoJsonFeatures = [];
    this.geoJsonFileName = null;
    this.currentFeatureIndex = 0;
    this.geojsonSource.clear();
    this.bboxSource.clear();
    this.currentBbox = null;
    this.geometryChange.emit(null);
    this.pointService.setActiveFeature(null);
  }

  clearBbox(): void {
    this.bboxSource.clear();
    this.currentBbox = null;
    this.cdr.detectChanges();
  }

  private getBasemapSource(basemap: string): any {
    switch (basemap) {
      case 'dark':
        return new XYZ({ url: 'https://{a-d}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png' });
      case 'satellite':
        return new XYZ({ url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}' });
      case 'streets':
        return new OSM();
      default:
        return new XYZ({ url: 'https://{a-d}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png' });
    }
  }
}
