import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { PanelModule } from 'primeng/panel';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { computeGeometryInfo, GeometryInfo } from '../../../shared/utils/geometry.utils';

@Component({
    selector: 'app-sample-info',
    standalone: true,
    imports: [CommonModule, PanelModule, TableModule, TagModule],
    templateUrl: './sample-info.component.html',
})
export class SampleInfoComponent implements OnChanges {
    @Input() feature: Feature<Geometry> | null = null;

    geometryInfo: GeometryInfo | null = null;
    properties: { key: string; value: any }[] = [];

    ngOnChanges(changes: SimpleChanges): void {
        if (changes['feature']) {
            this.update();
        }
    }

    private update(): void {
        if (!this.feature) {
            this.geometryInfo = null;
            this.properties = [];
            return;
        }

        this.geometryInfo = computeGeometryInfo(this.feature);

        const props = this.feature.getProperties();
        this.properties = Object.keys(props)
            .filter(k => k !== 'geometry')
            .map(k => ({ key: k, value: props[k] }));
    }

    getTypeSeverity(type: string): string {
        const map: Record<string, string> = {
            'Point': 'info',
            'MultiPoint': 'info',
            'LineString': 'warning',
            'MultiLineString': 'warning',
            'Polygon': 'success',
            'MultiPolygon': 'success',
        };
        return map[type] || 'info';
    }
}
