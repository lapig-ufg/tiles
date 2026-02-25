import {Component} from '@angular/core';
import {FeatureCollection, Feature as GeoJsonFeature, Geometry as GeoJsonGeometry} from 'geojson';
import {PointService} from '../grid-map/services/point.service';
import {computeRepresentativePoint, geoJsonFeatureToOl} from '../shared/utils/geometry.utils';
import {Feature} from 'ol';
import {Geometry} from 'ol/geom';

@Component({
    selector: 'app-geojson-upload',
    templateUrl: './geojson-upload.component.html',
    styleUrls: ['./geojson-upload.component.scss']
})
export class GeojsonUploadComponent {
    geoJsonFeatures: GeoJsonFeature<GeoJsonGeometry>[] = [];
    first = 0;
    rows = 1;
    totalPages: number = 0;
    inputPage: number = 1;

    constructor(public pointService: PointService) {
    }

    onFileSelect(event: any) {
        const file = event.files[0];
        const reader = new FileReader();
        reader.onload = () => {
            try {
                const geojson: FeatureCollection = JSON.parse(reader.result as string);
                this.geoJsonFeatures = geojson.features.filter(f => f.geometry !== null);
                this.totalPages = Math.ceil(this.geoJsonFeatures.length / this.rows);
                if (this.geoJsonFeatures.length > 0) {
                    this.selectFeature(this.geoJsonFeatures[0]);
                }
                this.updatePaginatedFeatures();
            } catch (error) {
                console.error('Invalid GeoJSON file', error);
            }
        };
        reader.readAsText(file);
    }

    onPageChange(event: { first?: number, rows?: number, page?: number }) {
        this.first = event.first ?? this.first;
        this.rows = event.rows ?? this.rows;
        if (this.first !== undefined && this.geoJsonFeatures[this.first]) {
            this.selectFeature(this.geoJsonFeatures[this.first]);
        }
        this.updatePaginatedFeatures();
    }

    goToPage() {
        if (this.inputPage < 1 || this.inputPage > this.totalPages) {
            this.inputPage = 1;
        }
        const newFirst = (this.inputPage - 1) * this.rows;
        this.onPageChange({first: newFirst, rows: this.rows, page: this.inputPage - 1});
    }

    updatePaginatedFeatures() {
        // keep for paginator binding
    }

    private selectFeature(geoJsonFeature: GeoJsonFeature<GeoJsonGeometry>): void {
        const olFeature: Feature<Geometry> = geoJsonFeatureToOl(geoJsonFeature);
        const repPoint = computeRepresentativePoint(olFeature);
        if (repPoint) {
            this.pointService.setPoint({lat: repPoint.lat, lon: repPoint.lon});
        }
        this.pointService.setPointInfo(geoJsonFeature.properties);
        this.pointService.setActiveFeature(olFeature);
    }
}
