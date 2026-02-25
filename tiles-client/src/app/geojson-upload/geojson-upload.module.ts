import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GeojsonUploadComponent } from './geojson-upload.component';
import { FileUploadModule } from 'primeng/fileupload';
import { PaginatorModule } from 'primeng/paginator';
import { InputNumberModule } from 'primeng/inputnumber';
import { ButtonModule } from 'primeng/button';

@NgModule({
    imports: [
        CommonModule,
        FormsModule,
        FileUploadModule,
        PaginatorModule,
        InputNumberModule,
        ButtonModule,
    ],
    exports: [
        GeojsonUploadComponent
    ],
    declarations: [
        GeojsonUploadComponent
    ]
})
export class GeojsonUploadModule { }
