import { NgModule } from '@angular/core';
import { RouterModule } from '@angular/router';
import { StacBdcComponent } from './stac-bdc.component';

@NgModule({
  imports: [RouterModule.forChild([
    { path: '', component: StacBdcComponent }
  ])],
  exports: [RouterModule]
})
export class StacBdcRoutingModule {}
