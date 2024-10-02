import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MapGridRoutingModule } from './map-grid-routing.module';
import {MapGridComponent} from "./map-grid.component";
import {HomeComponent} from "./home.component";
import {CardModule} from "primeng/card";
import { PlotlyModule } from 'angular-plotly.js';
import * as PlotlyJS from 'plotly.js/dist/plotly.min.js';
import {SentinelTimeseriesComponent} from "./sentinel-timeseries/sentinel-timeseries.component";
import {LandsatTimeseriesComponent} from "./landsat-timeseries/landsat-timeseries.component";
import {ModisTimeseriesComponent} from "./modis-timeseries/modis-timeseries.component";
import {TabViewModule} from "primeng/tabview";
import {FormsModule} from "@angular/forms";
import {SelectButtonModule} from "primeng/selectbutton";
import {MapGridLandsatComponent} from "./map-grid-landsat/map-grid-landsat.component";
import {DropdownModule} from "primeng/dropdown";

PlotlyModule.plotlyjs = PlotlyJS;
@NgModule({
    imports: [
        CommonModule,
        MapGridRoutingModule,
        CardModule,
        PlotlyModule,
        ModisTimeseriesComponent,
        LandsatTimeseriesComponent,
        SentinelTimeseriesComponent,
        TabViewModule,
        FormsModule,
        SelectButtonModule,
        DropdownModule
    ],
    declarations: [
        HomeComponent,
        MapGridComponent,
        MapGridLandsatComponent
    ]
})
export class MapGridModule { }
