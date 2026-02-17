import {Injectable} from '@angular/core';
import {HttpClient, HttpParams} from '@angular/common/http';
import {Observable} from 'rxjs';
import {
  JobCreateRequest,
  JobResponse,
  JobListResponse,
  StatsResponse,
  PreviewResponse,
  ExportRequest,
  ArtifactInfo,
} from '../interfaces/embedding-maps.interfaces';

@Injectable({providedIn: 'root'})
export class EmbeddingMapsApiService {
  private apiUrl = 'https://tiles.lapig.iesa.ufg.br/api/embedding-maps';

  constructor(private http: HttpClient) {}

  // ----- Jobs CRUD -----

  createJob(req: JobCreateRequest): Observable<JobResponse> {
    return this.http.post<JobResponse>(`${this.apiUrl}/jobs`, req);
  }

  listJobs(limit = 20, offset = 0, status?: string): Observable<JobListResponse> {
    let params = new HttpParams()
      .set('limit', limit.toString())
      .set('offset', offset.toString());
    if (status) {
      params = params.set('status', status);
    }
    return this.http.get<JobListResponse>(`${this.apiUrl}/jobs`, {params});
  }

  getJob(jobId: string): Observable<JobResponse> {
    return this.http.get<JobResponse>(`${this.apiUrl}/jobs/${jobId}`);
  }

  runJob(jobId: string): Observable<any> {
    return this.http.post(`${this.apiUrl}/jobs/${jobId}/run`, {});
  }

  cancelJob(jobId: string): Observable<any> {
    return this.http.post(`${this.apiUrl}/jobs/${jobId}/cancel`, {});
  }

  deleteJob(jobId: string): Observable<any> {
    return this.http.delete(`${this.apiUrl}/jobs/${jobId}`);
  }

  // ----- Tiles & Preview -----

  getTileUrlTemplate(jobId: string, product: string): string {
    return `${this.apiUrl}/tiles/${jobId}/{z}/{x}/{y}.png?product=${product}`;
  }

  getPreview(jobId: string, product: string): Observable<PreviewResponse> {
    const params = new HttpParams().set('product', product);
    return this.http.get<PreviewResponse>(`${this.apiUrl}/preview/${jobId}`, {params});
  }

  // ----- Stats -----

  getStats(jobId: string, product: string): Observable<StatsResponse> {
    const params = new HttpParams().set('product', product);
    return this.http.get<StatsResponse>(`${this.apiUrl}/jobs/${jobId}/stats`, {params});
  }

  // ----- Export & Artifacts -----

  requestExport(jobId: string, req: ExportRequest): Observable<any> {
    return this.http.post(`${this.apiUrl}/jobs/${jobId}/export`, req);
  }

  listArtifacts(jobId: string): Observable<{job_id: string; artifacts: ArtifactInfo[]}> {
    return this.http.get<{job_id: string; artifacts: ArtifactInfo[]}>(`${this.apiUrl}/jobs/${jobId}/artifacts`);
  }
}
