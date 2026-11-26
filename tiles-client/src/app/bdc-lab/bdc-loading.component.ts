import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { ProgressBarModule } from 'primeng/progressbar';

@Component({
    selector: 'app-bdc-loading',
    standalone: true,
    imports: [CommonModule, ProgressSpinnerModule, ProgressBarModule],
    template: `
        <section class="loading-card" role="status" aria-live="polite" aria-atomic="true">
            <div class="loading-content">
                <div class="loading-symbol" aria-hidden="true">
                    <p-progressSpinner [style]="{ width: '32px', height: '32px' }" strokeWidth="3" animationDuration="1.2s"></p-progressSpinner>
                </div>
                <div class="loading-copy">
                    <span class="loading-caption">{{ caption }}</span>
                    <strong>{{ title }}</strong>
                    <p>{{ description }}</p>
                </div>
            </div>
            <div aria-hidden="true"><p-progressBar mode="indeterminate" [showValue]="false" [style]="{ height: '3px' }"></p-progressBar></div>
        </section>
    `,
    styles: [`
        :host { display: block; color: var(--text-color); }
        .loading-card { overflow: hidden; border-radius: 14px; border: 1px solid var(--surface-border); background: var(--surface-overlay, var(--surface-card)); box-shadow: 0 8px 32px rgb(0 0 0 / 12%), 0 2px 6px rgb(0 0 0 / 4%); }
        .loading-content { display: flex; align-items: center; gap: 16px; padding: 20px; }
        .loading-symbol { display: grid; place-items: center; flex: 0 0 52px; height: 52px; border-radius: 16px; background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
        .loading-copy { min-width: 0; }
        .loading-caption { display: block; margin-bottom: 5px; font-size: 10px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--primary-color); }
        strong { display: block; font-size: 14px; line-height: 1.5; font-weight: 600; }
        p { margin: 5px 0 0; color: var(--text-color-secondary); font-size: 12px; line-height: 1.6; }
        :host ::ng-deep .p-progress-spinner-circle { stroke: var(--primary-color) !important; }
        :host ::ng-deep .p-progressbar { border-radius: 0; background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
        :host ::ng-deep .p-progressbar-value { background: var(--primary-color); }
        @media (prefers-reduced-motion: reduce) {
            :host ::ng-deep .p-progress-spinner-svg, :host ::ng-deep .p-progress-spinner-circle,
            :host ::ng-deep .p-progressbar-value::before, :host ::ng-deep .p-progressbar-value::after { animation: none !important; }
        }
        @media (max-width: 480px) { .loading-content { gap: 12px; padding: 16px; } .loading-symbol { flex-basis: 44px; height: 44px; } }
    `],
})
export class BdcLoadingComponent {
    @Input() title = 'Carregando imagens';
    @Input() description = 'Aguarde a conclusão do processamento.';
    @Input() caption = 'Brazil Data Cube';
}
