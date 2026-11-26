import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { HttpClient } from '@angular/common/http';
import { BdcEvidenceComponent } from './bdc-evidence.component';

describe('BdcEvidenceComponent', () => {
    let component: BdcEvidenceComponent;
    let http: HttpTestingController;
    const file = { path: 'nested/a.json', name: 'a.json', kind: 'data', size: 12, group: 'nested', modified: 1 };
    beforeEach(() => { TestBed.configureTestingModule({ imports: [HttpClientTestingModule] }); http = TestBed.inject(HttpTestingController); component = new BdcEvidenceComponent(TestBed.inject(HttpClient)); });
    afterEach(() => { component.ngOnDestroy(); http.verify(); });
    it('pagina e filtra na API sem carregar todo o acervo', () => {
        component.query = 'Goiás'; component.kind = 'image'; component.load(2);
        const req = http.expectOne(r => r.url === '/api/bdc/evidence');
        expect(req.request.params.get('page')).toBe('2'); expect(req.request.params.get('q')).toBe('Goiás');
        req.flush({ files: [], total: 25, counts: { image: 25 }, available: true });
        expect(component.pages).toBe(2); expect(component.loading).toBeFalse();
    });
    it('cancela pesquisa obsoleta', () => {
        component.load(); const old = http.expectOne(r => r.url === component.base);
        component.load(2); expect(old.cancelled).toBeTrue();
        http.expectOne(r => r.url === component.base).flush({ files: [], total: 0, counts: {}, available: true });
    });
    it('cancela prévia ao fechar e preserva o texto sem interpretar HTML', () => {
        component.open(file); const old = http.expectOne(r => r.url.endsWith('/preview')); component.close(); expect(old.cancelled).toBeTrue();
        component.open(file); http.expectOne(r => r.url.endsWith('/preview')).flush({ text: '<script>bad()</script>', truncated: false });
        expect(component.preview).toBe('<script>bad()</script>');
        expect(component.fileUrl(file, true)).toContain('download=true');
    });
    it('não busca prévia textual para imagens e não repete erro', () => {
        component.open({ ...file, kind: 'image' }); http.expectNone(r => r.url.endsWith('/preview'));
        component.load(); http.expectOne(component.base + '?q=&kind=all&page=1&limit=24').flush({}, { status: 503, statusText: 'Unavailable' });
        expect(component.error).toContain('Não foi possível'); expect(component.loading).toBeFalse();
    });
});

describe('Galeria de comparações BDC', () => {
    let component: BdcEvidenceComponent;
    let http: HttpTestingController;
    beforeEach(() => { TestBed.configureTestingModule({ imports: [HttpClientTestingModule] }); http = TestBed.inject(HttpTestingController); component = new BdcEvidenceComponent(TestBed.inject(HttpClient)); });
    afterEach(() => { component.ngOnDestroy(); http.verify(); });
    it('abre comparações agrupadas como visualização principal', () => {
        expect(component.view).toBe('comparisons'); component.ngOnInit();
        http.expectOne(r => r.url.endsWith('/comparisons')).flush({ comparisons: [], total: 8, options: {}, counts: {}, available: true });
        http.expectOne(r => r.url.endsWith('/summary')).flush({});
        expect(component.comparisonPages).toBe(2);
        http.expectNone(r => r.url === component.base);
    });
    it('cancela comparações antigas quando o contexto muda', () => {
        component.loadComparisons(); const previous = http.expectOne(r => r.url.endsWith('/comparisons'));
        component.filters.source = 'landsat-2'; component.loadComparisons(); expect(previous.cancelled).toBeTrue();
        const current = http.expectOne(r => r.url.endsWith('/comparisons')); expect(current.request.params.get('source')).toBe('landsat-2');
        current.flush({ comparisons: [], total: 0, options: {}, counts: {}, available: true });
    });
});
