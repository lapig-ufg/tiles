import { BdcLabMapComponent } from './bdc-lab-map.component';
import { Component, ElementRef, HostListener, EventEmitter, Input, Output, OnChanges, OnDestroy, SimpleChanges, ViewChild } from '@angular/core';
import { map, of, switchMap, Subscription } from 'rxjs';
import View from 'ol/View';
import { fromLonLat } from 'ol/proj';
import { BdcLabApiService } from './bdc-lab-api.service';
import { BdcProfile, ImageFormat, ItemDetail, PointQualityResult, RenderSelection } from './bdc-lab.models';

@Component({
    selector: 'app-bdc-comparison',
    template: `
        <div #comparisonPanel class="comparison-panel" [class.expanded]="expanded" tabindex="-1">
        <div class="comparison-toolbar">
            <span><i class="pi pi-images" aria-hidden="true"></i> Comparação de imagens</span>
            <div class="composition-selector">
                <label for="comparison-profile">Composição dos dois mapas</label>
                <select id="comparison-profile" class="p-inputtext" [ngModel]="profileId" (ngModelChange)="changeProfile($event)" [disabled]="searching || loading || !commonProfiles.length">
                    <option *ngFor="let profile of commonProfiles" [value]="profile.id">{{ profile.label }}</option>
                </select>
            </div>
            <button pButton type="button" class="p-button-outlined p-button-sm" [icon]="expanded ? 'pi pi-window-minimize' : 'pi pi-window-maximize'"
                [label]="expanded ? 'Sair da tela cheia' : 'Tela cheia'" [attr.aria-expanded]="expanded" (click)="toggleFullscreen()"></button>
        </div>
        <form class="comparison-parameters" (ngSubmit)="searchAgain()">
            <div><label for="comparison-start">Data inicial</label><input id="comparison-start" name="start" type="date" class="p-inputtext" [(ngModel)]="draftStart" [disabled]="searching || loading" required></div>
            <div><label for="comparison-end">Data final</label><input id="comparison-end" name="end" type="date" class="p-inputtext" [(ngModel)]="draftEnd" [disabled]="searching || loading" required></div>
            <div><label for="comparison-radius">Raio do buffer (m)</label><input id="comparison-radius" name="radius" type="number" class="p-inputtext" min="100" max="5000" step="100" [(ngModel)]="draftRadius" [disabled]="searching || loading" required></div>
            <button pButton type="submit" [label]="searching ? 'Analisando…' : 'Buscar novamente'" [icon]="searching ? 'pi pi-spin pi-spinner' : 'pi pi-search'" [disabled]="searching || loading || !!parameterError"></button>
        </form>
        <p class="comparison-error" *ngIf="parameterError">{{ parameterError }}</p>
        <p class="applied-parameters" *ngIf="comparison.start">Resultado exibido: {{ comparison.start | date:'dd/MM/yyyy':'UTC' }} a {{ comparison.end | date:'dd/MM/yyyy':'UTC' }} · Buffer de {{ comparison.radius_m }} m.</p>
        <app-bdc-loading *ngIf="searching" title="Buscando as melhores imagens" description="Analisando o período e o buffer informados. Os mapas anteriores permanecem visíveis até a conclusão."></app-bdc-loading>
        <p class="comparison-error" role="alert" *ngIf="queryError">{{ queryError }} Os mapas exibidos correspondem à última consulta concluída.</p>
        <p class="comparison-error" role="status" *ngIf="!searching && (!comparison.best_local_id || !comparison.best_global_id)">Não há duas aquisições elegíveis para comparar. Ajuste o período ou o buffer e busque novamente.</p>
        <p class="sync-hint"><i class="pi pi-link" aria-hidden="true"></i> Mapas sincronizados: mova ou aproxime qualquer lado para comparar a mesma área.</p>
        <p class="comparison-error" role="alert" *ngIf="error">{{ error }}</p>
        <div class="comparison-grid">
            <section *ngFor="let side of sides; let i = index; trackBy: trackSide" class="comparison-pane">
                <header><span class="criterion-label">{{ i === 0 ? 'Melhor no buffer' : 'Menor cobertura global' }}</span>
                    <strong>{{ side?.item?.properties?.datetime | date:'dd/MM/yyyy':'UTC' }}</strong>
                    <p class="acquisition-id">{{ side?.item?.id || (loading ? 'Preparando aquisição…' : 'Aquisição indisponível') }}</p>
                    <p *ngIf="side">{{ metrics(side.item.id) }}</p>
                </header>
                <app-bdc-lab-map [expanded]="expanded" [selection]="side" [sharedView]="view" [qualityGeometry]="comparison.geometry"
                    [pointVisible]="true" [pointLon]="comparison.lon" [pointLat]="comparison.lat" [radius]="comparison.radius_m"
                    [allowPointPicking]="true" (pointMarked)="pointMarked.emit($event)" [busyMessage]="searching ? 'Analisando as imagens do período…' : loading ? 'Preparando mapas para comparação…' : ''"></app-bdc-lab-map>
                <app-bdc-loading *ngIf="loading" title="Preparando comparação" description="Carregando as duas aquisições selecionadas."></app-bdc-loading>
            </section>
        </div>
        </div>
    `,
    styles: [`
        .comparison-parameters { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; padding:14px 0; }
        .comparison-parameters > div { display:grid; gap:6px; }
        .comparison-parameters label, .applied-parameters { font-size:12px; color:var(--text-color-secondary); }
        .comparison-parameters input { width:170px; background:var(--surface-card); color:var(--text-color); }
        .comparison-parameters input[type=number] { width:150px; }

        .comparison-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
        .composition-selector { display:flex; align-items:center; gap:10px; margin-left:auto; }
        .composition-selector label { font-size:12px; color:var(--text-color-secondary); }
        .composition-selector select { min-width:160px; background:var(--surface-card); color:var(--text-color); }
        @media(max-width:700px) { .composition-selector { margin-left:0; flex-wrap:wrap; } }
        .comparison-toolbar span { display:flex; align-items:center; gap:8px; font-weight:600; }
        .comparison-panel { background:var(--surface-ground); }
        .comparison-panel.expanded { position:fixed; inset:0; z-index:2000; padding:20px; display:flex; flex-direction:column; overflow:auto; }
        .comparison-panel:fullscreen { width:100%; height:100%; }
        .expanded .comparison-grid { flex:1; min-height:0; grid-template-columns:repeat(2,minmax(0,1fr)); overflow:visible; }
        .expanded .comparison-pane { display:flex; flex-direction:column; min-height:0; }
        .expanded app-bdc-lab-map { flex:1; min-height:0; }
        .expanded .acquisition-id { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        @media(max-width:700px) { .comparison-panel.expanded { padding:10px; } .expanded .comparison-grid { gap:8px; } .expanded header { padding:10px; } }

        .sync-hint { display:flex; align-items:center; gap:8px; color:var(--text-color-secondary); font-size:13px; margin:16px 0; }
        .sync-hint i { color:var(--primary-color); }
        .comparison-grid { display:grid; grid-template-columns:repeat(2,minmax(300px,1fr)); gap:16px; overflow-x:auto; padding-bottom:8px; }
        .comparison-pane { min-width:0; }
        header { padding:16px; border:1px solid var(--surface-border); background:var(--surface-card); border-radius:12px; margin-bottom:12px; }
        .criterion-label { color:var(--primary-color); font-size:12px; font-weight:600; }
        strong { float:right; } p { font-size:12px; margin:8px 0 0; line-height:1.5; }
        .acquisition-id { overflow-wrap:anywhere; color:var(--text-color-secondary); }
        .comparison-error { padding:12px; color:var(--red-600); }
    `],
})
export class BdcComparisonComponent implements OnChanges, OnDestroy {
    @ViewChild('comparisonPanel') panel?: ElementRef<HTMLElement>;
    expanded = false;
    private previousFocus: HTMLElement | null = null;
    private previousOverflow: string | null = null;
    private nativeFullscreen = false;

    async toggleFullscreen(): Promise<void> {
        if (this.expanded) { this.closeFullscreen(); return; }
        this.previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        this.previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden'; this.expanded = true;
        const element = this.panel?.nativeElement;
        element?.focus();
        if (element?.requestFullscreen && !document.fullscreenElement) {
            try { await element.requestFullscreen(); this.nativeFullscreen = document.fullscreenElement === element; }
            catch { this.nativeFullscreen = false; }
        }
    }
    @HostListener('document:fullscreenchange') onFullscreenChange(): void {
        if (document.fullscreenElement === this.panel?.nativeElement) this.nativeFullscreen = true;
        else if (this.nativeFullscreen) this.closeFullscreen();
    }
    @HostListener('document:keydown.escape') onEscape(): void { if (this.expanded) this.closeFullscreen(); }
    closeFullscreen(): void {
        this.expanded = false; this.nativeFullscreen = false;
        if (this.previousOverflow !== null) { document.body.style.overflow = this.previousOverflow; this.previousOverflow = null; }
        if (document.fullscreenElement && document.fullscreenElement === this.panel?.nativeElement) void document.exitFullscreen().catch(() => {});
        this.previousFocus?.focus(); this.previousFocus = null;
    }
    @Output() pointMarked = new EventEmitter<[number, number]>();
    @ViewChild(BdcLabMapComponent) map?: BdcLabMapComponent;
    @Input() comparison!: PointQualityResult;
    @Input() collection = '';
    @Output() profileIdChange = new EventEmitter<string>();
    get commonProfiles(): BdcProfile[] {
        if (this.details.length !== 2) return [];
        return this.details[0].profiles.filter(profile => this.details[1].profiles.some(other => other.id === profile.id));
    }
    changeProfile(id: string): void {
        if (!this.commonProfiles.some(profile => profile.id === id)) return;
        this.profileId = id; this.render(); this.profileIdChange.emit(id);
    }
    @Input() start = '';
    @Input() end = '';
    @Input() radius = 1000;
    @Input() searching = false;
    @Input() queryError = '';
    @Output() parametersChange = new EventEmitter<{start: string; end: string; radius: number}>();
    draftStart = '';
    draftEnd = '';
    draftRadius = 1000;
    get parameterError(): string {
        const start = Date.parse(this.draftStart); const end = Date.parse(this.draftEnd);
        if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return 'Informe um período válido.';
        if ((end - start) / 86400000 > 366) return 'O intervalo máximo é de 366 dias.';
        if (typeof this.draftRadius !== 'number' || !Number.isFinite(this.draftRadius) || this.draftRadius < 100 || this.draftRadius > 5000) return 'O raio deve estar entre 100 e 5.000 metros.';
        return '';
    }
    searchAgain(): void {
        if (this.searching || this.loading || this.parameterError) return;
        this.parametersChange.emit({start:this.draftStart, end:this.draftEnd, radius:this.draftRadius});
    }
    @Input() profileId = '';
    @Input() format: ImageFormat = 'jpeg';
    @Input() quality = 90;
    @Input() background: 'white' | 'black' = 'white';
    view = new View({center: fromLonLat([-49.4, -16.7]), zoom:14, minZoom:6, maxZoom:18});
    sides: (RenderSelection | null)[] = [null, null];
    loading = false;
    error = '';
    private details: ItemDetail[] = [];
    private request?: Subscription;
    private generation = 0;
    private lastPoint?: [number, number];

    constructor(private api: BdcLabApiService) {}
    ngOnChanges(changes: SimpleChanges): void {
        if (changes['start']) this.draftStart = this.start;
        if (changes['end']) this.draftEnd = this.end;
        if (changes['radius']) this.draftRadius = this.radius;
        if (changes['comparison'] || changes['collection']) this.load();
        else if (['profileId','format','quality','background'].some(key => changes[key])) this.render();
    }
    ngOnDestroy(): void { this.closeFullscreen(); this.generation++; this.request?.unsubscribe(); }
    private load(): void {
        this.request?.unsubscribe(); const generation = ++this.generation;
        this.details = []; this.sides = [null, null]; this.error = '';
        if (!this.lastPoint || this.lastPoint[0] !== this.comparison.lon || this.lastPoint[1] !== this.comparison.lat) {
            this.view.setCenter(fromLonLat([this.comparison.lon, this.comparison.lat])); this.view.setZoom(14);
            this.lastPoint = [this.comparison.lon, this.comparison.lat];
        }
        const local = this.comparison.best_local_id; const global = this.comparison.best_global_id;
        if (!local || !global) { this.loading = false; return; }
        this.loading = true;
        const first = this.api.detail(this.collection, local);
        this.request = first.pipe(switchMap(localDetail => local === global ? of([localDetail, localDetail]) :
            this.api.detail(this.collection, global).pipe(map(globalDetail => [localDetail, globalDetail])))).subscribe({
            next: details => {
                if (generation !== this.generation) return;
                this.details = details.length === 1 ? [details[0], details[0]] : details;
                this.loading = false; this.render();
            },
            error: () => { if (generation === this.generation) { this.loading = false; this.error = 'Não foi possível carregar as duas aquisições. Execute a comparação novamente.'; } },
        });
    }
    private render(): void {
        if (!this.details.length) return;
        const profiles = this.details.map(detail => detail.profiles.find(profile => profile.id === this.profileId));
        if (profiles.some(profile => !profile)) {
            this.sides = [null, null];
            this.error = this.profileId ? 'A composição escolhida não está disponível nas duas aquisições.' : '';
            return;
        }
        this.error = '';
        this.sides = this.details.map((detail, index) => ({collection:this.collection, item:detail.item, profile:profiles[index]!, format:this.format, quality:this.quality, background:this.background}));
    }
    trackSide(index: number): number { return index; }
    metrics(id: string): string {
        const row = this.comparison.rows.find(row => row.id === id);
        return `Nuvens no buffer: ${row?.cloud_pct?.toFixed(1) ?? 'não informadas'}% · Na cena: ${row?.scene_cloud_pct?.toFixed(1) ?? 'não informadas'}%`;
    }
}
