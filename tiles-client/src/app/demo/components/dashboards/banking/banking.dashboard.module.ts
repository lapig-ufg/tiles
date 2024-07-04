import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';
import { TableModule } from 'primeng/table';
import { InputNumberModule } from 'primeng/inputnumber';
import { ChartModule } from 'primeng/chart';
import { RippleModule } from 'primeng/ripple';
import { TagModule } from 'primeng/tag';
import { TooltipModule } from 'primeng/tooltip';
import { BankingDashboardComponent } from './banking.dashboard.component';
import { BankingDashboardRoutingModule } from './banking.dashboard-routing.module';

@NgModule({
    imports: [
        CommonModule,
        ButtonModule,
        RippleModule,
        TagModule,
        TooltipModule,
        TableModule,
        InputNumberModule,
        ChartModule,
        BankingDashboardRoutingModule
    ],
    declarations: [BankingDashboardComponent]
})
export class BankingDashboardModule { }
