import { NgZone, SimpleChange } from '@angular/core';
import { fromLonLat } from 'ol/proj';
import { BdcLabMapComponent } from './bdc-lab-map.component';
import { BdcLabApiService } from './bdc-lab-api.service';

describe('Marcação de ponto BDC', () => {
    let component: BdcLabMapComponent;
    beforeEach(() => { component = new BdcLabMapComponent({} as BdcLabApiService, new NgZone({enableLongStackTrace: false})); });
    afterEach(() => component.ngOnDestroy());
    it('só emite coordenadas quando a ferramenta está ativa', () => {
        const emit = spyOn(component.pointMarked, 'emit'); component.allowPointPicking = true;
        component.markCoordinate(fromLonLat([-54.9, -3.1])); expect(emit).not.toHaveBeenCalled();
        component.togglePointPicking(); component.markCoordinate(fromLonLat([-54.9, -3.1]));
        expect(emit).toHaveBeenCalledOnceWith([-54.9, -3.1]); expect(component.pickingPoint).toBeFalse();
    });
    it('bloqueia marcação durante a análise e permite cancelar', () => {
        const emit = spyOn(component.pointMarked, 'emit'); component.allowPointPicking = true;
        component.togglePointPicking(); component.togglePointPicking(); expect(component.pickingPoint).toBeFalse();
        component.busyMessage = 'Analisando'; component.togglePointPicking(); component.markCoordinate(fromLonLat([0, 0]));
        expect(emit).not.toHaveBeenCalled();
    });
    it('encerra ferramenta quando a coleção deixa de permitir pontos', () => {
        component.allowPointPicking = true; component.togglePointPicking(); component.allowPointPicking = false;
        component.ngOnChanges({allowPointPicking: new SimpleChange(true, false, false)});
        expect(component.pickingPoint).toBeFalse();
    });
});
