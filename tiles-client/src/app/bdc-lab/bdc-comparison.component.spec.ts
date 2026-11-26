import { SimpleChange } from '@angular/core';
import { of, Subject } from 'rxjs';
import { BdcComparisonComponent } from './bdc-comparison.component';
import { BdcLabApiService } from './bdc-lab-api.service';
import { ItemDetail, PointQualityResult } from './bdc-lab.models';

describe('Comparação BDC lado a lado', () => {
    let api: jasmine.SpyObj<BdcLabApiService>;
    let component: BdcComparisonComponent;
    const detail = (id: string) => ({item:{id}, profiles:[{id:'rgb'}]} as ItemDetail);
    beforeEach(() => {
        api = jasmine.createSpyObj('api', ['detail']);
        api.detail.and.callFake((collection, id) => of(detail(id)));
        component = new BdcComparisonComponent(api); component.collection = 'landsat-2'; component.profileId = 'rgb';
        component.comparison = {best_local_id:'local', best_global_id:'global', lon:-54.9, lat:-3.1, radius_m:1000, rows:[]} as unknown as PointQualityResult;
    });
    afterEach(() => component.ngOnDestroy());
    const load = () => component.ngOnChanges({comparison: new SimpleChange(null, component.comparison, true)});
    it('mantém aquisições distintas e o mesmo perfil visual', () => {
        load(); expect(component.sides.map(side => side?.item.id)).toEqual(['local','global']);
        expect(component.sides.every(side => side?.profile.id === 'rgb')).toBeTrue();
        const view = component.view; component.format = 'png'; component.ngOnChanges({format:new SimpleChange('jpeg','png',false)});
        expect(component.view).toBe(view); expect(component.sides.every(side => side?.format === 'png')).toBeTrue();
    });
    it('consulta uma única vez quando os critérios escolhem a mesma aquisição', () => {
        component.comparison.best_global_id = 'local'; load(); expect(api.detail).toHaveBeenCalledTimes(1);
        expect(component.sides.map(side => side?.item.id)).toEqual(['local','local']);
    });
    it('não aceita detalhes de uma comparação anterior', () => {
        const pending = new Subject<ItemDetail>(); api.detail.and.returnValue(pending); load();
        api.detail.and.callFake((collection,id) => of(detail(id))); component.comparison = {...component.comparison,best_local_id:'next'}; load();
        pending.next(detail('old')); expect(component.sides[0]?.item.id).toBe('next'); expect(pending.observed).toBeFalse();
    });
    it('não compara composições diferentes quando o perfil está ausente', () => {
        component.profileId = 'missing'; load(); expect(component.sides).toEqual([null,null]); expect(component.error).toContain('composição');
    });
    it('amplia sem trocar aquisições e restaura a rolagem ao sair com Esc', async () => {
        load(); const sides = component.sides; const view = component.view; const overflow = document.body.style.overflow;
        await component.toggleFullscreen();
        expect(component.expanded).toBeTrue(); expect(document.body.style.overflow).toBe('hidden');
        expect(component.sides).toBe(sides); expect(component.view).toBe(view);
        component.onEscape(); expect(component.expanded).toBeFalse(); expect(document.body.style.overflow).toBe(overflow);
    });
    it('restaura a página quando a comparação é destruída em tela cheia', async () => {
        const overflow = document.body.style.overflow;
        await component.toggleFullscreen(); component.ngOnDestroy();
        expect(component.expanded).toBeFalse(); expect(document.body.style.overflow).toBe(overflow);
    });

    it('aplica a composição comum aos dois mapas sem perder o enquadramento', () => {
        api.detail.and.callFake((collection, id) => of({ ...detail(id), profiles: [{id:'rgb'}, {id:'ndvi'}] } as ItemDetail));
        load(); const view = component.view; const emit = spyOn(component.profileIdChange, 'emit');
        component.changeProfile('ndvi');
        expect(component.sides.every(side => side?.profile.id === 'ndvi')).toBeTrue();
        expect(component.view).toBe(view); expect(emit).toHaveBeenCalledOnceWith('ndvi');
    });
    it('oferece apenas perfis disponíveis nas duas aquisições', () => {
        api.detail.and.callFake((collection, id) => of({ ...detail(id), profiles: id === 'local' ? [{id:'rgb'}, {id:'ndvi'}] : [{id:'rgb'}] } as ItemDetail));
        load(); expect(component.commonProfiles.map(profile => profile.id)).toEqual(['rgb']);
        component.changeProfile('ndvi'); expect(component.profileId).toBe('rgb');
    });

    it('envia período e raio válidos sem encerrar a tela cheia', () => {
        component.expanded = true; component.draftStart = '2024-01-01'; component.draftEnd = '2024-01-31'; component.draftRadius = 2000;
        const emit = spyOn(component.parametersChange, 'emit'); component.searchAgain();
        expect(emit).toHaveBeenCalledOnceWith({start:'2024-01-01',end:'2024-01-31',radius:2000}); expect(component.expanded).toBeTrue();
    });
    it('recusa período invertido, buffer inválido e consulta simultânea', () => {
        const emit = spyOn(component.parametersChange, 'emit'); component.draftStart='2024-02-01'; component.draftEnd='2024-01-01'; component.searchAgain();
        component.draftEnd='2024-03-01'; component.draftRadius=0; component.searchAgain();
        component.draftRadius=1000; component.searching=true; component.searchAgain(); expect(emit).not.toHaveBeenCalled();
    });

});
