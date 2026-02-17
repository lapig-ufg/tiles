import {NgModule} from '@angular/core';
import {RouterModule} from '@angular/router';
import {JobCreateComponent} from './pages/job-create/job-create.component';
import {JobDetailComponent} from './pages/job-detail/job-detail.component';
import {JobVisualizeComponent} from './pages/job-visualize/job-visualize.component';
import {JobExportComponent} from './pages/job-export/job-export.component';

@NgModule({
  imports: [RouterModule.forChild([
    {path: '', component: JobCreateComponent},
    {path: 'jobs/:id', component: JobDetailComponent},
    {path: 'jobs/:id/view', component: JobVisualizeComponent},
    {path: 'jobs/:id/export', component: JobExportComponent},
  ])],
  exports: [RouterModule]
})
export class EmbeddingMapsRoutingModule {}
