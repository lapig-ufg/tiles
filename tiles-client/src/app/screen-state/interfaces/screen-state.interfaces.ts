/** Estratégia de sincronização com URL */
export type SyncStrategy = 'storage-only' | 'url-only' | 'hybrid';

/** Tipos suportados para coerção de URL params */
export type FieldType = 'string' | 'number' | 'boolean' | 'date' | 'string[]' | 'number[]';

/** Definição de campo persistido */
export interface FieldDef {
  type: FieldType;
  defaultValue?: any;
}

/** Configuração por tela */
export interface ScreenStateConfig {
  screenKey: string;
  group?: string;
  fields: Record<string, FieldDef>;
  strategy?: SyncStrategy;
  syncUrlOnRestore?: boolean;
  debounceMs?: number;
  ttlMs?: number;
  schemaVersion?: number;
  clearOnLogout?: boolean;
}

/** Snapshot salvo no storage */
export interface ScreenStateSnapshot {
  data: Record<string, any>;
  savedAt: number;
  schemaVersion: number;
  appVersion?: string;
}

/** Adaptador de storage (interface para abstração) */
export interface ScreenStorageAdapter {
  get(key: string): ScreenStateSnapshot | null;
  set(key: string, snapshot: ScreenStateSnapshot): void;
  remove(key: string): void;
  clearByPrefix(prefix: string): void;
  clearAll(): void;
}

/** Objeto de controle retornado por bindState() */
export interface ScreenStateBinder<T> {
  state: T;
  restore(): T;
  persistNow(): void;
  schedulePersist(): void;
  reset(): void;
  patchAndPersist(partial: Partial<T>): void;
  destroy(): void;
}
