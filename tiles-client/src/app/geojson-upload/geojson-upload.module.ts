import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import {GeojsonUploadComponent} from "./geojson-upload.component";
import {FileUploadModule} from "primeng/fileupload";
import {PaginatorModule} from "primeng/paginator";
@NgModule({
    imports: [
        CommonModule,
        FileUploadModule,
        PaginatorModule,
    ],
    exports: [
        GeojsonUploadComponent
    ],
    declarations: [
        GeojsonUploadComponent
    ]
})
export class GeojsonUploadModule { }
