import {Injectable} from '@angular/core';
import {ScreenStorageAdapter, ScreenStateSnapshot} from '../interfaces/screen-state.interfaces';

const PREFIX = 'ss_';

@Injectable({providedIn: 'root'})
export class LocalStorageAdapter implements ScreenStorageAdapter {

  private get available(): boolean {
    return typeof window !== 'undefined' && !!window.localStorage;
  }

  get(key: string): ScreenStateSnapshot | null {
    if (!this.available) return null;
    try {
      const raw = localStorage.getItem(PREFIX + key);
      if (!raw) return null;
      return JSON.parse(raw) as ScreenStateSnapshot;
    } catch {
      return null;
    }
  }

  set(key: string, snapshot: ScreenStateSnapshot): void {
    if (!this.available) return;
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(snapshot));
    } catch (e) {
      console.warn('[ScreenState] localStorage quota exceeded or write failed', e);
    }
  }

  remove(key: string): void {
    if (!this.available) return;
    localStorage.removeItem(PREFIX + key);
  }

  clearByPrefix(prefix: string): void {
    if (!this.available) return;
    const fullPrefix = PREFIX + prefix;
    const keysToRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(fullPrefix)) {
        keysToRemove.push(k);
      }
    }
    keysToRemove.forEach(k => localStorage.removeItem(k));
  }

  clearAll(): void {
    if (!this.available) return;
    const keysToRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) {
        keysToRemove.push(k);
      }
    }
    keysToRemove.forEach(k => localStorage.removeItem(k));
  }
}
