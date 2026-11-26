import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { BdcLabApiService } from './bdc-lab-api.service';
import { RenderSelection, safeBdcUrl } from './bdc-lab.models';

describe('BdcLabApiService', () => {
    let api: BdcLabApiService;
    let http: HttpTestingController;
    beforeEach(() => { TestBed.configureTestingModule({ imports: [HttpClientTestingModule], providers: [BdcLabApiService] }); api = TestBed.inject(BdcLabApiService); http = TestBed.inject(HttpTestingController); });
    afterEach(() => http.verify());
    it('consulta o serviço isolado sem acessar a API GEE', () => {
        api.collections().subscribe(); http.expectOne('/api/bdc/collections').flush({ collections: [], paused: false });
    });
    it('preserva bbox, filtros e paginação', () => {
        api.search({ collection: 'landsat-2', bbox: [-50,-17,-49,-16], start: '2024-08-01', end: '2024-08-31', page: 2, limit: 12, max_cloud: 30 }).subscribe();
        const request = http.expectOne(r => r.url === '/api/bdc/search');
        expect(request.request.params.get('page')).toBe('2'); expect(request.request.params.get('bbox')).toBe('-50,-17,-49,-16'); expect(request.request.params.get('max_cloud')).toBe('30'); request.flush({ items: [] });
    });
    it('separa formato, qualidade e fundo nas URLs dos tiles', () => {
        const selection = { collection: 'landsat-2', item: { id: 'LC08' }, profile: { id: 'landsat-tvi-true' }, format: 'jpeg', quality: 85, background: 'white' } as RenderSelection;
        expect(api.tileUrl(selection, 12, 34, 10)).toContain('/10/12/34.jpeg?');
        expect(api.tileUrl(selection, 12, 34, 10)).toContain('quality=85');
        expect(api.tileUrl({ ...selection, format: 'png' }, 12, 34, 10)).not.toEqual(api.tileUrl(selection, 12, 34, 10));
    });
    it('não repete erro 503', () => {
        let status = 0; api.collections().subscribe({ error: error => status = error.status });
        http.expectOne('/api/bdc/collections').flush({}, { status: 503, statusText: 'Unavailable' });
        expect(status).toBe(503); http.expectNone('/api/bdc/collections');
    });
    it('preserva headers e bytes da imagem', () => {
        api.tile('/api/bdc/tiles/test').subscribe(response => { expect(response.headers.get('X-Cache')).toBe('HIT'); expect(response.body?.size).toBe(3); });
        http.expectOne('/api/bdc/tiles/test').flush(new Blob(['abc'], { type: 'image/jpeg' }), { headers: { 'X-Cache': 'HIT' } });
    });
});

describe('links de assets BDC', () => {
    it('aceita somente HTTPS no provedor esperado', () => {
        expect(safeBdcUrl('https://data.inpe.br/bdc/data/a.tif')).toBeTruthy();
        for (const url of ['javascript:alert(1)', '//evil.test', 'https://data.inpe.br.evil.test/a', 'https://user:secret@data.inpe.br/a', 'http://data.inpe.br/a', 'https://data.inpe.br:8000/a']) expect(safeBdcUrl(url)).toBeNull();
    });
});
