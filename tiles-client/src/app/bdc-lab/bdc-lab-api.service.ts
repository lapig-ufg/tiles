import { Injectable } from '@angular/core';
import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Observable, timeout } from 'rxjs';
import { BdcCollection, ItemDetail, RenderSelection, SearchFilters, SearchResult, WarmResult } from './bdc-lab.models';

@Injectable()
export class BdcLabApiService {
    readonly base = '/api/bdc';
    constructor(private http: HttpClient) {}

    collections(): Observable<{ collections: BdcCollection[]; paused: boolean }> {
        return this.http.get<{ collections: BdcCollection[]; paused: boolean }>(`${this.base}/collections`).pipe(timeout(90000));
    }
    search(filters: SearchFilters): Observable<SearchResult> {
        const values = { ...filters, bbox: filters.bbox.join(',') };
        return this.http.get<SearchResult>(`${this.base}/search`, { params: new HttpParams({ fromObject: values }) }).pipe(timeout(90000));
    }
    detail(collection: string, item: string): Observable<ItemDetail> {
        return this.http.get<ItemDetail>(`${this.base}/items/${encodeURIComponent(collection)}/${encodeURIComponent(item)}`).pipe(timeout(90000));
    }
    tileUrl(selection: RenderSelection, x: number, y: number, z: number): string {
        const params = new HttpParams({ fromObject: { profile: selection.profile.id, quality: selection.quality, background: selection.background } });
        return `${this.base}/tiles/${encodeURIComponent(selection.collection)}/${encodeURIComponent(selection.item.id)}/${z}/${x}/${y}.${selection.format}?${params}`;
    }
    tile(url: string): Observable<HttpResponse<Blob>> {
        return this.http.get(url, { observe: 'response', responseType: 'blob' }).pipe(timeout(180000));
    }
    warm(selection: RenderSelection, tiles: [number, number, number][]): Observable<WarmResult> {
        return this.http.post<WarmResult>(`${this.base}/prewarm`, {
            collection: selection.collection, item: selection.item.id, profile: selection.profile.id,
            format: selection.format, quality: selection.quality, background: selection.background, tiles,
        }).pipe(timeout(600000));
    }
}
