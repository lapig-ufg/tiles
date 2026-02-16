import {NgModule} from '@angular/core';
import {RouterModule} from '@angular/router';
import {ImageCatalogComponent} from './image-catalog.component';

@NgModule({
    imports: [RouterModule.forChild([
        {path: '', component: ImageCatalogComponent}
    ])],
    exports: [RouterModule]
})
export class ImageCatalogRoutingModule {}
