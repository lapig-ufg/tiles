import {fakeAsync, flushMicrotasks, tick} from '@angular/core/testing';
import ImageTile from 'ol/ImageTile';
import TileState from 'ol/TileState';
import {TileRequestQueue} from './tile-request-queue';

describe('TileRequestQueue', () => {
    let queue: TileRequestQueue;
    let fetchTile: jasmine.Spy<typeof fetch>;
    let images: HTMLImageElement[];
    let revokeUrl: jasmine.Spy;

    beforeEach(() => {
        queue = new TileRequestQueue(2);
        images = [];
        fetchTile = spyOn(window, 'fetch').and.returnValue(new Promise(() => {}));
        spyOn(URL, 'createObjectURL').and.returnValue('blob:tile');
        revokeUrl = spyOn(URL, 'revokeObjectURL');
        spyOnProperty(HTMLImageElement.prototype, 'src', 'set').and.callFake(function (this: HTMLImageElement) {
            images.push(this);
        });
    });

    function tile(owner: object, index: number): ImageTile {
        const result = new ImageTile([13, index, 1], TileState.IDLE, `/tile/${index}`, null,
            (imageTile, url) => queue.load(owner, imageTile, url));
        result.load();
        return result;
    }

    function response(status = 200): Response {
        const result = new Response(null, {status});
        spyOn(result, 'blob').and.returnValue(Promise.resolve(new Blob(['tile'])));
        return result;
    }

    it('compartilha o limite entre mapas e só admite o próximo após a liberação', fakeAsync(() => {
        let resolveFirst!: (value: Response) => void;
        fetchTile.and.callFake(() => new Promise(resolve => {
            if (fetchTile.calls.count() === 1) resolveFirst = resolve;
        }));
        const firstOwner = {};
        const secondOwner = {};
        const first = tile(firstOwner, 1);
        tile(secondOwner, 2);
        tile(secondOwner, 3);
        expect(fetchTile.calls.count()).toBe(2);
        resolveFirst(response());
        flushMicrotasks();
        expect(fetchTile.calls.count()).toBe(2);
        images[0].dispatchEvent(new Event('load'));
        flushMicrotasks();
        expect(first.getState()).toBe(TileState.LOADED);
        expect(fetchTile.calls.count()).toBe(3);
        expect(fetchTile.calls.mostRecent().args[0]).toBe('/tile/3');
        expect(fetchTile.calls.first().args[1]!.credentials).toBe('omit');
        queue.cancel(secondOwner);
    }));

    it('ignora a resposta que chega depois do cancelamento', fakeAsync(() => {
        let resolveFetch!: (value: Response) => void;
        fetchTile.and.returnValue(new Promise(resolve => resolveFetch = resolve));
        const owner = {};
        const pending = tile(owner, 1);
        queue.cancel(owner);
        resolveFetch(response());
        flushMicrotasks();
        expect(pending.getState()).toBe(TileState.EMPTY);
        expect(URL.createObjectURL).not.toHaveBeenCalled();
        expect(images.length).toBe(0);
    }));

    for (const event of ['error', 'cancel']) {
        it(`revoga a URL e remove os listeners após ${event === 'error' ? 'falha de decodificação' : 'cancelamento'}`, fakeAsync(() => {
            fetchTile.and.returnValue(Promise.resolve(response()));
            const owner = {};
            const pending = tile(owner, 1);
            flushMicrotasks();
            const image = images[0];
            const removeListener = spyOn(image, 'removeEventListener').and.callThrough();
            if (event === 'error') image.dispatchEvent(new Event('error'));
            else queue.cancel(owner);
            flushMicrotasks();
            expect(pending.getState()).toBe(event === 'error' ? TileState.ERROR : TileState.EMPTY);
            expect(revokeUrl).toHaveBeenCalledOnceWith('blob:tile');
            expect(removeListener).toHaveBeenCalledWith('load', jasmine.any(Function));
            expect(removeListener).toHaveBeenCalledWith('error', jasmine.any(Function));
            image.dispatchEvent(new Event('load'));
            expect(pending.getState()).toBe(event === 'error' ? TileState.ERROR : TileState.EMPTY);
        }));
    }

    it('cancela o corpo de uma resposta HTTP inválida antes de liberar a vaga', fakeAsync(() => {
        const stream = new ReadableStream();
        const failure = new Response(stream, {status: 503});
        const cancelBody = spyOn(stream, 'cancel').and.returnValue(Promise.resolve());
        fetchTile.and.returnValues(Promise.resolve(failure), new Promise(() => {}), new Promise(() => {}));
        const owner = {};
        const failed = tile(owner, 1);
        tile(owner, 2);
        tile(owner, 3);
        flushMicrotasks();
        expect(failed.getState()).toBe(TileState.ERROR);
        expect(cancelBody).toHaveBeenCalled();
        expect(fetchTile.calls.count()).toBe(3);
        expect(URL.createObjectURL).not.toHaveBeenCalled();
        queue.cancel(owner);
    }));

    it('preserva a fila de outro mapa ao cancelar downloads ativos e pendentes', fakeAsync(() => {
        fetchTile.and.callFake((_url, options) => new Promise((_resolve, reject) => {
            options!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
        }));
        const oldOwner = {};
        const currentOwner = {};
        tile(oldOwner, 1);
        tile(oldOwner, 2);
        const queued = tile(oldOwner, 3);
        tile(currentOwner, 4);
        queue.cancel(oldOwner);
        flushMicrotasks();
        expect(queued.getState()).toBe(TileState.EMPTY);
        expect(fetchTile.calls.allArgs().map(args => args[0])).toEqual(['/tile/1', '/tile/2', '/tile/4']);
        expect(fetchTile.calls.mostRecent().args[1]!.signal!.aborted).toBeFalse();
        queue.cancel(currentOwner);
        flushMicrotasks();
    }));
    it('respeita Retry-After compartilhado e limita as tentativas', fakeAsync(() => {
        spyOn(Math, 'random').and.returnValue(0);
        fetchTile.and.callFake(() => Promise.resolve(new Response(null, {
            status: 503, headers: {'Retry-After': '2'},
        })));
        const owner = {};
        const pending = tile(owner, 1);
        flushMicrotasks();
        expect(pending.getState()).toBe(TileState.LOADING);
        tick(1999);
        expect(fetchTile.calls.count()).toBe(1);
        tick(1);
        expect(fetchTile.calls.count()).toBe(2);
        tick(2000);
        expect(fetchTile.calls.count()).toBe(3);
        expect(pending.getState()).toBe(TileState.ERROR);
        tick(10000);
        expect(fetchTile.calls.count()).toBe(3);
    }));

    it('cancela uma retentativa pendente sem fazer outra chamada', fakeAsync(() => {
        fetchTile.and.returnValue(Promise.resolve(new Response(null, {
            status: 429, headers: {'Retry-After': '2'},
        })));
        const owner = {};
        const pending = tile(owner, 1);
        flushMicrotasks();
        queue.cancel(owner);
        tick(10000);
        expect(fetchTile.calls.count()).toBe(1);
        expect(pending.getState()).toBe(TileState.EMPTY);
    }));

    for (const header of ['invalid', '120']) {
        it(`não antecipa uma pausa inválida ou excessiva: ${header}`, fakeAsync(() => {
            fetchTile.and.returnValue(Promise.resolve(new Response(null, {
                status: 503, headers: {'Retry-After': header},
            })));
            const pending = tile({}, 1);
            flushMicrotasks();
            expect(pending.getState()).toBe(TileState.ERROR);
            expect(fetchTile.calls.count()).toBe(1);
        }));
    }

    it('carrega 38 anos com seis tiles inéditos por ano após uma limitação', fakeAsync(() => {
        queue = new TileRequestQueue(8);
        spyOn(Math, 'random').and.returnValue(0);
        const attempts = new Map<string, number>();
        let active = 0;
        let peak = 0;
        fetchTile.and.callFake(url => {
            const key = String(url);
            const count = (attempts.get(key) || 0) + 1;
            attempts.set(key, count);
            active++;
            peak = Math.max(peak, active);
            return new Promise(resolve => setTimeout(() => {
                active--;
                resolve(key === '/tile/1' && count === 1
                    ? new Response(null, {status: 503, headers: {'Retry-After': '2'}})
                    : response());
            }, 20));
        });
        const pending: ImageTile[] = [];
        for (let year = 0; year < 38; year++) {
            const owner = {};
            for (let offset = 0; offset < 6; offset++) pending.push(tile(owner, year * 6 + offset));
        }
        let decoded = 0;
        for (let elapsed = 0; elapsed < 10000; elapsed += 20) {
            tick(20);
            while (decoded < images.length) images[decoded++].dispatchEvent(new Event('load'));
            flushMicrotasks();
        }
        expect(pending.every(item => item.getState() === TileState.LOADED)).toBeTrue();
        expect(attempts.size).toBe(228);
        expect(fetchTile.calls.count()).toBe(229);
        expect(peak).toBeLessThanOrEqual(8);
        expect(revokeUrl.calls.count()).toBe(228);
    }));

    it('respeita uma data HTTP e pausa também as solicitações de outro mapa', fakeAsync(() => {
        spyOn(Math, 'random').and.returnValue(0);
        const deadline = Math.ceil(Date.now() / 1000) * 1000 + 2000;
        fetchTile.and.returnValue(Promise.resolve(new Response(null, {
            status: 429, headers: {'Retry-After': new Date(deadline).toUTCString()},
        })));
        const owner = {};
        tile(owner, 1);
        flushMicrotasks();
        tile(owner, 2);
        tick(deadline - Date.now() - 1);
        expect(fetchTile.calls.count()).toBe(1);
        queue.cancel(owner);
        tick(10000);
        expect(fetchTile.calls.count()).toBe(1);
    }));

});
