import {Component, Input, OnChanges, SimpleChanges} from '@angular/core';
import {StatsResponse, ProductType} from '../../interfaces/embedding-maps.interfaces';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';

@Component({
  selector: 'app-emb-stats-panel',
  templateUrl: './stats-panel.component.html',
  styleUrls: ['./stats-panel.component.scss']
})
export class StatsPanelComponent implements OnChanges {
  @Input() jobId: string | null = null;
  @Input() product: ProductType = 'rgb_embedding';

  stats: StatsResponse | null = null;
  loading = false;
  error: string | null = null;

  constructor(private api: EmbeddingMapsApiService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if ((changes['jobId'] || changes['product']) && this.jobId) {
      this.loadStats();
    }
  }

  loadStats(): void {
    if (!this.jobId) return;

    this.loading = true;
    this.error = null;

    this.api.getStats(this.jobId, this.product).subscribe({
      next: stats => {
        this.stats = stats;
        this.loading = false;
      },
      error: err => {
        this.error = err.error?.detail || 'Erro ao carregar estatisticas';
        this.loading = false;
      }
    });
  }
}
