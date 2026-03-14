import { NgModule } from '@angular/core';
import { RouterModule } from '@angular/router';
import { StacCopernicusComponent } from './stac-copernicus.component';

@NgModule({
  imports: [RouterModule.forChild([
    { path: '', component: StacCopernicusComponent }
  ])],
  exports: [RouterModule]
})
export class StacCopernicusRoutingModule {}
