import {NgModule} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {HttpClientModule} from '@angular/common/http';
import {ImageCatalogRoutingModule} from './image-catalog-routing.module';
import {ImageCatalogComponent} from './image-catalog.component';
import {ScreenStateModule} from '../screen-state/screen-state.module';

import {CardModule} from 'primeng/card';
import {TableModule} from 'primeng/table';
import {SelectButtonModule} from 'primeng/selectbutton';
import {CalendarModule} from 'primeng/calendar';
import {SliderModule} from 'primeng/slider';
import {ButtonModule} from 'primeng/button';
import {CheckboxModule} from 'primeng/checkbox';
import {PaginatorModule} from 'primeng/paginator';
import {ProgressSpinnerModule} from 'primeng/progressspinner';
import {TagModule} from 'primeng/tag';
import {MessagesModule} from 'primeng/messages';
import {TooltipModule} from 'primeng/tooltip';
import {InputNumberModule} from 'primeng/inputnumber';
import {RippleModule} from 'primeng/ripple';
import {BadgeModule} from 'primeng/badge';

import {PlotlyModule} from 'angular-plotly.js';
import * as PlotlyJS from 'plotly.js/dist/plotly.min.js';
import {SentinelTimeseriesComponent} from '../grid-map/sentinel-timeseries/sentinel-timeseries.component';
import {LandsatTimeseriesComponent} from '../grid-map/landsat-timeseries/landsat-timeseries.component';

PlotlyModule.plotlyjs = PlotlyJS;

@NgModule({
    imports: [
        CommonModule,
        FormsModule,
        HttpClientModule,
        ImageCatalogRoutingModule,
        ScreenStateModule,
        CardModule,
        TableModule,
        SelectButtonModule,
        CalendarModule,
        SliderModule,
        ButtonModule,
        CheckboxModule,
        PaginatorModule,
        ProgressSpinnerModule,
        TagModule,
        MessagesModule,
        TooltipModule,
        InputNumberModule,
        RippleModule,
        BadgeModule,
        PlotlyModule,
        SentinelTimeseriesComponent,
        LandsatTimeseriesComponent,
    ],
    declarations: [
        ImageCatalogComponent
    ]
})
export class ImageCatalogModule {}
