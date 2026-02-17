import {Component, Input, OnChanges, SimpleChanges} from '@angular/core';
import {ArtifactInfo} from '../../interfaces/embedding-maps.interfaces';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';

@Component({
  selector: 'app-emb-artifacts-list',
  templateUrl: './artifacts-list.component.html',
  styleUrls: ['./artifacts-list.component.scss']
})
export class ArtifactsListComponent implements OnChanges {
  @Input() jobId: string | null = null;

  artifacts: ArtifactInfo[] = [];
  loading = false;

  constructor(private api: EmbeddingMapsApiService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['jobId'] && this.jobId) {
      this.loadArtifacts();
    }
  }

  loadArtifacts(): void {
    if (!this.jobId) return;

    this.loading = true;
    this.api.listArtifacts(this.jobId).subscribe({
      next: res => {
        this.artifacts = res.artifacts;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.artifacts = [];
      }
    });
  }

  getStatusSeverity(status: string): string {
    switch (status) {
      case 'completed': return 'success';
      case 'processing': return 'info';
      case 'failed': return 'danger';
      default: return 'warning';
    }
  }
}
