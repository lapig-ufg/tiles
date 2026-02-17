import {Component, OnInit, OnDestroy, AfterViewInit} from '@angular/core';
import {ActivatedRoute} from '@angular/router';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import OSM from 'ol/source/OSM';
import {fromLonLat, transformExtent} from 'ol/proj';
import {EmbeddingMapsApiService} from '../../services/embedding-maps-api.service';
import {EmbeddingMapsStateService} from '../../services/embedding-maps-state.service';
import {JobResponse, ProductType, PRODUCT_OPTIONS} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-job-visualize',
  templateUrl: './job-visualize.component.html',
  styleUrls: ['./job-visualize.component.scss']
})
export class JobVisualizeComponent implements OnInit, OnDestroy, AfterViewInit {
  job: JobResponse | null = null;
  jobId = '';
  loading = true;
  selectedProduct: ProductType = 'rgb_embedding';

  private map: Map | null = null;
  private embeddingLayer: TileLayer<XYZ> | null = null;

  productOptions = PRODUCT_OPTIONS;

  constructor(
    private route: ActivatedRoute,
    private api: EmbeddingMapsApiService,
    private stateService: EmbeddingMapsStateService,
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.params['id'];
    this.loadJob();
  }

  ngAfterViewInit(): void {
    this.initMap();
  }

  ngOnDestroy(): void {
    if (this.map) {
      this.map.setTarget(undefined);
      this.map = null;
    }
  }

  private loadJob(): void {
    this.loading = true;
    this.api.getJob(this.jobId).subscribe({
      next: job => {
        this.job = job;
        this.stateService.setActiveJob(job);
        this.loading = false;

        // Selecionar primeiro produto disponivel
        if (job.products.length > 0) {
          this.selectedProduct = job.products[0].product;
        }

        this.updateEmbeddingLayer();
        this.fitToBounds();
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  private initMap(): void {
    this.map = new Map({
      target: 'embedding-map',
      layers: [
        new TileLayer({source: new OSM()}),
      ],
      view: new View({
        center: fromLonLat([-49.25, -16.5]),
        zoom: 10,
      }),
    });
  }

  onProductChange(product: ProductType): void {
    this.selectedProduct = product;
    this.stateService.setSelectedProduct(product);
    this.updateEmbeddingLayer();
  }

  private updateEmbeddingLayer(): void {
    if (!this.map || !this.jobId) return;

    // Remover layer anterior
    if (this.embeddingLayer) {
      this.map.removeLayer(this.embeddingLayer);
    }

    const urlTemplate = this.api.getTileUrlTemplate(this.jobId, this.selectedProduct);

    this.embeddingLayer = new TileLayer({
      source: new XYZ({
        url: urlTemplate,
        attributions: `Embedding Maps - ${this.selectedProduct}`,
        attributionsCollapsible: false,
      }),
      opacity: 0.85,
    });

    this.map.addLayer(this.embeddingLayer);
  }

  private fitToBounds(): void {
    if (!this.map || !this.job) return;

    const roi = this.job.config?.['roi'];
    if (roi?.['roi_type'] === 'bbox' && roi['bbox']) {
      const [w, s, e, n] = roi['bbox'];
      const extent = transformExtent([w, s, e, n], 'EPSG:4326', 'EPSG:3857');
      this.map.getView().fit(extent, {padding: [50, 50, 50, 50], maxZoom: 16});
    }
  }

  get availableProducts(): ProductType[] {
    if (!this.job) return [];
    return this.job.products
      .filter(p => p.status === 'COMPLETED')
      .map(p => p.product);
  }
}
