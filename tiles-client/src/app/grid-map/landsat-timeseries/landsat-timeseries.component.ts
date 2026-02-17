import {Component, Input, OnChanges, OnDestroy, SimpleChanges} from '@angular/core';
import {HttpClient} from "@angular/common/http";
import {Subject, of} from "rxjs";
import {switchMap, catchError, takeUntil, tap} from "rxjs/operators";
import {PlotlySharedModule} from "angular-plotly.js";
import {NgIf} from "@angular/common";
import {ProgressSpinnerModule} from "primeng/progressspinner";

@Component({
    selector: 'app-landsat-timeseries',
    standalone: true,
    imports: [
        PlotlySharedModule,
        NgIf,
        ProgressSpinnerModule
    ],
    templateUrl: './landsat-timeseries.component.html',
    styleUrl: './landsat-timeseries.component.scss'
})
export class LandsatTimeseriesComponent implements OnChanges, OnDestroy {
    @Input() lat: number | undefined;
    @Input() lon: number | undefined;
    plotlyData: any;
    loading = false;

    private loadSubject = new Subject<{lat: number, lon: number}>();
    private destroy$ = new Subject<void>();

    layout = {
        title: 'Landsat Timeseries',
        xaxis: {
            title: 'Date',
            ticks: {
                color: '#495057'
            },
            grid: {
                color: '#ebedef'
            }
        },
        yaxis: {
            title: 'NDVI',
            min: 0,
            max: 1.2,
            type: 'linear'
        },
        yaxis2: {
            title: 'Precipitation (mm)',
            overlaying: 'y',
            side: 'right',
            type: 'linear'
        }
    };

    constructor(private http: HttpClient) {
        this.loadSubject.pipe(
            takeUntil(this.destroy$),
            tap(() => { this.loading = true; this.plotlyData = null; }),
            switchMap(({lat, lon}) => {
                const url = `https://tiles.lapig.iesa.ufg.br/api/timeseries/landsat/${lat}/${lon}`;
                return this.http.get<any[]>(url).pipe(
                    catchError(() => of(null))
                );
            })
        ).subscribe(data => {
            this.loading = false;
            if (data) this.plotlyData = data;
        });
    }

    ngOnChanges(changes: SimpleChanges) {
        if ((changes['lat'] || changes['lon']) && this.isValidLatLon()) {
            this.loadSubject.next({lat: this.lat!, lon: this.lon!});
        }
    }

    ngOnDestroy() {
        this.destroy$.next();
        this.destroy$.complete();
    }

    isValidLatLon(): boolean {
        return this.lat !== undefined && this.lon !== undefined && !isNaN(this.lat) && !isNaN(this.lon);
    }
}
