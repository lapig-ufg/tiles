import {Component, Input, Output, EventEmitter} from '@angular/core';
import {ScreenStateService} from '../services/screen-state.service';

@Component({
  selector: 'app-screen-state-clear',
  template: `
    <p-button icon="pi pi-filter-slash"
              [label]="label"
              styleClass="p-button-outlined p-button-sm"
              (onClick)="onClear()">
    </p-button>
  `,
})
export class ScreenStateClearComponent {

  @Input() screenKey?: string;
  @Input() group?: string;
  @Input() clearAll = false;
  @Input() label = 'Limpar filtros';
  @Output() cleared = new EventEmitter<void>();

  constructor(private service: ScreenStateService) {}

  onClear(): void {
    if (this.clearAll) {
      this.service.clearAll();
    } else if (this.group) {
      this.service.clearGroup(this.group);
    } else if (this.screenKey) {
      this.service.clear(this.screenKey);
    }
    this.cleared.emit();
  }
}
