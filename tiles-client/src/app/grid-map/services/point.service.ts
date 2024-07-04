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

  setPoint(point: Point): void {
    this.point.next(point);
  }
}
