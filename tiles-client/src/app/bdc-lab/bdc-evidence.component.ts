import { environment } from '../../environments/environment';
import { Component, HostListener, OnDestroy, OnInit } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Subscription, timeout } from 'rxjs';

interface EvidenceFile { title?: string; path: string; name: string; kind: string; size: number; group: string; modified: number; }
interface EvidenceIndex { files: EvidenceFile[]; total: number; counts: Record<string, number>; available: boolean; }
interface MetricGroup { source: string; stage: string; visparam: string; z: number; n: number; mae: number[] | null; rmse: number[] | null; ssim: number[] | null; }
interface EvidenceSummary { counts?: Record<string, number>; reference_formats?: Record<string, number>; groups?: MetricGroup[]; }

interface Comparison {
    id: string; source: string; region: string; year: number; month: number; period: string; visparam: string; stage: string; z: number;
    status: string; reason: string; board: EvidenceFile | null; data: EvidenceFile; reference_format: string;
    metrics: { valid_pixels?: number; mae?: number[] | null; rmse?: number[] | null; ssim?: number[] | null; above_threshold_pct?: number | null; threshold_dn?: number; coverage_disagreement_pct?: number };
}
interface ComparisonIndex { comparisons: Comparison[]; total: number; available: boolean; options: Record<string, string[]>; counts: Record<string, number>; }

@Component({ selector: 'app-bdc-evidence', templateUrl: './bdc-evidence.component.html', styleUrls: ['./bdc-evidence.component.scss'] })
export class BdcEvidenceComponent implements OnInit, OnDestroy {
    view: 'comparisons' | 'files' = 'comparisons';
    comparisons: Comparison[] = [];
    comparisonTotal = 0;
    comparisonPage = 1;
    comparisonLoading = false;
    comparisonError = '';
    filters = { source: '', region: '', period: '', status: '' };
    options: Record<string, string[]> = {};
    comparisonCounts: Record<string, number> = {};
    private comparisonRequest?: Subscription;
    readonly base = `${environment.bdcApiBaseUrl}/evidence`;
    files: EvidenceFile[] = [];
    counts: Record<string, number> = {};
    total = 0;
    page = 1;
    query = '';
    kind = 'all';
    loading = false;
    available = true;
    error = '';
    summary: EvidenceSummary = {};
    metricSource = '';
    selected: EvidenceFile | null = null;
    preview = '';
    previewLoading = false;
    previewError = '';
    truncated = false;
    private listRequest?: Subscription;
    private previewRequest?: Subscription;
    private summaryRequest?: Subscription;
    private returnFocus?: HTMLElement;

    constructor(private http: HttpClient) {}
    ngOnInit(): void {
        this.loadComparisons();
        this.summaryRequest = this.http.get<EvidenceSummary>(`${this.base}/summary`).pipe(timeout(30000)).subscribe({ next: value => this.summary = value, error: () => this.summary = {} });
    }
    get comparisonPages(): number { return Math.max(1, Math.ceil(this.comparisonTotal / 6)); }
    changeView(view: 'comparisons' | 'files'): void { this.view = view; if (view === 'files') this.load(); }
    loadComparisons(page = 1): void {
        this.comparisonRequest?.unsubscribe(); this.comparisonPage = page; this.comparisonLoading = true; this.comparisonError = '';
        const params = new HttpParams({ fromObject: { ...this.filters, page, limit: 6 } });
        this.comparisonRequest = this.http.get<ComparisonIndex>(`${this.base}/comparisons`, { params }).pipe(timeout(30000)).subscribe({
            next: value => { this.comparisons = value.comparisons; this.comparisonTotal = value.total; this.available = value.available; this.options = value.options; this.comparisonCounts = value.counts; this.comparisonLoading = false; },
            error: () => { this.comparisons = []; this.comparisonError = 'Não foi possível carregar as comparações. Verifique a conexão e tente novamente.'; this.comparisonLoading = false; },
        });
    }
    regionName(value: string): string { return ({ 'cerrado-goias': 'Cerrado, Goiás', 'amazonia-para': 'Amazônia, Pará' } as Record<string, string>)[value] || value; }
    sourceName(value: string): string { return ({ 'landsat-2': 'Landsat', 'S2_L2A-1': 'Sentinel-2' } as Record<string, string>)[value] || value; }
    periodName(value: string): string { return ({ WET: 'Período chuvoso', DRY: 'Período seco', MONTH: 'Recorte mensal' } as Record<string, string>)[value] || value; }
    composition(value: string): string { return ({ 'landsat-tvi-true': 'Cores naturais', 'landsat-tvi-agri': 'Destaque de agricultura', 'landsat-tvi-false': 'Falsa cor', 'landsat-tvi-ndvi': 'Índice de vegetação (NDVI)', 'tvi-rgb': 'Cores naturais', 'tvi-green': 'Vegetação e uso do solo', 'tvi-red': 'Infravermelho e vegetação', 'tvi-ndvi': 'Índice de vegetação (NDVI)' } as Record<string, string>)[value] || value; }
    statusName(value: string): string { return ({ measured: 'Com dados em comum', no_overlap: 'Sem dados em comum', unavailable: 'Comparação indisponível' } as Record<string, string>)[value] || value; }
    trackComparison(_: number, value: Comparison): string { return value.id; }
    get pages(): number { return Math.max(1, Math.ceil(this.total / 24)); }
    get sources(): string[] { return [...new Set((this.summary.groups || []).map(g => g.source))]; }
    get metrics(): MetricGroup[] { return (this.summary.groups || []).filter(g => !this.metricSource || g.source === this.metricSource); }
    load(page = 1): void {
        this.listRequest?.unsubscribe();
        this.page = page;
        this.loading = true;
        this.error = '';
        const params = new HttpParams({ fromObject: { q: this.query.trim(), kind: this.kind, page, limit: 24 } });
        this.listRequest = this.http.get<EvidenceIndex>(this.base, { params }).pipe(timeout(30000)).subscribe({
            next: result => { this.files = result.files; this.total = result.total; this.counts = result.counts; this.available = result.available; this.loading = false; },
            error: () => { this.files = []; this.error = 'Não foi possível carregar as evidências. Verifique a conexão com a API e tente novamente.'; this.loading = false; },
        });
    }
    fileUrl(file: EvidenceFile, download = false): string {
        return `${this.base}/file?${new HttpParams({ fromObject: { path: file.path, download } })}`;
    }
    open(file: EvidenceFile): void {
        this.previewRequest?.unsubscribe();
        this.returnFocus = document.activeElement as HTMLElement;
        this.selected = file;
        this.preview = '';
        this.previewError = '';
        this.truncated = false;
        this.previewLoading = file.kind !== 'image';
        if (file.kind !== 'image') {
            this.previewRequest = this.http.get<{ text: string; truncated: boolean }>(`${this.base}/preview`, { params: { path: file.path } }).pipe(timeout(30000)).subscribe({
                next: value => { this.preview = value.text; this.truncated = value.truncated; this.previewLoading = false; },
                error: () => { this.previewError = 'A prévia não está disponível. Tente baixar o arquivo.'; this.previewLoading = false; },
            });
        }
    }
    close(): void { this.previewRequest?.unsubscribe(); this.selected = null; this.returnFocus?.focus(); }
    @HostListener('document:keydown.escape') escape(): void { if (this.selected) this.close(); }
    bytes(value: number): string { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`; }
    channels(values: number[] | null): string { return values?.map(v => v.toFixed(3)).join(' / ') || 'Não medido'; }
    trackFile(_: number, file: EvidenceFile): string { return file.path; }
    ngOnDestroy(): void { this.comparisonRequest?.unsubscribe(); this.listRequest?.unsubscribe(); this.previewRequest?.unsubscribe(); this.summaryRequest?.unsubscribe(); }
}
