import { of, Subject, throwError } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { BdcLabComponent } from './bdc-lab.component';
import { BdcLabApiService } from './bdc-lab-api.service';
import { BdcCollection, BdcItem, PointQualityResult, ItemDetail, SearchResult } from './bdc-lab.models';

describe('BdcLabComponent', () => {
    let api: jasmine.SpyObj<BdcLabApiService>;
    let component: BdcLabComponent;
    const collection = { id: 'landsat-2', title: 'Landsat', family: 'Landsat', providers: [], extent: {}, formats: [], bands: [], summaries: {}, description: '', license: '', has_cog: true, public: true } as BdcCollection;
    const item = { id: 'scene', collection: 'landsat-2', properties: {}, assets: {}, bbox: [-50,-17,-49,-16] } as BdcItem;
    beforeEach(() => {
        api = jasmine.createSpyObj('BdcLabApiService', ['collections','search','detail','bestImages']);
        api.collections.and.returnValue(of({ collections: [collection], paused: false }));
        component = new BdcLabComponent(api); component.ngOnInit();
    });
    afterEach(() => component.ngOnDestroy());
    it('mantém estado e catálogo independentes', () => { expect(component.online).toBeTrue(); expect(component.collection?.id).toBe('landsat-2'); expect(component.selection).toBeNull(); });
    it('cancela pesquisa anterior antes de aceitar a próxima resposta', () => {
        const first = new Subject<SearchResult>(); const second = new Subject<SearchResult>();
        api.search.and.returnValues(first, second); component.search(); component.search();
        first.next({ items: [item], page: 1, next_page: null, matched: 1, returned: 1 });
        expect(component.items).toEqual([]);
        second.next({ items: [], page: 1, next_page: null, matched: 0, returned: 0 });
        expect(component.loading).toBeFalse(); expect(component.items).toEqual([]);
    });
    it('descarta detalhe tardio após troca de coleção', () => {
        const request = new Subject<ItemDetail>(); api.detail.and.returnValue(request); component.select(item); component.chooseCollection();
        request.next({ item, collection, profiles: [] }); expect(component.detail).toBeNull(); expect(component.detailLoading).toBeFalse();
    });
    it('preserva a área da consulta ao paginar mesmo após mover a área selecionada', () => {
        api.search.and.returnValue(of({ items: [], page: 1, next_page: 2, matched: 20, returned: 0 }));
        component.search(); const original = api.search.calls.mostRecent().args[0].bbox;
        component.bbox = [-55,-4,-54,-3]; component.search(2);
        expect(api.search.calls.mostRecent().args[0].bbox).toEqual(original);
    });
    it('não pesquisa intervalo invertido ou área excessiva', () => {
        component.start = '2024-09-01'; component.search(); expect(api.search).not.toHaveBeenCalled();
        component.start = '2024-08-01'; component.bbox = [-180,-80,180,80]; component.search(); expect(api.search).not.toHaveBeenCalled();
    });
    it('informa pausa sem retentativa', () => {
        api.search.and.returnValue(throwError(() => new HttpErrorResponse({ status: 503 })));
        component.search(); expect(component.paused).toBeTrue(); expect(api.search).toHaveBeenCalledTimes(1); expect(component.loading).toBeFalse();
    });
    it('não habilita renderização sem perfil compatível', () => {
        api.detail.and.returnValue(of({ item, collection, profiles: [] })); component.select(item); expect(component.selection).toBeNull();
    });
    it('troca formato sem alterar os filtros da pesquisa', () => {
        api.detail.and.returnValue(of({ item, collection, profiles: [{ id: 'rgb', label: 'RGB', bands: ['r','g','b'], min: [0], max: [255], gamma: 1, scale: 1, offset: 0, ndvi: false, fidelity: 'D', note: '' }] }));
        component.select(item); component.format = 'png'; component.updateRender();
        expect(component.selection?.format).toBe('png'); expect(component.start).toBe('2024-08-01');
    });
    it('cancela subscriptions ao sair da tela', () => {
        const pending = new Subject<SearchResult>(); api.search.and.returnValue(pending); component.search(); component.ngOnDestroy();
        expect(pending.observed).toBeFalse();
    });
    it('seleciona a melhor aquisição local e preserva a comparação ao abrir o detalhe', () => {
        const result = { best_local_id: item.id, best_global_id: 'other', rows: [{ id: item.id, item, eligible: true, scene_cloud_pct: 90, cloud_pct: 0 }], evaluated: 1, eligible: 1, seconds: 1 } as PointQualityResult;
        api.bestImages.and.returnValue(of(result));
        api.detail.and.returnValue(of({ item, collection, profiles: [] }));
        component.pointMode = true; component.maxCloud = 5; component.comparePoint();
        expect(api.bestImages.calls.mostRecent().args).toEqual(['landsat-2', -49.4, -16.7, 1000, '2024-08-01', '2024-08-31', 'cloud']);
        expect(component.comparison).toBe(result); expect(component.detail?.item.id).toBe(item.id);
    });
    it('descarta comparação tardia quando o buffer muda', () => {
        const pending = new Subject<PointQualityResult>(); api.bestImages.and.returnValue(pending);
        component.pointMode = true; component.comparePoint(); component.radius = 2000; component.filtersChanged();
        expect(pending.observed).toBeFalse(); expect(component.comparison).toBeNull(); expect(component.loading).toBeFalse();
    });
    it('recusa coordenadas vazias e coleções sem máscara compatível', () => {
        component.pointLat = NaN; component.comparePoint(); expect(api.bestImages).not.toHaveBeenCalled();
        component.pointLat = -16.7; component.collectionId = 'other'; component.comparePoint(); expect(api.bestImages).not.toHaveBeenCalled();
    });

    it('marca o ponto do mapa e cancela a comparação anterior', () => {
        const pending = new Subject<PointQualityResult>(); api.bestImages.and.returnValue(pending);
        component.comparePoint(); component.pointMarked([-54.9, -3.1]);
        expect(component.pointMode).toBeTrue(); expect(component.pointLon).toBe(-54.9);
        expect(component.pointLat).toBe(-3.1); expect(pending.observed).toBeFalse();
        expect(component.comparison).toBeNull();
    });
    it('informa a fase de análise e encerra o indicador após erro', () => {
        const pending = new Subject<PointQualityResult>(); api.bestImages.and.returnValue(pending);
        component.pointMode = true; component.comparePoint(); expect(component.busyMessage).toContain('Analisando');
        pending.error(new HttpErrorResponse({status: 502}));
        expect(component.busyMessage).toBe('');
    });

    it('mantém a comparação enquanto busca novos parâmetros e após uma falha', () => {
        const previous = {best_local_id:'local',best_global_id:'global',rows:[]} as unknown as PointQualityResult;
        component.comparison=previous; component.pointMode=true;
        const pending = new Subject<PointQualityResult>(); api.bestImages.and.returnValue(pending);
        component.repeatComparison({start:'2024-01-01',end:'2024-01-31',radius:2000});
        expect(component.comparison).toBe(previous); expect(component.loading).toBeTrue();
        expect(api.bestImages.calls.mostRecent().args[3]).toBe(2000);
        pending.error(new HttpErrorResponse({status:502})); expect(component.comparison).toBe(previous); expect(component.loading).toBeFalse();
    });

});
