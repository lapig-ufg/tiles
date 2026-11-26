import { of, Subject, throwError } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { BdcLabComponent } from './bdc-lab.component';
import { BdcLabApiService } from './bdc-lab-api.service';
import { BdcCollection, BdcItem, ItemDetail, SearchResult } from './bdc-lab.models';

describe('BdcLabComponent', () => {
    let api: jasmine.SpyObj<BdcLabApiService>;
    let component: BdcLabComponent;
    const collection = { id: 'landsat-2', title: 'Landsat', family: 'Landsat', providers: [], extent: {}, formats: [], bands: [], summaries: {}, description: '', license: '', has_cog: true, public: true } as BdcCollection;
    const item = { id: 'scene', collection: 'landsat-2', properties: {}, assets: {}, bbox: [-50,-17,-49,-16] } as BdcItem;
    beforeEach(() => {
        api = jasmine.createSpyObj('BdcLabApiService', ['collections','search','detail']);
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
});
