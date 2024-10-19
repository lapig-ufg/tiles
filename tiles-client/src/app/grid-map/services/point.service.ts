import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

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

  setPointInfo(info: any): void {
    this.pointInfo.next(info);
  }

  setPoint(point: Point): void {
    this.point.next(point);
  }
}
