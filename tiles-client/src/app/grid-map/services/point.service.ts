import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { Feature } from 'ol';
import { Geometry } from 'ol/geom';

export interface Point {
  lat?: number;
  lon?: number;
}

@Injectable({
  providedIn: 'root',
})
export class PointService {
  private point = new BehaviorSubject<Point>({ lat: undefined, lon: undefined });
  point$ = this.point.asObservable();
  private pointInfo = new BehaviorSubject<any>(null);
  pointInfo$ = this.pointInfo.asObservable();
  private activeFeature = new BehaviorSubject<Feature<Geometry> | null>(null);
  activeFeature$ = this.activeFeature.asObservable();

  setPointInfo(info: any): void {
    this.pointInfo.next(info);
  }

  setPoint(point: Point): void {
    this.point.next(point);
  }

  setActiveFeature(feature: Feature<Geometry> | null): void {
    this.activeFeature.next(feature);
  }
}
