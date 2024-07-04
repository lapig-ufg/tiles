import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class LayerService {
  private baseUrl = 'https://tiles.lapig.iesa.ufg.br/api/layers/sentinel';

  constructor(private http: HttpClient) {}

  getSentinelLayer(period: string, year: number, x: number, y: number, z: number, latitude?: number, longitude?: number): Observable<any> {
    let params: any = {};
    if (latitude !== undefined) params.latitude = latitude;
    if (longitude !== undefined) params.longitude = longitude;
    return this.http.get(`${this.baseUrl}/${period}/${year}/${x}/${y}/${z}`, { params });
  }
}
