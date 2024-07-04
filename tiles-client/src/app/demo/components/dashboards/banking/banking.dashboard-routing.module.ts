import { NgModule } from '@angular/core';
import { RouterModule } from '@angular/router';
import { BankingDashboardComponent } from './banking.dashboard.component';


@NgModule({
	imports: [RouterModule.forChild([
		{ path: '', component: BankingDashboardComponent }
	])],
	exports: [RouterModule]
})
export class BankingDashboardRoutingModule { }
