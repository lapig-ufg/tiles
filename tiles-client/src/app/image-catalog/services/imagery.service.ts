import {Injectable} from '@angular/core';
import {HttpClient, HttpParams} from '@angular/common/http';
import {Observable} from 'rxjs';

export interface CatalogParams {
    lat: number;
    lon: number;
    start: string;
    end: string;
    visparam?: string;
    bufferMeters?: number;
    limit?: number;
    offset?: number;
    sort?: 'date_asc' | 'cloud_asc';
    maxCloud?: number;
}

export interface CatalogItem {
    id: string;
    datetime: string;
    cloud: number | null;
    cloudSource: string;
    platform: string;
    additional: Record<string, any>;
    selected?: boolean;
}

export interface CatalogResponse {
    layer: string;
    query: Record<string, any>;
    total: number;
    limit: number;
    offset: number;
    nextOffset: number | null;
    items: CatalogItem[];
}

@Injectable({providedIn: 'root'})
export class ImageryService {
    private apiUrl = 'https://tiles.lapig.iesa.ufg.br/api/imagery';

    constructor(private http: HttpClient) {}

    getCatalog(layer: string, params: CatalogParams): Observable<CatalogResponse> {
        let httpParams = new HttpParams()
            .set('lat', params.lat.toString())
            .set('lon', params.lon.toString())
            .set('start', params.start)
            .set('end', params.end);

        if (params.visparam) httpParams = httpParams.set('visparam', params.visparam);
        if (params.bufferMeters) httpParams = httpParams.set('bufferMeters', params.bufferMeters.toString());
        if (params.limit) httpParams = httpParams.set('limit', params.limit.toString());
        if (params.offset) httpParams = httpParams.set('offset', params.offset.toString());
        if (params.sort) httpParams = httpParams.set('sort', params.sort);
        if (params.maxCloud !== undefined && params.maxCloud < 100) httpParams = httpParams.set('maxCloud', params.maxCloud.toString());

        return this.http.get<CatalogResponse>(`${this.apiUrl}/${layer}/catalog`, {params: httpParams});
    }
}
