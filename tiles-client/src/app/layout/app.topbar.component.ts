import { Component, ElementRef, ViewChild } from '@angular/core';
import { LayoutService } from 'src/app/layout/service/app.layout.service';
import {PointService} from "../grid-map/services/point.service";
import {Observable} from "rxjs";

@Component({
    selector: 'app-topbar',
    templateUrl: './app.topbar.component.html'
})
export class AppTopbarComponent {
    latitude!:number;
    longitude!:number;
    public pointInfo$: Observable<any> = this.pointService.pointInfo$;

    @ViewChild('menubutton') menuButton!: ElementRef;

    constructor(public layoutService: LayoutService, public pointService: PointService) { }

    onMenuButtonClick() {
        this.layoutService.onMenuToggle();
    }

    onProfileButtonClick() {
        this.layoutService.showProfileSidebar();
    }
    onConfigButtonClick() {
        this.layoutService.showConfigSidebar();
    }
    onSearchPoint(){
        this.pointService.setPoint({lat: this.latitude, lon: this.longitude})
    }

}
