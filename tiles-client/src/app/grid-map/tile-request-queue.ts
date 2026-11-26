import ImageTile from 'ol/ImageTile';
import Tile from 'ol/Tile';
import TileState from 'ol/TileState';

interface TileRequest {
    owner: object;
    tile: ImageTile;
    url: string;
    controller: AbortController;
    started: boolean;
    attempts: number;
    createdAt: number;
    objectUrl?: string;
    cleanupImage?: () => void;
}

export class TileRequestQueue {
    private readonly requests = new Set<TileRequest>();
    private pending: TileRequest[] = [];
    private running = 0;
    private blockedUntil = 0;
    private wakeup?: ReturnType<typeof setTimeout>;

    constructor(private readonly concurrency: number = 8) {}

    load(owner: object, tile: Tile, url: string): void {
        const request: TileRequest = {
            owner, tile: tile as ImageTile, url,
            controller: new AbortController(), started: false, attempts: 0, createdAt: Date.now(),
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
        this.drain();
    }

    private drain(): void {
        clearTimeout(this.wakeup);
        this.wakeup = undefined;
        if (this.pending.length && this.blockedUntil > Date.now()) {
            this.wakeup = setTimeout(() => this.drain(), this.blockedUntil - Date.now());
            return;
        }
        while (this.running < this.concurrency && this.pending.length) {
            const request = this.pending.shift()!;
            request.started = true;
            this.running++;
            void this.download(request);
        }
    }

    private async download(request: TileRequest): Promise<void> {
        let retry = false;
        request.attempts++;
        try {
            const response = await fetch(request.url, {
                signal: request.controller.signal,
                credentials: 'omit',
            });
            if (request.controller.signal.aborted || !response.ok) {
                await response.body?.cancel();
                if (request.controller.signal.aborted) return;
                retry = this.deferRetry(request, response);
                if (retry) return;
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
            if (retry && !request.controller.signal.aborted) {
                request.started = false;
                this.pending.push(request);
            } else {
                this.requests.delete(request);
            }
            this.running--;
            this.drain();
        }
    }

    private deferRetry(request: TileRequest, response: Response): boolean {
        if (response.status !== 429 && response.status !== 503) return false;
        const header = response.headers.get('Retry-After')?.trim();
        if (!header) return false;
        const now = Date.now();
        const milliseconds = /^\d+(?:\.\d+)?$/.test(header)
            ? Number(header) * 1000 : Date.parse(header) - now;
        if (!Number.isFinite(milliseconds) || milliseconds < 0) return false;
        const delay = Math.max(milliseconds, 500 * 2 ** (request.attempts - 1)) + Math.random() * 250;
        if (delay > 30000) return false;
        this.blockedUntil = Math.max(this.blockedUntil, now + delay);
        return request.attempts < 3 && now + delay - request.createdAt <= 60000;
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
