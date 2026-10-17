export type EnergyDevice = 'pv' | 'battery' | 'grid';

export type EnergyWorldFrame = {
  viewVersion: string | null;
  kind: 'observation' | 'preview' | 'actual';
  timeLabel: string;
  interval: {
    slot: number | null;
    start: string | null;
    end: string | null;
    measurementSlot: number | null;
    socBoundarySlot: number | null;
  };
  paused: boolean | null;
  pvKw: number | null;
  loadKw: number | null;
  batteryKw: number | null;
  batteryTargetKw: number | null;
  gridImportKw: number | null;
  gridExportKw: number | null;
  socPercent: number | null;
  prices: {
    buyEurKwh: number | null;
    sellEurKwh: number | null;
    sourceSlot: number | null;
    kind: string | null;
  };
  motion: 'none' | 'preview' | 'receipt_replay';
};

export type EnergyWorldProps = {
  frame: EnergyWorldFrame | null;
  selected: EnergyDevice | null;
  onSelect: (device: EnergyDevice) => void;
  reducedMotion: boolean;
};
