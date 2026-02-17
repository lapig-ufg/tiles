import {Injectable, OnDestroy} from '@angular/core';
import {BehaviorSubject, Subscription, timer, Observable} from 'rxjs';
import {switchMap, takeWhile, tap} from 'rxjs/operators';
import {
  JobResponse,
  JobStatus,
  ProductType,
} from '../interfaces/embedding-maps.interfaces';
import {EmbeddingMapsApiService} from './embedding-maps-api.service';

@Injectable({providedIn: 'root'})
export class EmbeddingMapsStateService implements OnDestroy {
  private activeJobSubject = new BehaviorSubject<JobResponse | null>(null);
  private selectedProductSubject = new BehaviorSubject<ProductType>('rgb_embedding');
  private jobListSubject = new BehaviorSubject<JobResponse[]>([]);
  private loadingSubject = new BehaviorSubject<boolean>(false);

  activeJob$ = this.activeJobSubject.asObservable();
  selectedProduct$ = this.selectedProductSubject.asObservable();
  jobList$ = this.jobListSubject.asObservable();
  loading$ = this.loadingSubject.asObservable();

  private pollSub: Subscription | null = null;

  constructor(private api: EmbeddingMapsApiService) {}

  ngOnDestroy(): void {
    this.stopPolling();
  }

  // ----- Getters -----

  get activeJob(): JobResponse | null {
    return this.activeJobSubject.value;
  }

  get selectedProduct(): ProductType {
    return this.selectedProductSubject.value;
  }

  // ----- Setters -----

  setActiveJob(job: JobResponse | null): void {
    this.activeJobSubject.next(job);
  }

  setSelectedProduct(product: ProductType): void {
    this.selectedProductSubject.next(product);
  }

  setJobList(jobs: JobResponse[]): void {
    this.jobListSubject.next(jobs);
  }

  setLoading(loading: boolean): void {
    this.loadingSubject.next(loading);
  }

  // ----- Polling -----

  pollJobStatus(jobId: string, intervalMs = 3000): Observable<JobResponse> {
    this.stopPolling();

    const poll$ = timer(0, intervalMs).pipe(
      switchMap(() => this.api.getJob(jobId)),
      tap(job => this.setActiveJob(job)),
      takeWhile(job => this.isTerminalStatus(job.status) === false, true),
    );

    return poll$;
  }

  startPolling(jobId: string, intervalMs = 3000): void {
    this.pollSub = this.pollJobStatus(jobId, intervalMs).subscribe();
  }

  stopPolling(): void {
    if (this.pollSub) {
      this.pollSub.unsubscribe();
      this.pollSub = null;
    }
  }

  // ----- Helpers -----

  private isTerminalStatus(status: JobStatus): boolean {
    return status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED';
  }

  refreshJobList(limit = 20, offset = 0): void {
    this.setLoading(true);
    this.api.listJobs(limit, offset).subscribe({
      next: res => {
        this.setJobList(res.items);
        this.setLoading(false);
      },
      error: () => this.setLoading(false),
    });
  }
}
