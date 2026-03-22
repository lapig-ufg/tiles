import { Component, ElementRef, ViewChild } from '@angular/core';
import { LayoutService } from 'src/app/layout/service/app.layout.service';
import { PointService } from '../grid-map/services/point.service';
import { Observable } from 'rxjs';

interface ParsedCoord {
    lat: number;
    lon: number;
    swapped: boolean;
}

@Component({
    selector: 'app-topbar',
    templateUrl: './app.topbar.component.html'
})
export class AppTopbarComponent {
    coordInput: string = '';
    coordError: string | null = null;
    coordWarning: string | null = null;
    public pointInfo$: Observable<any> = this.pointService.pointInfo$;

    @ViewChild('menubutton') menuButton!: ElementRef;

    constructor(public layoutService: LayoutService, public pointService: PointService) {}

    get isValidCoord(): boolean {
        return this.coordInput.trim().length > 0 && this.tryParseCoordinates(this.coordInput.trim()) !== null;
    }

    onMenuButtonClick() {
        this.layoutService.onMenuToggle();
    }

    onProfileButtonClick() {
        this.layoutService.showProfileSidebar();
    }

    onConfigButtonClick() {
        this.layoutService.showConfigSidebar();
    }

    onCoordInputChange(): void {
        this.coordWarning = null;
        const trimmed = this.coordInput.trim();
        if (!trimmed) {
            this.coordError = null;
            return;
        }
        const result = this.parseCoordinates(trimmed);
        if (!result) {
            // coordError já foi definido dentro de parseCoordinates
            return;
        }
        this.coordError = null;
        if (result.swapped) {
            this.coordWarning = 'Coordenadas invertidas — corrigido automaticamente.';
        }
    }

    onSearchPoint(): void {
        const result = this.parseCoordinates(this.coordInput.trim());
        if (!result) {
            return;
        }
        this.coordError = null;
        if (result.swapped) {
            this.coordInput = `${result.lat}, ${result.lon}`;
            this.coordWarning = 'Coordenadas invertidas — corrigido automaticamente.';
        }
        this.pointService.setActiveFeature(null);
        this.pointService.setPoint({ lat: result.lat, lon: result.lon });
    }

    /** Versão pura sem efeitos colaterais — usada pelo getter do template. */
    private tryParseCoordinates(input: string): ParsedCoord | null {
        if (!input) return null;

        let parts: string[];
        if (input.includes(',')) {
            parts = input.split(',').map(p => p.trim());
        } else if (input.includes(';')) {
            parts = input.split(';').map(p => p.trim());
        } else {
            parts = input.trim().split(/\s+/);
        }

        if (parts.length !== 2 || !parts[0] || !parts[1]) return null;

        const a = parseFloat(parts[0]);
        const b = parseFloat(parts[1]);
        if (isNaN(a) || isNaN(b)) return null;

        if (a >= -90 && a <= 90 && b >= -180 && b <= 180) {
            return { lat: a, lon: b, swapped: false };
        }
        if (b >= -90 && b <= 90 && a >= -180 && a <= 180) {
            return { lat: b, lon: a, swapped: true };
        }
        return null;
    }

    /** Versão com side effects — define coordError para feedback no template. */
    private parseCoordinates(input: string): ParsedCoord | null {
        if (!input) {
            this.coordError = null;
            return null;
        }

        // Separar por vírgula, ponto e vírgula ou espaço(s)
        let parts: string[];
        if (input.includes(',')) {
            parts = input.split(',').map(p => p.trim());
        } else if (input.includes(';')) {
            parts = input.split(';').map(p => p.trim());
        } else {
            parts = input.trim().split(/\s+/);
        }

        if (parts.length !== 2 || !parts[0] || !parts[1]) {
            this.coordError = 'Formato esperado: lat, lon (ex: -15.78, -47.92)';
            return null;
        }

        const a = parseFloat(parts[0]);
        const b = parseFloat(parts[1]);

        if (isNaN(a) || isNaN(b)) {
            this.coordError = 'Valores inválidos. Informe números decimais.';
            return null;
        }

        // Caso normal: lat ∈ [-90,90], lon ∈ [-180,180]
        if (a >= -90 && a <= 90 && b >= -180 && b <= 180) {
            return { lat: a, lon: b, swapped: false };
        }

        // Caso invertido: o primeiro valor parece longitude e o segundo latitude
        if (b >= -90 && b <= 90 && a >= -180 && a <= 180) {
            return { lat: b, lon: a, swapped: true };
        }

        this.coordError = 'Coordenadas fora dos limites (lat: -90 a 90, lon: -180 a 180).';
        return null;
    }
}
