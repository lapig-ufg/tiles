import ImageTile from 'ol/ImageTile';
import Tile from 'ol/Tile';
import TileState from 'ol/TileState';

interface TileRequest {
    owner: object;
    tile: ImageTile;
    url: string;
    controller: AbortController;
    started: boolean;
    objectUrl?: string;
    cleanupImage?: () => void;
}

export class TileRequestQueue {
    private readonly requests = new Set<TileRequest>();
    private pending: TileRequest[] = [];
    private running = 0;

    constructor(private readonly concurrency: number = 8) {}

    load(owner: object, tile: Tile, url: string): void {
        const request: TileRequest = {
            owner, tile: tile as ImageTile, url,
            controller: new AbortController(), started: false,
        };
        this.requests.add(request);
        this.pending.push(request);
        this.drain();
    }

    cancel(owner: object): void {
        for (const request of this.requests) {
            if (request.owner !== owner || request.controller.signal.aborted) continue;
            request.controller.abort();
            request.cleanupImage?.();
            this.revokeObjectUrl(request);
            this.finishTile(request, TileState.EMPTY);
            if (!request.started) this.requests.delete(request);
        }
        this.pending = this.pending.filter(request => !request.controller.signal.aborted);
    }

    private drain(): void {
        while (this.running < this.concurrency && this.pending.length) {
            const request = this.pending.shift()!;
            request.started = true;
            this.running++;
            void this.download(request);
        }
    }

    private async download(request: TileRequest): Promise<void> {
        try {
            const response = await fetch(request.url, {
                signal: request.controller.signal,
                credentials: 'omit',
            });
            if (request.controller.signal.aborted || !response.ok) {
                await response.body?.cancel();
                if (request.controller.signal.aborted) return;
                throw new Error(`Tile HTTP ${response.status}`);
            }
            const blob = await response.blob();
            if (request.controller.signal.aborted) return;
            request.objectUrl = URL.createObjectURL(blob);
            await this.decodeImage(request);
        } catch {
            if (!request.controller.signal.aborted) this.finishTile(request, TileState.ERROR);
        } finally {
            request.cleanupImage?.();
            this.revokeObjectUrl(request);
            this.requests.delete(request);
            this.running--;
            this.drain();
        }
    }

    private decodeImage(request: TileRequest): Promise<void> {
        return new Promise((resolve, reject) => {
            const image = new Image();
            let loaded = false;
            const onLoad = () => {
                if (!request.controller.signal.aborted) {
                    loaded = true;
                    request.tile.setImage(image);
                }
                resolve();
            };
            const onError = () => reject(new Error('Tile image decode failed'));
            request.cleanupImage = () => {
                image.removeEventListener('load', onLoad);
                image.removeEventListener('error', onError);
                if (!loaded) image.removeAttribute('src');
                resolve();
            };
            image.addEventListener('load', onLoad);
            image.addEventListener('error', onError);
            image.src = request.objectUrl!;
        });
    }

    private finishTile(request: TileRequest, state: number): void {
        // A troca da imagem libera os listeners de carregamento mantidos pelo OpenLayers.
        request.tile.setImage(document.createElement('canvas'));
        request.tile.setState(state);
    }

    private revokeObjectUrl(request: TileRequest): void {
        if (request.objectUrl) {
            URL.revokeObjectURL(request.objectUrl);
            request.objectUrl = undefined;
        }
    }
}
