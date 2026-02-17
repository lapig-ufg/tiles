import {Component, OnInit} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';
import {
  JobResponse,
  ProductType,
  ExportFormat,
  ExportRequest,
  PRODUCT_OPTIONS,
} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-job-export',
  templateUrl: './job-export.component.html',
  styleUrls: ['./job-export.component.scss']
})
export class JobExportComponent implements OnInit {
  job: JobResponse | null = null;
  jobId = '';
  loading = true;
  exporting = false;
  exportSuccess = false;

  // Export config
  selectedProducts: ProductType[] = [];
  selectedFormats: ExportFormat[] = [];
  exportScale: number | null = null;

  formatOptions: {label: string; value: ExportFormat}[] = [
    {label: 'Cloud Optimized GeoTIFF', value: 'COG'},
    {label: 'GeoTIFF', value: 'GeoTIFF'},
    {label: 'CSV', value: 'CSV'},
    {label: 'Parquet', value: 'Parquet'},
    {label: 'JSON', value: 'JSON'},
  ];

  productOptions = PRODUCT_OPTIONS;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private api: EmbeddingMapsApiService,
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.params['id'];
    this.loadJob();
  }

  private loadJob(): void {
    this.loading = true;
    this.api.getJob(this.jobId).subscribe({
      next: job => {
        this.job = job;
        this.loading = false;
        // Pre-selecionar produtos completados
        this.selectedProducts = job.products
          .filter(p => p.status === 'COMPLETED')
          .map(p => p.product);
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  isProductSelected(product: ProductType): boolean {
    return this.selectedProducts.includes(product);
  }

  toggleProduct(product: ProductType): void {
    if (this.isProductSelected(product)) {
      this.selectedProducts = this.selectedProducts.filter(p => p !== product);
    } else {
      this.selectedProducts.push(product);
    }
  }

  isFormatSelected(format: ExportFormat): boolean {
    return this.selectedFormats.includes(format);
  }

  toggleFormat(format: ExportFormat): void {
    if (this.isFormatSelected(format)) {
      this.selectedFormats = this.selectedFormats.filter(f => f !== format);
    } else {
      this.selectedFormats.push(format);
    }
  }

  startExport(): void {
    if (this.selectedProducts.length === 0 || this.selectedFormats.length === 0) return;

    const req: ExportRequest = {
      products: this.selectedProducts,
      formats: this.selectedFormats,
      scale: this.exportScale ?? undefined,
      export_target: 's3',
    };

    this.exporting = true;
    this.exportSuccess = false;

    this.api.requestExport(this.jobId, req).subscribe({
      next: () => {
        this.exporting = false;
        this.exportSuccess = true;
      },
      error: () => {
        this.exporting = false;
      }
    });
  }

  get availableProducts(): ProductType[] {
    if (!this.job) return [];
    return this.job.products
      .filter(p => p.status === 'COMPLETED')
      .map(p => p.product);
  }
}
