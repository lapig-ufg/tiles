import { Component, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { CalendarModule } from 'primeng/calendar';
import { DropdownModule } from 'primeng/dropdown';
import { MultiSelectModule } from 'primeng/multiselect';
import { QueryableProperty } from '../../../stac/models/stac.models';

export interface QueryableFilterValues {
  [key: string]: any;
}

interface DynamicField {
  key: string;
  label: string;
  type: string;
  format?: string;
  enumOptions?: { label: string; value: any }[];
  minimum?: number;
  maximum?: number;
}

@Component({
  selector: 'app-queryable-filter',
  standalone: true,
  imports: [CommonModule, FormsModule, InputTextModule, InputNumberModule, CalendarModule, DropdownModule, MultiSelectModule],
  template: `
    <div class="queryable-filters flex flex-column gap-2">
      <div *ngFor="let field of dynamicFields" class="flex align-items-center gap-2">
        <label class="text-sm white-space-nowrap" style="min-width: 120px">{{ field.label }}:</label>

        <!-- String with enum -> Dropdown -->
        <p-dropdown *ngIf="field.enumOptions && field.type === 'string'"
                    [options]="field.enumOptions" [(ngModel)]="values[field.key]"
                    optionLabel="label" optionValue="value"
                    [showClear]="true" placeholder="Selecione..."
                    styleClass="w-full text-sm"
                    (onChange)="onValueChange()">
        </p-dropdown>

        <!-- String array with enum -> MultiSelect -->
        <p-multiSelect *ngIf="field.enumOptions && field.type === 'array'"
                       [options]="field.enumOptions" [(ngModel)]="values[field.key]"
                       optionLabel="label" optionValue="value"
                       placeholder="Selecione..."
                       styleClass="w-full text-sm"
                       (onChange)="onValueChange()">
        </p-multiSelect>

        <!-- Number -->
        <p-inputNumber *ngIf="!field.enumOptions && (field.type === 'number' || field.type === 'integer')"
                       [(ngModel)]="values[field.key]"
                       [min]="field.minimum" [max]="field.maximum"
                       inputStyleClass="w-full p-inputtext-sm"
                       (onInput)="onValueChange()">
        </p-inputNumber>

        <!-- Date-time -->
        <p-calendar *ngIf="field.format === 'date-time'"
                    [(ngModel)]="values[field.key]" dateFormat="yy-mm-dd"
                    [showIcon]="true" inputStyleClass="w-full p-inputtext-sm"
                    (onClose)="onValueChange()">
        </p-calendar>

        <!-- Plain string (no enum, no special format) -->
        <input *ngIf="!field.enumOptions && field.type === 'string' && field.format !== 'date-time'"
               pInputText [(ngModel)]="values[field.key]"
               class="p-inputtext-sm w-full"
               (input)="onValueChange()">
      </div>

      <div *ngIf="dynamicFields.length === 0" class="text-sm text-color-secondary">
        Selecione uma coleção para ver os filtros disponíveis.
      </div>
    </div>
  `,
})
export class QueryableFilterComponent implements OnChanges {
  @Input() queryables: Record<string, QueryableProperty> = {};
  @Input() summaries: Record<string, any> = {};
  @Output() valuesChange = new EventEmitter<QueryableFilterValues>();

  dynamicFields: DynamicField[] = [];
  values: QueryableFilterValues = {};

  // Properties to skip (handled separately by the parent component)
  private skipProperties = ['datetime', 'geometry', 'bbox', 'id', 'collection'];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['queryables'] || changes['summaries']) {
      this.buildFields();
    }
  }

  private buildFields(): void {
    this.dynamicFields = [];
    this.values = {};

    for (const [key, prop] of Object.entries(this.queryables)) {
      if (this.skipProperties.includes(key)) continue;

      const field: DynamicField = {
        key,
        label: prop.title || key,
        type: prop.type,
        format: prop.format,
        minimum: prop.minimum,
        maximum: prop.maximum,
      };

      // Check for enum values (from queryable or summaries)
      const enumValues = prop.enum || this.summaries[key];
      if (enumValues && Array.isArray(enumValues)) {
        field.enumOptions = enumValues.map((v: any) => ({ label: String(v), value: v }));
      }

      this.dynamicFields.push(field);
    }
  }

  onValueChange(): void {
    const nonEmpty: QueryableFilterValues = {};
    for (const [key, value] of Object.entries(this.values)) {
      if (value !== null && value !== undefined && value !== '') {
        nonEmpty[key] = value;
      }
    }
    this.valuesChange.emit(nonEmpty);
  }
}
