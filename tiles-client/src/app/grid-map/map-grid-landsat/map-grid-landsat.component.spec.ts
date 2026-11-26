import {CommonModule} from '@angular/common';
import {NO_ERRORS_SCHEMA} from '@angular/core';
import {ComponentFixture, fakeAsync, TestBed, tick} from '@angular/core/testing';
import {FormsModule} from '@angular/forms';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {DropdownModule} from 'primeng/dropdown';
import {SelectButtonModule} from 'primeng/selectbutton';
import {fromLonLat} from 'ol/proj';
import {PointService} from '../services/point.service';
import {MapGridLandsatComponent} from './map-grid-landsat.component';

describe('MapGridLandsatComponent: carregamento progressivo', () => {
    let fixture: ComponentFixture<MapGridLandsatComponent>;
    let component: MapGridLandsatComponent;
    let observer: jasmine.SpyObj<IntersectionObserver>;
    let intersection: IntersectionObserverCallback | undefined;
    let fetchTile: jasmine.Spy<typeof fetch>;
    let pointService: PointService;
    let publishPoint: jasmine.Spy;

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
            declarations: [MapGridLandsatComponent],
            imports: [CommonModule, FormsModule, NoopAnimationsModule, SelectButtonModule, DropdownModule],
            providers: [{provide: PointService, useValue: pointService}],
            schemas: [NO_ERRORS_SCHEMA],
        }).compileComponents();
        fixture = TestBed.createComponent(MapGridLandsatComponent);
        component = fixture.componentInstance;
        component.selectedLandsatYear = 2020;
        component.selectedLandsatPeriod = 'MONTH';
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

    function startTile(): any {
        const map = maps()[0].map;
        const tile = map.getLayers().item(0).getSource().getTile(10, 12, 34, 1, map.getView().getProjection());
        tile.load();
        return tile;
    }

    it('MONTH com todos os anos cria apenas os mapas visíveis, um por quadro', fakeAsync(() => {
        component.selectedLandsatYear = 'Todos';
        fixture.detectChanges();
        tick(16);
        expect(component.landsatMaps.length).toBeGreaterThan(400);
        expect(component.landsatMaps.every(map => Number.isFinite(map.year))).toBeTrue();
        expect(maps().length).toBe(0);
        expect(fetchTile).not.toHaveBeenCalled();
        expect(intersection).toBeDefined();
        if (intersection) {
            visible(elements().slice(0, 3));
            tick(16);
            expect(maps().length).toBe(1);
            tick(32);
            expect(maps().length).toBe(3);
        }
        fixture.destroy();
    }));

    it('troca filtros rapidamente e mantém a URL absoluta e os parâmetros finais', fakeAsync(() => {
        fixture.detectChanges();
        component.selectedLandsatPeriod = 'WET';
        component.updateLandsatMaps();
        component.selectedLandsatPeriod = 'DRY';
        component.selectedLandsatYear = 2019;
        component.selectedLandsatVisParam = 'landsat-tvi-true';
        component.updateLandsatMaps();
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (intersection) {
            visible(elements());
            tick(16);
            expect(maps().map(inst => inst.id)).toEqual(['landsat-map-DRY-2019']);
            startTile();
            const url = new URL(String(fetchTile.calls.mostRecent().args[0]));
            expect(url.protocol).toBe('https:');
            expect(url.hostname).toMatch(/^tm[1-5]\.lapig\.iesa\.ufg\.br$/);
            expect(url.pathname).toBe('/api/layers/landsat/12/34/10');
            expect(url.searchParams.get('year')).toBe('2019');
            expect(url.searchParams.get('period')).toBe('DRY');
            expect(url.searchParams.get('visparam')).toBe('landsat-tvi-true');
        }
        fixture.destroy();
    }));

    it('aborta o download quando o mapa sai da tela e preserva centro e zoom ao recriar', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (intersection) {
            visible(elements().slice(0, 1));
            tick(16);
            startTile();
            const signal = fetchTile.calls.mostRecent().args[1]!.signal!;
            maps()[0].map.getView().setCenter(fromLonLat([-48, -15]));
            maps()[0].map.getView().setZoom(11);
            visible(elements().slice(0, 1), false);
            expect(signal.aborted).toBeTrue();
            expect(maps().length).toBe(0);
            visible(elements().slice(0, 1));
            tick(16);
            expect(maps()[0].map.getView().getCenter()).toEqual(fromLonLat([-48, -15]));
            expect(maps()[0].map.getView().getZoom()).toBe(11);
        }
        fixture.destroy();
    }));

    it('destrói mapas e downloads ativos e invalida a criação pendente', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        let signal: AbortSignal | null | undefined;
        if (intersection) {
            visible(elements());
            tick(16);
            startTile();
            signal = fetchTile.calls.mostRecent().args[1]!.signal;
        }
        fixture.destroy();
        tick(100);
        expect(maps().length).toBe(0);
        expect(signal?.aborted).toBeTrue();
        expect(observer.disconnect).toHaveBeenCalled();
    }));

    it('cancela downloads ativos ao trocar a visualização e rejeita eventos do observador anterior', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        visible(elements().slice(0, 1));
        tick(16);
        startTile();
        const signal = fetchTile.calls.mostRecent().args[1]!.signal!;
        const previousIntersection = intersection!;
        const previousTarget = elements()[0];
        component.selectedLandsatVisParam = 'landsat-tvi-agri';
        component.updateLandsatMaps();
        fixture.detectChanges();
        expect(signal.aborted).toBeTrue();
        previousIntersection([{
            target: previousTarget, isIntersecting: true, boundingClientRect: previousTarget.getBoundingClientRect(),
            intersectionRatio: 1, intersectionRect: previousTarget.getBoundingClientRect(), rootBounds: null,
            time: performance.now(),
        }], observer);
        tick(32);
        expect(maps().length).toBe(0);
        visible(elements().slice(0, 1));
        tick(16);
        startTile();
        expect(String(fetchTile.calls.mostRecent().args[0])).toContain('visparam=landsat-tvi-agri');
        fixture.destroy();
    }));

    it('preserva os meses de setembro a agosto no ano PRODES', fakeAsync(() => {
        component.selectedYearType = 'PRODES-YEAR';
        fixture.detectChanges();
        expect(component.landsatMaps.map(map => map.id)).toEqual([
            'landsat-map-09-2019', 'landsat-map-10-2019', 'landsat-map-11-2019', 'landsat-map-12-2019',
            'landsat-map-01-2020', 'landsat-map-02-2020', 'landsat-map-03-2020', 'landsat-map-04-2020',
            'landsat-map-05-2020', 'landsat-map-06-2020', 'landsat-map-07-2020', 'landsat-map-08-2020',
        ]);
        fixture.destroy();
    }));

    it('aplica um ponto de coordenadas zero sem recursão e cancela a imagem anterior', fakeAsync(() => {
        fixture.detectChanges();
        tick(16);
        expect(intersection).toBeDefined();
        if (intersection) {
            visible(elements().slice(0, 1));
            tick(16);
            startTile();
            const signal = fetchTile.calls.mostRecent().args[1]!.signal!;
            publishPoint.and.callThrough();
            pointService.setPoint({lat: 0, lon: 0});
            tick(0);
            expect(signal.aborted).toBeTrue();
            expect(maps()[0].map.getView().getCenter()).toEqual(fromLonLat([0, 0]));
            expect(publishPoint.calls.count()).toBe(2);
        }
        fixture.destroy();
    }));
});
