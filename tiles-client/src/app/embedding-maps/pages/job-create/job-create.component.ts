import {Component, OnInit, OnDestroy, ViewChild} from '@angular/core';
import {ActivatedRoute, Router} from '@angular/router';
import {ScreenStateConfig, ScreenStateBinder} from '../../../screen-state/interfaces/screen-state.interfaces';
import {ScreenStateService} from '../../../screen-state/services/screen-state.service';
import {bindState} from '../../../screen-state/helpers/manual-state.helper';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';
import {EmbeddingMapsStateService} from '../../services/embedding-maps-state.service';
import {
  JobResponse,
  JobCreateRequest,
  ProductType,
  RoiType,
  RoiConfig,
  PRESET_CONFIGS,
} from '../../interfaces/embedding-maps.interfaces';
import {RoiSelectorComponent} from '../../components/roi-selector/roi-selector.component';
import {ProductSelectorComponent} from '../../components/product-selector/product-selector.component';

const JOB_CREATE_STATE_CONFIG: ScreenStateConfig = {
  screenKey: 'embedding-maps-create',
  group: 'embedding-maps',
  strategy: 'storage-only',
  debounceMs: 400,
  ttlMs: 30 * 24 * 60 * 60 * 1000,
  schemaVersion: 1,
  fields: {
    selectedYear:   {type: 'number', defaultValue: 2023},
    roiMode:        {type: 'string', defaultValue: 'bbox'},
    selectedPreset: {type: 'string', defaultValue: 'STANDARD'},
    selectedScale:  {type: 'number', defaultValue: 10},
  }
};

@Component({
  selector: 'app-emb-job-create',
  templateUrl: './job-create.component.html',
  styleUrls: ['./job-create.component.scss']
})
export class JobCreateComponent implements OnInit, OnDestroy {
  @ViewChild('roiSelector') roiSelector!: RoiSelectorComponent;
  @ViewChild('productSelector') productSelector!: ProductSelectorComponent;

  // Form state
  name = '';
  description = '';
  selectedYear = 2023;
  selectedScale = 10;
  selectedPreset = 'STANDARD';
  sampleSize = 5000;
  roiMode: RoiType = 'bbox';
  selectedProducts: ProductType[] = ['rgb_embedding'];
  rgbBands: number[] = [0, 16, 9];
  pcaComponents = 3;
  kmeansK = 8;
  yearB: number | null = null;

  // Job list
  recentJobs: JobResponse[] = [];
  loadingJobs = false;
  creating = false;

  private stateBinder!: ScreenStateBinder<any>;

  constructor(
    private api: EmbeddingMapsApiService,
    private stateService: EmbeddingMapsStateService,
    private route: ActivatedRoute,
    private router: Router,
    private screenStateService: ScreenStateService,
  ) {}

  ngOnInit(): void {
    this.stateBinder = bindState(
      JOB_CREATE_STATE_CONFIG,
      this.route,
      this.router,
      this.screenStateService,
      {
        selectedYear: 2023,
        roiMode: 'bbox',
        selectedPreset: 'STANDARD',
        selectedScale: 10,
      }
    );

    const restored = this.stateBinder.restore();
    this.selectedYear = restored.selectedYear;
    this.roiMode = restored.roiMode;
    this.selectedPreset = restored.selectedPreset;
    this.selectedScale = restored.selectedScale;

    const preset = (PRESET_CONFIGS as any)[this.selectedPreset];
    if (preset) {
      this.sampleSize = preset.sample_size;
    }

    this.loadRecentJobs();
  }

  ngOnDestroy(): void {
    this.stateBinder.destroy();
  }

  onFilterChange(): void {
    this.stateBinder.patchAndPersist({
      selectedYear: this.selectedYear,
      roiMode: this.roiMode,
      selectedPreset: this.selectedPreset,
      selectedScale: this.selectedScale,
    });
  }

  loadRecentJobs(): void {
    this.loadingJobs = true;
    this.api.listJobs(10, 0).subscribe({
      next: res => {
        this.recentJobs = res.items;
        this.loadingJobs = false;
      },
      error: () => {
        this.loadingJobs = false;
      }
    });
  }

  createJob(): void {
    if (!this.name.trim()) return;

    const roi = this.roiSelector.getRoi();
    const products = this.productSelector.getProducts();

    const request: JobCreateRequest = {
      name: this.name,
      description: this.description || undefined,
      year: this.selectedYear,
      roi,
      processing: {
        scale: this.selectedScale,
        crs: 'EPSG:4326',
        tile_scale: 4,
        best_effort: true,
        max_pixels: 1_000_000_000,
        sample_size: this.sampleSize,
      },
      products,
    };

    this.creating = true;
    this.api.createJob(request).subscribe({
      next: job => {
        this.creating = false;
        this.router.navigate(['embedding', 'jobs', job.id]);
      },
      error: () => {
        this.creating = false;
      }
    });
  }

  navigateToJob(job: JobResponse): void {
    this.router.navigate(['embedding', 'jobs', job.id]);
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
}
