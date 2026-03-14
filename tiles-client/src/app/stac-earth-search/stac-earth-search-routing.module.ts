import { NgModule } from '@angular/core';
import { RouterModule } from '@angular/router';
import { StacEarthSearchComponent } from './stac-earth-search.component';

@NgModule({
  imports: [RouterModule.forChild([
    { path: '', component: StacEarthSearchComponent }
  ])],
  exports: [RouterModule]
})
export class StacEarthSearchRoutingModule {}
