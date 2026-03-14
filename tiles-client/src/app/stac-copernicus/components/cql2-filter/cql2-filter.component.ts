import { Component, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DropdownModule } from 'primeng/dropdown';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { CalendarModule } from 'primeng/calendar';
import { QueryableProperty } from '../../../stac/models/stac.models';

export interface CqlFilterRow {
  property: string;
  operator: string;
  value: any;
}

export interface Cql2Filter {
  op: string;
  args: any[];
}

@Component({
  selector: 'app-cql2-filter',
  standalone: true,
  imports: [CommonModule, FormsModule, ButtonModule, DropdownModule, InputTextModule, InputNumberModule, CalendarModule],
  templateUrl: './cql2-filter.component.html',
})
export class Cql2FilterComponent implements OnChanges {
  @Input() queryables: Record<string, QueryableProperty> = {};
  @Output() filterChange = new EventEmitter<Cql2Filter | null>();

  rows: CqlFilterRow[] = [];

  propertyOptions: { label: string; value: string }[] = [];

  operatorOptions = [
    { label: '=', value: '=' },
    { label: '!=', value: '!=' },
    { label: '<', value: '<' },
    { label: '>', value: '>' },
    { label: '<=', value: '<=' },
    { label: '>=', value: '>=' },
    { label: 'LIKE', value: 'like' },
  ];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['queryables']) {
      this.propertyOptions = Object.entries(this.queryables).map(([key, prop]) => ({
        label: prop.title || key,
        value: key,
      }));
    }
  }

  addRow(): void {
    this.rows.push({
      property: this.propertyOptions[0]?.value || '',
      operator: '=',
      value: null,
    });
  }

  removeRow(index: number): void {
    this.rows.splice(index, 1);
    this.emitFilter();
  }

  getPropertyType(propertyName: string): string {
    return this.queryables[propertyName]?.type || 'string';
  }

  getPropertyFormat(propertyName: string): string {
    return this.queryables[propertyName]?.format || '';
  }

  onValueChange(): void {
    this.emitFilter();
  }

  private emitFilter(): void {
    const validRows = this.rows.filter(r => r.property && r.value !== null && r.value !== '');

    if (validRows.length === 0) {
      this.filterChange.emit(null);
      return;
    }

    const args = validRows.map(row => ({
      op: row.operator,
      args: [
        { property: row.property },
        row.value,
      ],
    }));

    if (args.length === 1) {
      this.filterChange.emit(args[0] as Cql2Filter);
    } else {
      this.filterChange.emit({
        op: 'and',
        args,
      });
    }
  }
}
