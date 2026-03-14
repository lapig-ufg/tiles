import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import {
  StacCollection,
  StacCollectionsResponse,
  StacSearchParams,
  StacSearchResponse,
  QueryablesResponse,
} from '../models/stac.models';

@Injectable({ providedIn: 'root' })
export class StacService {

  constructor(private http: HttpClient) {}

  getCollections(baseUrl: string): Observable<StacCollection[]> {
    return this.http.get<StacCollectionsResponse>(`${baseUrl}/collections`).pipe(
      map(res => res.collections)
    );
  }

  getCollection(baseUrl: string, collectionId: string): Observable<StacCollection> {
    return this.http.get<StacCollection>(`${baseUrl}/collections/${collectionId}`);
  }

  search(baseUrl: string, params: StacSearchParams): Observable<StacSearchResponse> {
    return this.http.post<StacSearchResponse>(`${baseUrl}/search`, params);
  }

  getQueryables(baseUrl: string, collectionId?: string): Observable<QueryablesResponse> {
    const url = collectionId
      ? `${baseUrl}/collections/${collectionId}/queryables`
      : `${baseUrl}/queryables`;
    return this.http.get<QueryablesResponse>(url);
  }

  getNextPage(nextUrl: string): Observable<StacSearchResponse> {
    return this.http.get<StacSearchResponse>(nextUrl);
  }
}
