export interface MapItem {
    year: number;
    id: string;
}

export interface PeriodMapItem extends MapItem {
    period: string;
}

export interface MonthMapItem extends MapItem {
    month: string;
}
