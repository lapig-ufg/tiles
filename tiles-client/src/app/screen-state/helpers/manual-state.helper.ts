import {ActivatedRoute, Router} from '@angular/router';
import {ScreenStateConfig, ScreenStateBinder, FieldType} from '../interfaces/screen-state.interfaces';
import {ScreenStateService} from '../services/screen-state.service';

/**
 * Coerce um valor string (vindo da URL) para o tipo esperado.
 * Retorna `undefined` se a conversão falhar.
 */
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
      const nums = arr.map(Number).filter((n: number) => !isNaN(n));
      return nums.length > 0 ? nums : undefined;
    }

    default:
      return value;
  }
}

/**
 * Cria um binder de estado manual para componentes com ngModel/template-driven.
 */
export function bindState<T extends Record<string, any>>(
  config: ScreenStateConfig,
  route: ActivatedRoute,
  router: Router,
  service: ScreenStateService,
  defaults: T
): ScreenStateBinder<T> {

  let debounceTimer: any = null;
  let _skipUrlSync = false;

  const state = {...defaults} as T;

  function restore(): T {
    // 1. Começa com defaults
    Object.assign(state, defaults);

    // 2. Merge do storage
    const stored = service.load(config);
    if (stored) {
      for (const key of Object.keys(config.fields)) {
        if (stored[key] !== undefined && stored[key] !== null) {
          const fieldDef = config.fields[key];
          if (fieldDef.type === 'date' && typeof stored[key] === 'string') {
            const d = new Date(stored[key]);
            if (!isNaN(d.getTime())) {
              (state as any)[key] = d;
            }
          } else {
            (state as any)[key] = stored[key];
          }
        }
      }
    }

    // 3. Merge da URL (maior prioridade)
    const strategy = config.strategy ?? 'storage-only';
    if (strategy === 'url-only' || strategy === 'hybrid') {
      const params = route.snapshot.queryParams;
      for (const key of Object.keys(config.fields)) {
        if (params[key] !== undefined && params[key] !== '') {
          const coerced = coerceValue(params[key], config.fields[key].type);
          if (coerced !== undefined) {
            (state as any)[key] = coerced;
          }
        }
      }
    }

    // 4. Sync URL se necessário
    if (config.syncUrlOnRestore && stored && !_skipUrlSync) {
      syncUrl();
    }

    return state;
  }

  function syncUrl(): void {
    const strategy = config.strategy ?? 'storage-only';
    if (strategy === 'storage-only') return;

    _skipUrlSync = true;
    const queryParams: Record<string, any> = {};
    for (const key of Object.keys(config.fields)) {
      const val = (state as any)[key];
      if (val !== undefined && val !== null && val !== (defaults as any)[key]) {
        if (val instanceof Date) {
          queryParams[key] = val.toISOString();
        } else if (Array.isArray(val)) {
          queryParams[key] = val.join(',');
        } else {
          queryParams[key] = val;
        }
      }
    }

    router.navigate([], {
      relativeTo: route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true,
    }).then(() => {
      _skipUrlSync = false;
    });
  }

  function persistNow(): void {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    service.save(config, state);
  }

  function schedulePersist(): void {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => {
      service.save(config, state);
      debounceTimer = null;
    }, config.debounceMs ?? 400);
  }

  function reset(): void {
    Object.assign(state, defaults);
    service.clear(config.screenKey);
  }

  function patchAndPersist(partial: Partial<T>): void {
    Object.assign(state, partial);
    schedulePersist();
  }

  function destroy(): void {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
  }

  return {
    state,
    restore,
    persistNow,
    schedulePersist,
    reset,
    patchAndPersist,
    destroy,
  };
}
