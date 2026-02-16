import { NgModule } from '@angular/core';
import { RouterModule } from '@angular/router';
import {MapGridComponent} from "./map-grid.component";
import {HomeComponent} from "./home.component";
import {MapGridLandsatComponent} from "./map-grid-landsat/map-grid-landsat.component";

@NgModule({
    imports: [RouterModule.forChild([
        { path: '', data: {breadcrumb: 'Maps'}, component: HomeComponent },
        { path: 'sentinel', data: {breadcrumb: 'Maps'}, component: MapGridComponent },
        { path: 'landsat', data: {breadcrumb: 'Maps'}, component: MapGridLandsatComponent }
    ])],
    exports: [RouterModule]
})
export class MapGridRoutingModule { }
