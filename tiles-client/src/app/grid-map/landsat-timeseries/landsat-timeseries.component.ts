import {Component, Input, OnChanges, SimpleChanges} from '@angular/core';
import {HttpClient} from "@angular/common/http";
import {catchError, throwError} from "rxjs";
import {PlotlySharedModule} from "angular-plotly.js";
import {NgIf} from "@angular/common";

@Component({
    selector: 'app-landsat-timeseries',
    standalone: true,
    imports: [
        PlotlySharedModule,
        NgIf
    ],
    templateUrl: './landsat-timeseries.component.html',
    styleUrl: './landsat-timeseries.component.scss'
})
export class LandsatTimeseriesComponent implements OnChanges {
  @Input() lat: number | undefined;
  @Input() lon: number | undefined;
    plotlyData: any;

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
    }

    ngOnChanges(changes: SimpleChanges) {
        if (changes['lat'] && changes['lon'] && this.isValidLatLon()) {
            this.loadTimeseries();
        }
    }

    isValidLatLon(): boolean {
        return this.lat !== undefined && this.lon !== undefined && !isNaN(this.lat) && !isNaN(this.lon);
    }


    loadTimeseries() {
        const url = `https://tiles.lapig.iesa.ufg.br/api/timeseries/landsat/${this.lat}/${this.lon}`;
        this.http.get<any[]>(url)
            .pipe(
                catchError(error => {
                    console.error('Error fetching timeseries:', error);
                    return throwError(error);
                })
            )
            .subscribe(data => {
                console.log(data)
                this.plotlyData = data;
            });
    }
}
