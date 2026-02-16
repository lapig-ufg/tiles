import {NgModule} from '@angular/core';
import {CommonModule} from '@angular/common';
import {ButtonModule} from 'primeng/button';
import {ScreenStateDirective} from './directives/screen-state.directive';
import {ScreenStateClearComponent} from './components/screen-state-clear.component';

@NgModule({
  imports: [CommonModule, ButtonModule],
  declarations: [ScreenStateDirective, ScreenStateClearComponent],
  exports: [ScreenStateDirective, ScreenStateClearComponent],
})
export class ScreenStateModule {}
