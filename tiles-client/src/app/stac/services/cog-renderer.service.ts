import { Injectable, OnDestroy } from '@angular/core';
import WebGLTileLayer from 'ol/layer/WebGLTile';
import GeoTIFF from 'ol/source/GeoTIFF';

const MAX_WEBGL_CONTEXTS = 14;

interface ManagedLayer {
  layer: WebGLTileLayer;
  source: GeoTIFF;
  createdAt: number;
}

@Injectable({ providedIn: 'root' })
export class CogRendererService implements OnDestroy {

  private managedLayers: ManagedLayer[] = [];

  /**
   * Creates a COG layer for a pre-rendered visual asset (8-bit RGB like TCI).
   * Uses max:255 for uint8 normalization.
   */
  createVisualLayer(cogUrl: string, style?: any): { layer: WebGLTileLayer; source: GeoTIFF } {
    this.enforceLimit();

    const source = new GeoTIFF({
      sources: [{ url: cogUrl, max: 255 }],
    });

    const layer = new WebGLTileLayer({
      source,
      style: style || undefined,
    });

    this.managedLayers.push({ layer, source, createdAt: Date.now() });
    return { layer, source };
  }

  /**
   * Creates a multi-band COG layer from individual band URLs.
   *
   * Replicates the pattern from earth-search-explorer.html:
   * - Each source has max:10000 (Sentinel-2 surface reflectance scale)
   * - normalize: true (default) — OL handles normalization internally
   * - Style expressions receive values in a range compatible with max
   *
   * Band ordering in style expressions:
   *   sources[0] → ['band', 1]
   *   sources[1] → ['band', 2]
   *   etc.
   */
  createMultiBandLayer(bandUrls: string[], style: any, max: number = 10000): { layer: WebGLTileLayer; source: GeoTIFF } {
    this.enforceLimit();

    const sources = bandUrls.map(url => ({ url, max }));

    const source = new GeoTIFF({ sources });

    const layer = new WebGLTileLayer({
      source,
      style,
    });

    this.managedLayers.push({ layer, source, createdAt: Date.now() });
    return { layer, source };
  }

  destroyLayer(layer: WebGLTileLayer): void {
    const index = this.managedLayers.findIndex(m => m.layer === layer);
    if (index >= 0) {
      this.disposeManaged(this.managedLayers[index]);
      this.managedLayers.splice(index, 1);
    }
  }

  destroyAll(): void {
    for (const managed of this.managedLayers) {
      this.disposeManaged(managed);
    }
    this.managedLayers = [];
  }

  get activeCount(): number {
    return this.managedLayers.length;
  }

  ngOnDestroy(): void {
    this.destroyAll();
  }

  private enforceLimit(): void {
    while (this.managedLayers.length >= MAX_WEBGL_CONTEXTS) {
      const oldest = this.managedLayers.shift();
      if (oldest) this.disposeManaged(oldest);
    }
  }

  private disposeManaged(managed: ManagedLayer): void {
    try {
      managed.layer.dispose();
    } catch (e) {
      // WebGL context may already be lost
    }
  }
}
