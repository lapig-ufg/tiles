# Screen State - Persistência de Estado de Telas

Sistema plugável para persistir e restaurar automaticamente o estado de filtros em telas Angular.

## Uso Rápido (Modo B - Manual / ngModel)

```typescript
import {ScreenStateConfig, ScreenStateBinder} from '../screen-state/interfaces/screen-state.interfaces';
import {ScreenStateService} from '../screen-state/services/screen-state.service';
import {bindState} from '../screen-state/helpers/manual-state.helper';

// 1. Definir config
const MY_SCREEN_CONFIG: ScreenStateConfig = {
  screenKey: 'my-screen',
  fields: {
    selectedFilter: {type: 'string', defaultValue: 'all'},
    page:           {type: 'number', defaultValue: 0},
  },
  debounceMs: 400,
  ttlMs: 7 * 24 * 60 * 60 * 1000, // 7 dias
  schemaVersion: 1,
};

// 2. No componente
export class MyComponent implements OnInit, OnDestroy {
  selectedFilter = 'all';
  page = 0;
  private binder!: ScreenStateBinder<any>;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private screenState: ScreenStateService,
  ) {}

  ngOnInit() {
    this.binder = bindState(MY_SCREEN_CONFIG, this.route, this.router, this.screenState, {
      selectedFilter: 'all',
      page: 0,
    });
    const restored = this.binder.restore();
    this.selectedFilter = restored.selectedFilter;
    this.page = restored.page;
  }

  onFilterChange() {
    this.binder.patchAndPersist({selectedFilter: this.selectedFilter, page: this.page});
  }

  clearFilters() {
    this.binder.reset();
    // reaplicar defaults do binder.state nos campos do componente
  }

  ngOnDestroy() {
    this.binder.destroy();
  }
}
```

## Uso com Reactive Forms (Modo A)

```typescript
import {bindFormState} from '../screen-state/helpers/form-state.helper';

// No ngOnInit:
const formBinder = bindFormState(this.myForm, config, route, router, service);
formBinder.restore();

// No ngOnDestroy:
formBinder.destroy();
```

## Componente de Limpeza

```html
<app-screen-state-clear
  screenKey="my-screen"
  (cleared)="clearFilters()"
></app-screen-state-clear>
```

## ScreenStateConfig

| Campo             | Tipo           | Default          | Descrição                            |
|-------------------|----------------|------------------|--------------------------------------|
| `screenKey`       | `string`       | (obrigatório)    | Chave única da tela                  |
| `group`           | `string`       | -                | Grupo para `clearGroup()`            |
| `fields`          | `Record`       | (obrigatório)    | Whitelist de campos com tipo         |
| `strategy`        | `SyncStrategy` | `'storage-only'` | `storage-only`, `url-only`, `hybrid` |
| `syncUrlOnRestore`| `boolean`      | `false`          | Atualizar URL após restore           |
| `debounceMs`      | `number`       | `400`            | Debounce para salvar                 |
| `ttlMs`           | `number`       | -                | Expiração do estado                  |
| `schemaVersion`   | `number`       | `1`              | Versão do schema                     |

## Tipos de Campo

`'string'` | `'number'` | `'boolean'` | `'date'` | `'string[]'` | `'number[]'`

## Versionamento

Ao alterar a estrutura dos campos de uma tela, incremente o `schemaVersion`. O estado antigo será automaticamente descartado.

## Storage

Dados são armazenados no `localStorage` com prefixo `ss_`. Exemplo: `ss_image-catalog`.
