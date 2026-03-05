import {Component, Input, OnChanges, OnDestroy, SimpleChanges} from '@angular/core';
import {HttpClient, HttpParams} from "@angular/common/http";
import {Subject, of} from "rxjs";
import {switchMap, catchError, takeUntil, tap} from "rxjs/operators";
import {PlotlySharedModule} from "angular-plotly.js";
import {NgIf} from "@angular/common";
import {FormsModule} from "@angular/forms";
import {ProgressSpinnerModule} from "primeng/progressspinner";
import {SelectButtonModule} from "primeng/selectbutton";

@Component({
    selector: 'app-sentinel-timeseries',
    standalone: true,
    imports: [
        PlotlySharedModule,
        NgIf,
        FormsModule,
        ProgressSpinnerModule,
        SelectButtonModule
    ],
    templateUrl: './sentinel-timeseries.component.html',
    styleUrl: './sentinel-timeseries.component.scss'
})
export class SentinelTimeseriesComponent implements OnChanges, OnDestroy {
    @Input() lat: number | undefined;
    @Input() lon: number | undefined;
    plotlyData: any;
    loading = false;

    selectedMethod = 'whittaker';
    smoothingMethods = [
        {label: 'Raw', value: 'raw'},
        {label: 'Savgol', value: 'savgol'},
        {label: 'Whittaker', value: 'whittaker'},
        {label: 'Spline', value: 'spline'},
        {label: 'LOESS', value: 'loess'},
    ];

    private loadSubject = new Subject<{lat: number, lon: number, method: string}>();
    private destroy$ = new Subject<void>();

    layout = {
        title: 'Sentinel 2 Timeseries',
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
            switchMap(({lat, lon, method}) => {
                const params = new HttpParams().set('method', method);
                const url = `https://tiles.lapig.iesa.ufg.br/api/timeseries/sentinel2/${lat}/${lon}`;
                return this.http.get<any[]>(url, {params}).pipe(
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
            this.loadSubject.next({lat: this.lat!, lon: this.lon!, method: this.selectedMethod});
        }
    }

    onMethodChange() {
        if (this.isValidLatLon()) {
            this.loadSubject.next({lat: this.lat!, lon: this.lon!, method: this.selectedMethod});
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
