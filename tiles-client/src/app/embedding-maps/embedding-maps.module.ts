import {NgModule} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule, ReactiveFormsModule} from '@angular/forms';
import {HttpClientModule} from '@angular/common/http';
import {EmbeddingMapsRoutingModule} from './embedding-maps-routing.module';
import {ScreenStateModule} from '../screen-state/screen-state.module';

// PrimeNG
import {CardModule} from 'primeng/card';
import {TableModule} from 'primeng/table';
import {ButtonModule} from 'primeng/button';
import {DropdownModule} from 'primeng/dropdown';
import {InputTextModule} from 'primeng/inputtext';
import {InputTextareaModule} from 'primeng/inputtextarea';
import {InputNumberModule} from 'primeng/inputnumber';
import {CheckboxModule} from 'primeng/checkbox';
import {RadioButtonModule} from 'primeng/radiobutton';
import {SelectButtonModule} from 'primeng/selectbutton';
import {TagModule} from 'primeng/tag';
import {ProgressBarModule} from 'primeng/progressbar';
import {ProgressSpinnerModule} from 'primeng/progressspinner';
import {PanelModule} from 'primeng/panel';
import {DividerModule} from 'primeng/divider';
import {TooltipModule} from 'primeng/tooltip';
import {FileUploadModule} from 'primeng/fileupload';
import {DialogModule} from 'primeng/dialog';
import {TimelineModule} from 'primeng/timeline';
import {ChipModule} from 'primeng/chip';

// Pages
import {JobCreateComponent} from './pages/job-create/job-create.component';
import {JobDetailComponent} from './pages/job-detail/job-detail.component';
import {JobVisualizeComponent} from './pages/job-visualize/job-visualize.component';
import {JobExportComponent} from './pages/job-export/job-export.component';

// Components
import {ParamsFormComponent} from './components/params-form/params-form.component';
import {RoiSelectorComponent} from './components/roi-selector/roi-selector.component';
import {ProductSelectorComponent} from './components/product-selector/product-selector.component';
import {StatsPanelComponent} from './components/stats-panel/stats-panel.component';
import {ArtifactsListComponent} from './components/artifacts-list/artifacts-list.component';

@NgModule({
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    HttpClientModule,
    EmbeddingMapsRoutingModule,
    ScreenStateModule,
    // PrimeNG
    CardModule,
    TableModule,
    ButtonModule,
    DropdownModule,
    InputTextModule,
    InputTextareaModule,
    InputNumberModule,
    CheckboxModule,
    RadioButtonModule,
    SelectButtonModule,
    TagModule,
    ProgressBarModule,
    ProgressSpinnerModule,
    PanelModule,
    DividerModule,
    TooltipModule,
    FileUploadModule,
    DialogModule,
    TimelineModule,
    ChipModule,
  ],
  declarations: [
    // Pages
    JobCreateComponent,
    JobDetailComponent,
    JobVisualizeComponent,
    JobExportComponent,
    // Components
    ParamsFormComponent,
    RoiSelectorComponent,
    ProductSelectorComponent,
    StatsPanelComponent,
    ArtifactsListComponent,
  ]
})
export class EmbeddingMapsModule {}
