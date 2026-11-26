import {CommonModule} from '@angular/common';
import {NO_ERRORS_SCHEMA} from '@angular/core';
import {ComponentFixture, fakeAsync, flushMicrotasks, TestBed, tick} from '@angular/core/testing';
import {FormsModule} from '@angular/forms';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {SelectButtonModule} from 'primeng/selectbutton';
import {DropdownModule} from 'primeng/dropdown';
import {ActivatedRoute, Router} from '@angular/router';
import {fromLonLat} from 'ol/proj';
import TileState from 'ol/TileState';
import {MapGridComponent} from './map-grid.component';
import {PointService} from './services/point.service';
import {ScreenStateService} from '../screen-state/services/screen-state.service';

describe('MapGridComponent: ciclo de vida', () => {
    let fixture: ComponentFixture<MapGridComponent>;
    let component: MapGridComponent;
    let intersection: IntersectionObserverCallback | undefined;
    let observer: jasmine.SpyObj<IntersectionObserver>;
    let pointService: PointService;
    let publishPoint: jasmine.Spy;
    let fetchTile: jasmine.Spy<typeof fetch>;

    beforeEach(async () => {
        intersection = undefined;
        observer = jasmine.createSpyObj('IntersectionObserver', ['observe', 'unobserve', 'disconnect']);
        spyOn(window, 'IntersectionObserver').and.callFake(function (callback) {
            intersection = callback;
            return observer;
        });
        spyOnProperty(HTMLImageElement.prototype, 'src', 'set').and.stub();
        fetchTile = spyOn(window, 'fetch').and.returnValue(new Promise(() => {}));
        pointService = new PointService();
        publishPoint = spyOn(pointService, 'setPoint').and.stub();
        await TestBed.configureTestingModule({
            declarations: [MapGridComponent],
            imports: [CommonModule, FormsModule, NoopAnimationsModule, SelectButtonModule, DropdownModule],
            providers: [
                {provide: PointService, useValue: pointService},
                {provide: ActivatedRoute, useValue: {snapshot: {queryParams: {}}}},
                {provide: Router, useValue: {navigate: () => Promise.resolve(true)}},
                {provide: ScreenStateService, useValue: {
                    load: () => ({selectedSentinelYear: 2020, selectedSentinelPeriod: 'MONTH'}),
                    save: () => {}, clear: () => {},
                }},
            ],
            schemas: [NO_ERRORS_SCHEMA],
        }).compileComponents();
        fixture = TestBed.createComponent(MapGridComponent);
        component = fixture.componentInstance;
    });

    function elements(): HTMLElement[] {
        return Array.from(fixture.nativeElement.querySelectorAll('.map-container'));
    }

    function visible(targets: HTMLElement[], isIntersecting = true): void {
        intersection!(targets.map(target => ({
            target, isIntersecting, boundingClientRect: target.getBoundingClientRect(),
            intersectionRatio: isIntersecting ? 1 : 0, intersectionRect: target.getBoundingClientRect(),
            rootBounds: null, time: performance.now(),
        })), observer);
    }

    function maps(): any[] {
        return (component as any).mapsInstances;
    }

    it('não cria mapas fora da tela nem mapas sem elemento no template', fakeAsync(() => {
        fixture.detectChanges();
        tick(32);
        expect(maps().length).toBe(0);
        expect(observer.observe).toHaveBeenCalledTimes(12);
        fixture.destroy();
    }));

    it('restaura o ano persistido como opção numérica do seletor', fakeAsync(() => {
        spyOn(TestBed.inject(ScreenStateService), 'load').and.returnValue({
            selectedSentinelYear: '2020', selectedSentinelPeriod: 'WET',
        });
        fixture.detectChanges();
        expect(component.selectedSentinelYear).toBe(2020);
        expect(elements().map(element => element.id)).toEqual(['sentinel-map-WET-2020']);
        fixture.destroy();
    }));

    it('distribui a criação dos mapas visíveis entre quadros de renderização', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (!intersection) { fixture.destroy(); return; }
        visible(elements().slice(0, 3));
        tick(16);
        expect(maps().length).toBe(1);
        tick(32);
        expect(maps().length).toBe(3);
        fixture.destroy();
    }));

    it('troca rapidamente de filtro sem criar mapas da seleção obsoleta', fakeAsync(() => {
        fixture.detectChanges();
        component.selectedSentinelPeriod = 'DRY';
        component.updateSentinelMaps();
        component.selectedSentinelPeriod = 'WET';
        component.updateSentinelMaps();
        fixture.detectChanges();
        tick(32);
        if (intersection) { visible(elements()); tick(32); }
        expect(maps().map(inst => inst.id)).toEqual(['sentinel-map-WET-2020']);
        fixture.destroy();
    }));

    it('ignora eventos antigos de visibilidade quando a visualização reutiliza os mesmos identificadores', fakeAsync(() => {
        showFirstMap();
        const previousIntersection = intersection!;
        const previousElements = elements();
        component.selectedSentinelVisParam = 'tvi-red';
        component.updateSentinelMaps();
        fixture.detectChanges();
        previousIntersection(previousElements.map(target => ({
            target, isIntersecting: true, boundingClientRect: target.getBoundingClientRect(),
            intersectionRatio: 1, intersectionRect: target.getBoundingClientRect(),
            rootBounds: null, time: performance.now(),
        })), observer);
        tick(32);
        expect(maps().length).toBe(0);
        visible(elements().slice(0, 1));
        tick(16);
        startTile();
        expect(String(fetchTile.calls.mostRecent().args[0])).toContain('visparam=tvi-red');
        fixture.destroy();
    }));

    it('mantém centro e zoom atualizados nos mapas que entram na tela depois', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (!intersection) { fixture.destroy(); return; }
        visible(elements().slice(0, 1));
        tick(16);
        maps()[0].map.getView().setCenter(fromLonLat([-48, -15]));
        maps()[0].map.getView().setZoom(10);
        visible(elements().slice(1, 2));
        tick(16);
        expect(maps()[1].map.getView().getCenter()).toEqual(fromLonLat([-48, -15]));
        expect(maps()[1].map.getView().getZoom()).toBe(10);
        fixture.destroy();
    }));

    it('destrói o observador e invalida a criação pendente', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        if (intersection) { visible(elements()); }
        fixture.destroy();
        tick(100);
        expect(maps().length).toBe(0);
        expect(observer.disconnect).toHaveBeenCalled();
    }));

    it('descarta o mapa oculto e permite recriá-lo ao voltar à tela', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (!intersection) { fixture.destroy(); return; }
        visible(elements().slice(0, 1));
        tick(16);
        const previous = maps()[0].map;
        visible(elements().slice(0, 1), false);
        expect(maps().length).toBe(0);
        expect(previous.getTarget()).toBeFalsy();
        visible(elements().slice(0, 1));
        tick(16);
        expect(maps().length).toBe(1);
        expect(maps()[0].map).not.toBe(previous);
        fixture.destroy();
    }));

    it('mantém carregamento por visibilidade sem IntersectionObserver', fakeAsync(() => {
        (window as any).IntersectionObserver = undefined;
        let visibleId = 'sentinel-map-01-2020';
        spyOn(HTMLElement.prototype, 'getBoundingClientRect').and.callFake(function (this: HTMLElement) {
            return new DOMRect(0, this.id === visibleId ? 0 : 10000, 400, 300);
        });
        fixture.detectChanges();
        tick(32);
        expect(maps().map(inst => inst.id)).toEqual(['sentinel-map-01-2020']);
        visibleId = 'sentinel-map-02-2020';
        window.dispatchEvent(new Event('scroll'));
        tick(32);
        expect(maps().map(inst => inst.id)).toEqual(['sentinel-map-02-2020']);
        fixture.destroy();
        window.dispatchEvent(new Event('scroll'));
        tick(32);
        expect(maps().length).toBe(0);
    }));

    it('aceita coordenadas zero sem republicar recursivamente o mesmo ponto', fakeAsync(() => {
        fixture.detectChanges();
        publishPoint.and.callThrough();
        pointService.setPoint({lat: 0, lon: 0});
        expect(component.lat).toBe(0);
        expect(component.lon).toBe(0);
        expect(publishPoint.calls.count()).toBe(2);
        fixture.destroy();
    }));

    it('recentra no ponto selecionado novamente depois de mover a visualização', fakeAsync(() => {
        showFirstMap();
        publishPoint.and.callThrough();
        pointService.setPoint({lat: -15, lon: -48});
        maps()[0].map.getView().setCenter(fromLonLat([-47, -14]));
        pointService.setPoint({lat: -15, lon: -48});
        expect(maps()[0].map.getView().getCenter()).toEqual(fromLonLat([-48, -15]));
        tick(0);
        fixture.destroy();
    }));
    function startTile(index = 1): any {
        const map = maps()[0].map;
        const source = map.getLayers().item(0).getSource();
        const tile = source.getTile(13, index, 1, 1, map.getView().getProjection());
        tile.load();
        return tile;
    }

    function showFirstMap(): void {
        fixture.detectChanges();
        tick(16);
        visible(elements().slice(0, 1));
        tick(16);
    }

    it('limita downloads concorrentes e descarta a fila ao trocar de filtro', fakeAsync(() => {
        fetchTile.and.callFake((_url, options) => new Promise((_resolve, reject) => {
            options!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
        }));
        showFirstMap();
        for (let index = 1; index <= 20; index++) startTile(index);
        expect(fetchTile.calls.count()).toBe(8);
        const signals = fetchTile.calls.allArgs().map(args => args[1]!.signal!);
        component.selectedSentinelPeriod = 'DRY';
        component.updateSentinelMaps();
        flushMicrotasks();
        expect(signals.every(signal => signal.aborted)).toBeTrue();
        expect(fetchTile.calls.count()).toBe(8);
        fixture.destroy();
    }));

    for (const action of ['ocultar', 'destruir', 'mudar o ponto']) {
        it(`aborta o download ao ${action}`, fakeAsync(() => {
            showFirstMap();
            const tile = startTile();
            expect(fetchTile.calls.count()).toBe(1);
            const signal = fetchTile.calls.mostRecent()?.args[1]?.signal;
            if (action === 'ocultar') visible(elements().slice(0, 1), false);
            if (action === 'destruir') fixture.destroy();
            if (action === 'mudar o ponto') {
                publishPoint.and.callThrough();
                pointService.setPoint({lat: -15, lon: -48});
                tick(0);
            }
            expect(signal?.aborted).toBeTrue();
            expect(tile.getState()).toBe(TileState.EMPTY);
            if (action !== 'destruir') fixture.destroy();
        }));
    }

    it('revoga a URL temporária quando a imagem termina de carregar', fakeAsync(() => {
        const response = new Response(null, {status: 200});
        spyOn(response, 'blob').and.returnValue(Promise.resolve(new Blob(['image'])));
        fetchTile.and.returnValue(Promise.resolve(response));
        const createUrl = spyOn(URL, 'createObjectURL').and.returnValue('blob:teste');
        const revokeUrl = spyOn(URL, 'revokeObjectURL');
        let downloadedImage: HTMLImageElement | undefined;
        (Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')!.set as jasmine.Spy)
            .and.callFake(function (this: HTMLImageElement, src: string) {
                if (src === 'blob:teste') downloadedImage = this;
            });
        showFirstMap();
        const tile = startTile();
        flushMicrotasks();
        expect(createUrl).toHaveBeenCalled();
        downloadedImage?.dispatchEvent(new Event('load'));
        flushMicrotasks();
        expect(tile.getState()).toBe(TileState.LOADED);
        expect(revokeUrl).toHaveBeenCalledOnceWith('blob:teste');
        fixture.destroy();
    }));

});
