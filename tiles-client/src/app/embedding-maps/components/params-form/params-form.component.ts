import {Component, EventEmitter, Input, Output} from '@angular/core';
import {YEAR_OPTIONS, PRESET_CONFIGS} from '../../interfaces/embedding-maps.interfaces';

@Component({
  selector: 'app-emb-params-form',
  templateUrl: './params-form.component.html',
  styleUrls: ['./params-form.component.scss']
})
export class ParamsFormComponent {
  @Input() name = '';
  @Input() description = '';
  @Input() selectedYear = 2023;
  @Input() selectedScale = 10;
  @Input() selectedPreset = 'STANDARD';
  @Input() sampleSize = 5000;

  @Output() nameChange = new EventEmitter<string>();
  @Output() descriptionChange = new EventEmitter<string>();
  @Output() selectedYearChange = new EventEmitter<number>();
  @Output() selectedScaleChange = new EventEmitter<number>();
  @Output() selectedPresetChange = new EventEmitter<string>();
  @Output() sampleSizeChange = new EventEmitter<number>();

  yearOptions = YEAR_OPTIONS;
  presetOptions = [
    {label: 'Rapido', value: 'FAST'},
    {label: 'Padrao', value: 'STANDARD'},
    {label: 'Detalhado', value: 'DETAILED'},
  ];

  onPresetChange(preset: string): void {
    this.selectedPreset = preset;
    this.selectedPresetChange.emit(preset);

    const cfg = (PRESET_CONFIGS as any)[preset];
    if (cfg) {
      this.selectedScale = cfg.scale;
      this.selectedScaleChange.emit(cfg.scale);
      this.sampleSize = cfg.sample_size;
      this.sampleSizeChange.emit(cfg.sample_size);
    }
  }
}
