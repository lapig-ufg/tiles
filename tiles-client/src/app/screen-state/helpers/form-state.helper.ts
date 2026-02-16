import {FormGroup} from '@angular/forms';
import {ActivatedRoute, Router} from '@angular/router';
import {Subscription} from 'rxjs';
import {debounceTime} from 'rxjs/operators';
import {ScreenStateConfig, FieldType} from '../interfaces/screen-state.interfaces';
import {ScreenStateService} from '../services/screen-state.service';

function coerceValue(value: any, type: FieldType): any {
  if (value === null || value === undefined) return undefined;
  switch (type) {
    case 'string':
      return String(value);
    case 'number': {
      const n = Number(value);
      return isNaN(n) ? undefined : n;
    }
    case 'boolean':
      return value === 'true' || value === '1' || value === true;
    case 'date': {
      const d = new Date(value);
      return isNaN(d.getTime()) ? undefined : d;
    }
    case 'string[]':
      return Array.isArray(value) ? value : String(value).split(',');
    case 'number[]': {
      const arr = Array.isArray(value) ? value : String(value).split(',');
      return arr.map(Number).filter((n: number) => !isNaN(n));
    }
    default:
      return value;
  }
}

function deepEqual(a: any, b: any): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export interface FormStateBinder {
  restore(): void;
  destroy(): void;
  reset(): void;
}

/**
 * Cria um binder de estado para FormGroup (Reactive Forms).
 */
export function bindFormState(
  form: FormGroup,
  config: ScreenStateConfig,
  route: ActivatedRoute,
  router: Router,
  service: ScreenStateService
): FormStateBinder {

  let sub: Subscription | null = null;
  let lastValue: string = '';

  function restore(): void {
    const patch: Record<string, any> = {};

    // Defaults do config
    for (const [key, def] of Object.entries(config.fields)) {
      if (def.defaultValue !== undefined) {
        patch[key] = def.defaultValue;
      }
    }

    // Merge do storage
    const stored = service.load(config);
    if (stored) {
      for (const key of Object.keys(config.fields)) {
        if (stored[key] !== undefined && stored[key] !== null) {
          const fieldDef = config.fields[key];
          if (fieldDef.type === 'date' && typeof stored[key] === 'string') {
            const d = new Date(stored[key]);
            if (!isNaN(d.getTime())) {
              patch[key] = d;
            }
          } else {
            patch[key] = stored[key];
          }
        }
      }
    }

    // Merge da URL
    const strategy = config.strategy ?? 'storage-only';
    if (strategy === 'url-only' || strategy === 'hybrid') {
      const params = route.snapshot.queryParams;
      for (const key of Object.keys(config.fields)) {
        if (params[key] !== undefined && params[key] !== '') {
          const coerced = coerceValue(params[key], config.fields[key].type);
          if (coerced !== undefined) {
            patch[key] = coerced;
          }
        }
      }
    }

    form.patchValue(patch, {emitEvent: false});
    lastValue = JSON.stringify(form.value);

    // Escuta mudanças com debounce
    sub = form.valueChanges
      .pipe(debounceTime(config.debounceMs ?? 400))
      .subscribe(value => {
        const serialized = JSON.stringify(value);
        if (serialized !== lastValue) {
          lastValue = serialized;
          service.save(config, value);
        }
      });
  }

  function destroy(): void {
    if (sub) {
      sub.unsubscribe();
      sub = null;
    }
  }

  function reset(): void {
    const defaults: Record<string, any> = {};
    for (const [key, def] of Object.entries(config.fields)) {
      defaults[key] = def.defaultValue ?? null;
    }
    form.patchValue(defaults);
    service.clear(config.screenKey);
  }

  return {restore, destroy, reset};
}
