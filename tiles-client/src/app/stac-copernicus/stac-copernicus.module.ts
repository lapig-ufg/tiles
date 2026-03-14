import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { StacCopernicusRoutingModule } from './stac-copernicus-routing.module';
import { StacCopernicusComponent } from './stac-copernicus.component';
import { ScreenStateModule } from '../screen-state/screen-state.module';

// PrimeNG
import { CardModule } from 'primeng/card';
import { ButtonModule } from 'primeng/button';
import { SelectButtonModule } from 'primeng/selectbutton';
import { CalendarModule } from 'primeng/calendar';
import { InputNumberModule } from 'primeng/inputnumber';
import { InputTextModule } from 'primeng/inputtext';
import { SliderModule } from 'primeng/slider';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TagModule } from 'primeng/tag';
import { TooltipModule } from 'primeng/tooltip';
import { MessagesModule } from 'primeng/messages';
import { RippleModule } from 'primeng/ripple';
import { BadgeModule } from 'primeng/badge';
import { CheckboxModule } from 'primeng/checkbox';
import { MultiSelectModule } from 'primeng/multiselect';
import { TableModule } from 'primeng/table';
import { AccordionModule } from 'primeng/accordion';
import { DropdownModule } from 'primeng/dropdown';

// Standalone components
import { AoiMapComponent } from '../stac/components/aoi-map/aoi-map.component';
import { ImageGridComponent } from '../stac/components/image-grid/image-grid.component';
import { Cql2FilterComponent } from './components/cql2-filter/cql2-filter.component';
import { QueryableFilterComponent } from './components/queryable-filter/queryable-filter.component';

@NgModule({
  imports: [
    CommonModule,
    FormsModule,
    HttpClientModule,
    StacCopernicusRoutingModule,
    ScreenStateModule,
    CardModule,
    ButtonModule,
    SelectButtonModule,
    CalendarModule,
    InputNumberModule,
    InputTextModule,
    SliderModule,
    ProgressSpinnerModule,
    TagModule,
    TooltipModule,
    MessagesModule,
    RippleModule,
    BadgeModule,
    CheckboxModule,
    MultiSelectModule,
    TableModule,
    AccordionModule,
    DropdownModule,
    // Standalone
    AoiMapComponent,
    ImageGridComponent,
    Cql2FilterComponent,
    QueryableFilterComponent,
  ],
  declarations: [
    StacCopernicusComponent,
  ]
})
export class StacCopernicusModule {}
