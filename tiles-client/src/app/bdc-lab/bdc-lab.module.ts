import { BdcComparisonComponent } from './bdc-comparison.component';
import { BdcLoadingComponent } from './bdc-loading.component';
import { BdcHelpComponent } from './bdc-help.component';
import { DialogModule } from 'primeng/dialog';
import { BdcEvidenceComponent } from './bdc-evidence.component';
import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { RouterModule } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { TooltipModule } from 'primeng/tooltip';
import { BdcLabComponent } from './bdc-lab.component';
import { BdcLabMapComponent } from './bdc-lab-map.component';
import { BdcLabApiService } from './bdc-lab-api.service';

@NgModule({
    imports: [BdcLoadingComponent, CommonModule, FormsModule, HttpClientModule, DialogModule, ButtonModule, TooltipModule,
        RouterModule.forChild([{ path: 'evidencias', component: BdcEvidenceComponent }, { path: '', component: BdcLabComponent }])],
    declarations: [BdcComparisonComponent, BdcHelpComponent, BdcEvidenceComponent, BdcLabComponent, BdcLabMapComponent],
    providers: [BdcLabApiService],
})
export class BdcLabModule {}
