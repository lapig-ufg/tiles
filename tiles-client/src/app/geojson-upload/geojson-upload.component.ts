import {Component} from '@angular/core';
import {FeatureCollection, Feature, Point} from 'geojson';
import {PointService} from "../grid-map/services/point.service";
import {log} from "ol/console";

@Component({
    selector: 'app-geojson-upload',
    templateUrl: './geojson-upload.component.html',
    styleUrls: ['./geojson-upload.component.scss']
})
export class GeojsonUploadComponent {
    features: Feature<Point>[] = [];
    paginatedFeatures: Feature<Point>[] = [];
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
                const geojson: FeatureCollection<Point> = JSON.parse(reader.result as string);
                this.features = geojson.features.filter(f => f.geometry.type === 'Point');
                this.totalPages = Math.ceil(this.features.length / this.rows);
                if (this.features.length > 0) {
                    const firstFeature = this.features[0];
                     this.pointService.setPointInfo(firstFeature.properties)
                    const lat = firstFeature.geometry.coordinates[1];
                    const lon = firstFeature.geometry.coordinates[0];
                    this.pointService.setPoint({lat, lon});
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
         if (this.first) {
            const feature = this.features[this.first];
            const lat = feature.geometry.coordinates[1];
            const lon = feature.geometry.coordinates[0];
            this.pointService.setPointInfo(feature.properties)
            this.pointService.setPoint({lat, lon});
        }
        this.updatePaginatedFeatures();
    }
    goToPage() {
        if (this.inputPage < 1 || this.inputPage > this.totalPages) {
            this.inputPage = 1;  // Ou defina um comportamento de fallback
        }

        const newFirst = (this.inputPage - 1) * this.rows;
        this.onPageChange({ first: newFirst, rows: this.rows, page: this.inputPage - 1 });
    }
    updatePaginatedFeatures() {
        const start = this.first;
        const end = this.first + this.rows;
        this.paginatedFeatures = this.features.slice(start, end);
    }
}
