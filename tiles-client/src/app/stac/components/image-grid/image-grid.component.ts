import { Component, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SelectButtonModule } from 'primeng/selectbutton';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';
import { StacItem } from '../../models/stac.models';
import { SpectralIndex } from '../../models/spectral-indices';
import { StacImageCardComponent } from '../stac-image-card/stac-image-card.component';

@Component({
  selector: 'app-image-grid',
  standalone: true,
  imports: [CommonModule, FormsModule, SelectButtonModule, ButtonModule, TagModule, StacImageCardComponent],
  templateUrl: './image-grid.component.html',
  styleUrls: ['./image-grid.component.scss'],
})
export class ImageGridComponent implements OnChanges {
  @Input() items: StacItem[] = [];
  @Input() gridSize: 1 | 2 | 3 = 2;
  @Input() spectralIndex!: SpectralIndex;
  @Input() renderMode: 'cog' | 'footprint' = 'cog';
  @Input() aoiGeometry: Feature<Geometry> | null = null;
  @Input() basemap: string = 'dark';
  @Input() totalResults: number = 0;
  @Input() viewBbox: [number, number, number, number] | null = null;
  @Input() sortField: string = 'datetime';
  @Input() sortDirection: 'asc' | 'desc' = 'desc';

  @Output() sortChange = new EventEmitter<{ field: string; direction: 'asc' | 'desc' }>();
  @Output() basemapChange = new EventEmitter<string>();

  gridOptions = [
    { label: '1x1', value: 1 },
    { label: '2x2', value: 2 },
    { label: '3x3', value: 3 },
  ];

  basemapOptions = [
    { label: 'Dark', value: 'dark' },
    { label: 'Satellite', value: 'satellite' },
  ];

  sortOptions = [
    { label: 'Data (recente)', value: 'datetime-desc' },
    { label: 'Data (antigo)', value: 'datetime-asc' },
    { label: 'Nuvem (menor)', value: 'cloud-asc' },
    { label: 'Nuvem (maior)', value: 'cloud-desc' },
  ];

  selectedSort: string = 'datetime-desc';
  displayItems: StacItem[] = [];

  /**
   * Suffix appended to trackBy keys to force card re-creation
   * when spectralIndex or renderMode changes.
   */
  private trackSuffix: string = '';

  ngOnChanges(changes: SimpleChanges): void {
    // When spectralIndex or renderMode changes, all cards must be destroyed
    // and recreated because the OL Map + WebGLTile layer are initialized once
    // in ngAfterViewInit. Angular's *ngFor won't recreate them unless trackBy
    // returns different keys.
    if (changes['spectralIndex'] || changes['renderMode']) {
      this.trackSuffix = (this.spectralIndex?.id || '') + '-' + (this.renderMode || '');
      // Force re-creation: clear then re-populate after a tick
      this.displayItems = [];
      setTimeout(() => this.updateDisplayItems());
      return;
    }

    if (changes['items'] || changes['gridSize']) {
      this.updateDisplayItems();
    }
  }

  onSortChange(): void {
    const [field, direction] = this.selectedSort.split('-');
    this.sortChange.emit({ field, direction: direction as 'asc' | 'desc' });
    this.sortItems();
  }

  onBasemapChange(): void {
    this.basemapChange.emit(this.basemap);
  }

  /**
   * trackBy includes spectralIndex ID so Angular destroys and recreates
   * card components when the index changes.
   */
  trackById = (index: number, item: StacItem): string => {
    return item.id + '::' + this.trackSuffix;
  }

  private updateDisplayItems(): void {
    this.displayItems = [...this.items];
    this.sortItems();
  }

  private sortItems(): void {
    const [field, direction] = this.selectedSort.split('-');
    this.displayItems.sort((a, b) => {
      let valA: any, valB: any;
      if (field === 'datetime') {
        valA = a.properties.datetime || '';
        valB = b.properties.datetime || '';
      } else if (field === 'cloud') {
        valA = a.properties['eo:cloud_cover'] ?? 999;
        valB = b.properties['eo:cloud_cover'] ?? 999;
      }
      const cmp = valA < valB ? -1 : valA > valB ? 1 : 0;
      return direction === 'asc' ? cmp : -cmp;
    });
  }
}
