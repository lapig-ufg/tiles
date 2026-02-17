import {Component, OnInit, OnDestroy} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import {Subscription} from 'rxjs';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';
import {EmbeddingMapsStateService} from '../../services/embedding-maps-state.service';
import {JobResponse, JobStatus} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-job-detail',
  templateUrl: './job-detail.component.html',
  styleUrls: ['./job-detail.component.scss']
})
export class JobDetailComponent implements OnInit, OnDestroy {
  job: JobResponse | null = null;
  loading = true;
  jobId = '';

  private subscriptions: Subscription[] = [];

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private api: EmbeddingMapsApiService,
    private stateService: EmbeddingMapsStateService,
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.params['id'];
    this.loadJob();
  }

  ngOnDestroy(): void {
    this.stateService.stopPolling();
    this.subscriptions.forEach(s => s.unsubscribe());
  }

  loadJob(): void {
    this.loading = true;
    this.api.getJob(this.jobId).subscribe({
      next: job => {
        this.job = job;
        this.stateService.setActiveJob(job);
        this.loading = false;

        if (job.status === 'RUNNING') {
          this.startPolling();
        }
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  runJob(): void {
    this.api.runJob(this.jobId).subscribe({
      next: () => {
        this.startPolling();
      }
    });
  }

  cancelJob(): void {
    this.api.cancelJob(this.jobId).subscribe({
      next: () => this.loadJob(),
    });
  }

  deleteJob(): void {
    this.api.deleteJob(this.jobId).subscribe({
      next: () => this.router.navigate(['embedding']),
    });
  }

  navigateToVisualize(): void {
    this.router.navigate(['embedding', 'jobs', this.jobId, 'view']);
  }

  navigateToExport(): void {
    this.router.navigate(['embedding', 'jobs', this.jobId, 'export']);
  }

  private startPolling(): void {
    const sub = this.stateService.pollJobStatus(this.jobId, 3000).subscribe({
      next: job => {
        this.job = job;
      }
    });
    this.subscriptions.push(sub);
  }

  getStatusSeverity(status: string): string {
    switch (status) {
      case 'COMPLETED': return 'success';
      case 'RUNNING': return 'info';
      case 'FAILED': return 'danger';
      case 'CANCELLED': return 'warning';
      default: return 'info';
    }
  }

  get canRun(): boolean {
    return this.job?.status === 'PENDING' || this.job?.status === 'FAILED';
  }

  get canCancel(): boolean {
    return this.job?.status === 'RUNNING';
  }

  get canVisualize(): boolean {
    return this.job?.status === 'COMPLETED';
  }

  get timelineEvents(): any[] {
    if (!this.job) return [];
    const events: any[] = [];

    events.push({
      status: 'Criado',
      date: this.job.created_at,
      icon: 'pi pi-plus',
      color: '#607D8B',
    });

    if (this.job.started_at) {
      events.push({
        status: 'Iniciado',
        date: this.job.started_at,
        icon: 'pi pi-play',
        color: '#2196F3',
      });
    }

    if (this.job.completed_at) {
      const color = this.job.status === 'COMPLETED' ? '#4CAF50' : '#f44336';
      events.push({
        status: this.job.status === 'COMPLETED' ? 'Concluido' : 'Finalizado',
        date: this.job.completed_at,
        icon: this.job.status === 'COMPLETED' ? 'pi pi-check' : 'pi pi-times',
        color,
      });
    }

    return events;
  }
}
