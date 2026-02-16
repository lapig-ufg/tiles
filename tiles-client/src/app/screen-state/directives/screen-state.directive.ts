import {Directive, Input, OnInit, OnDestroy} from '@angular/core';
import {FormGroup} from '@angular/forms';
import {ActivatedRoute, Router} from '@angular/router';
import {ScreenStateConfig} from '../interfaces/screen-state.interfaces';
import {ScreenStateService} from '../services/screen-state.service';
import {bindFormState, FormStateBinder} from '../helpers/form-state.helper';

@Directive({selector: '[screenState]'})
export class ScreenStateDirective implements OnInit, OnDestroy {

  @Input('screenState') screenKey!: string;
  @Input() screenStateConfig!: ScreenStateConfig;
  @Input() screenStateForm?: FormGroup;

  private binder: FormStateBinder | null = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private service: ScreenStateService,
  ) {}

  ngOnInit(): void {
    if (this.screenStateForm && this.screenStateConfig) {
      this.binder = bindFormState(
        this.screenStateForm,
        this.screenStateConfig,
        this.route,
        this.router,
        this.service,
      );
      this.binder.restore();
    }
  }

  ngOnDestroy(): void {
    if (this.binder) {
      this.binder.destroy();
    }
  }
}
