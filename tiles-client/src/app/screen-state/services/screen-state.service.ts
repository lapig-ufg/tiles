import {Injectable} from '@angular/core';
import {LocalStorageAdapter} from '../adapters/local-storage.adapter';
import {ScreenStateConfig, ScreenStateSnapshot} from '../interfaces/screen-state.interfaces';

@Injectable({providedIn: 'root'})
export class ScreenStateService {

  constructor(private adapter: LocalStorageAdapter) {}

  getStorageKey(screenKey: string): string {
    return screenKey;
  }

  save(config: ScreenStateConfig, data: Record<string, any>): void {
    const filtered: Record<string, any> = {};
    for (const key of Object.keys(config.fields)) {
      if (data[key] !== undefined) {
        const value = data[key];
        filtered[key] = value instanceof Date ? value.toISOString() : value;
      }
    }

    const snapshot: ScreenStateSnapshot = {
      data: filtered,
      savedAt: Date.now(),
      schemaVersion: config.schemaVersion ?? 1,
    };

    this.adapter.set(this.getStorageKey(config.screenKey), snapshot);
  }

  load(config: ScreenStateConfig): Record<string, any> | null {
    const snapshot = this.adapter.get(this.getStorageKey(config.screenKey));
    if (!snapshot) return null;

    const expectedVersion = config.schemaVersion ?? 1;
    if (snapshot.schemaVersion !== expectedVersion) {
      this.adapter.remove(this.getStorageKey(config.screenKey));
      return null;
    }

    if (config.ttlMs) {
      const elapsed = Date.now() - snapshot.savedAt;
      if (elapsed > config.ttlMs) {
        this.adapter.remove(this.getStorageKey(config.screenKey));
        return null;
      }
    }

    return snapshot.data;
  }

  clear(screenKey: string): void {
    this.adapter.remove(this.getStorageKey(screenKey));
  }

  clearGroup(groupPrefix: string): void {
    this.adapter.clearByPrefix(groupPrefix);
  }

  clearAll(): void {
    this.adapter.clearAll();
  }
}
