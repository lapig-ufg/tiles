import {Component, EventEmitter, Input, Output} from '@angular/core';
import {ProductConfig, ProductType, PRODUCT_OPTIONS, ProductOption} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-product-selector',
  templateUrl: './product-selector.component.html',
  styleUrls: ['./product-selector.component.scss']
})
export class ProductSelectorComponent {
  @Input() multiSelect = true;
  @Input() selectedProducts: ProductType[] = ['rgb_embedding'];
  @Input() yearB: number | null = null;
  @Input() rgbBands: number[] = [0, 16, 9];
  @Input() pcaComponents = 3;
  @Input() kmeansK = 8;

  @Output() selectedProductsChange = new EventEmitter<ProductType[]>();
  @Output() yearBChange = new EventEmitter<number | null>();
  @Output() rgbBandsChange = new EventEmitter<number[]>();
  @Output() pcaComponentsChange = new EventEmitter<number>();
  @Output() kmeansKChange = new EventEmitter<number>();

  productOptions = PRODUCT_OPTIONS;

  isSelected(product: ProductType): boolean {
    return this.selectedProducts.includes(product);
  }

  toggleProduct(product: ProductType): void {
    if (this.multiSelect) {
      if (this.isSelected(product)) {
        this.selectedProducts = this.selectedProducts.filter(p => p !== product);
      } else {
        this.selectedProducts = [...this.selectedProducts, product];
      }
    } else {
      this.selectedProducts = [product];
    }
    this.selectedProductsChange.emit(this.selectedProducts);
  }

  getProducts(): ProductConfig[] {
    return this.selectedProducts.map(p => {
      const cfg: ProductConfig = {product: p};
      if (p === 'rgb_embedding') {
        cfg.rgb_bands = this.rgbBands;
      } else if (p === 'pca') {
        cfg.pca_components = this.pcaComponents;
      } else if (p === 'clusters') {
        cfg.kmeans_k = this.kmeansK;
      } else if (p === 'change_detection') {
        cfg.year_b = this.yearB ?? undefined;
      }
      return cfg;
    });
  }
}
