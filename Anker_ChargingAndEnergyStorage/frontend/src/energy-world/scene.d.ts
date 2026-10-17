import type { EnergyDevice, EnergyWorldFrame } from './types';

export type WorldHandle = {
  update: (frame: EnergyWorldFrame | null, selected: EnergyDevice | null, reducedMotion: boolean) => void;
  reset: () => void;
  zoom: (direction: number) => void;
  stats: () => { frames: number; triangles: number; drawCalls: number; pixelRatio: number };
  dispose: () => void;
};

export function createWorld(
  container: HTMLDivElement,
  onSelect: (device: EnergyDevice) => void,
  onFailure: (reason: 'context' | 'performance') => void,
  onAssetState: (state: 'loading' | 'lux3d' | 'unavailable') => void,
): WorldHandle;
