import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { StacBdcRoutingModule } from './stac-bdc-routing.module';
import { StacBdcComponent } from './stac-bdc.component';
import { ScreenStateModule } from '../screen-state/screen-state.module';

// PrimeNG
import { CardModule } from 'primeng/card';
import { ButtonModule } from 'primeng/button';
import { SelectButtonModule } from 'primeng/selectbutton';
import { CalendarModule } from 'primeng/calendar';
import { InputNumberModule } from 'primeng/inputnumber';
import { SliderModule } from 'primeng/slider';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TagModule } from 'primeng/tag';
import { TooltipModule } from 'primeng/tooltip';
import { MessagesModule } from 'primeng/messages';
import { RippleModule } from 'primeng/ripple';
import { BadgeModule } from 'primeng/badge';
import { RadioButtonModule } from 'primeng/radiobutton';

// Standalone STAC components
import { AoiMapComponent } from '../stac/components/aoi-map/aoi-map.component';
import { ImageGridComponent } from '../stac/components/image-grid/image-grid.component';

@NgModule({
  imports: [
    CommonModule,
    FormsModule,
    HttpClientModule,
    StacBdcRoutingModule,
    ScreenStateModule,
    CardModule,
    ButtonModule,
    SelectButtonModule,
    CalendarModule,
    InputNumberModule,
    SliderModule,
    ProgressSpinnerModule,
    TagModule,
    TooltipModule,
    MessagesModule,
    RippleModule,
    BadgeModule,
    RadioButtonModule,
    AoiMapComponent,
    ImageGridComponent,
  ],
  declarations: [
    StacBdcComponent,
  ]
})
export class StacBdcModule {}
