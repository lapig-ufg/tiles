import {Component, EventEmitter, Input, Output} from '@angular/core';
import {RoiConfig, RoiType} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-roi-selector',
  templateUrl: './roi-selector.component.html',
  styleUrls: ['./roi-selector.component.scss']
})
export class RoiSelectorComponent {
  @Input() roiMode: RoiType = 'bbox';
  @Input() west = -49.5;
  @Input() south = -16.8;
  @Input() east = -49.0;
  @Input() north = -16.3;
  @Input() geojsonText = '';

  @Output() roiChange = new EventEmitter<RoiConfig>();
  @Output() roiModeChange = new EventEmitter<RoiType>();

  roiModeOptions = [
    {label: 'BBOX', value: 'bbox' as RoiType},
    {label: 'GeoJSON', value: 'polygon' as RoiType},
  ];

  onModeChange(mode: RoiType): void {
    this.roiMode = mode;
    this.roiModeChange.emit(mode);
    this.emitRoi();
  }

  onCoordChange(): void {
    this.emitRoi();
  }

  onGeoJsonUpload(event: any): void {
    const file = event.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      this.geojsonText = e.target?.result as string;
      this.emitRoi();
    };
    reader.readAsText(file);
  }

  onGeoJsonTextChange(): void {
    this.emitRoi();
  }

  private emitRoi(): void {
    if (this.roiMode === 'bbox') {
      this.roiChange.emit({
        roi_type: 'bbox',
        bbox: [this.west, this.south, this.east, this.north],
      });
    } else {
      try {
        const geojson = JSON.parse(this.geojsonText);
        const type: RoiType = geojson.type === 'FeatureCollection' ? 'feature_collection' : 'polygon';
        this.roiChange.emit({
          roi_type: type,
          geojson,
        });
      } catch {
        // JSON invalido, nao emite
      }
    }
  }

  getRoi(): RoiConfig {
    if (this.roiMode === 'bbox') {
      return {roi_type: 'bbox', bbox: [this.west, this.south, this.east, this.north]};
    }
    try {
      const geojson = JSON.parse(this.geojsonText);
      const type: RoiType = geojson.type === 'FeatureCollection' ? 'feature_collection' : 'polygon';
      return {roi_type: type, geojson};
    } catch {
      return {roi_type: 'bbox', bbox: [this.west, this.south, this.east, this.north]};
    }
  }
}
