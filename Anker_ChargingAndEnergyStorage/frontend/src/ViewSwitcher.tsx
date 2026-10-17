export type EnergyView = 'family' | 'professional' | 'assistant' | 'history' | 'sample';
const views = [{ value: 'family', label: '家庭视图' }, { value: 'professional', label: '专业视图' }, { value: 'assistant', label: 'Agent' }] as const;

export function initialView(): EnergyView {
  const requested = new URLSearchParams(location.search).get('view');
  if (['family', 'professional', 'assistant', 'history', 'sample'].includes(requested ?? '')) return requested as EnergyView;
  return 'family';
}

export default function ViewSwitcher({ value, onChange }: { value: EnergyView; onChange: (view: EnergyView) => void }) {
  return <div className="view-switcher" role="group" aria-label="选择信息展示方式">
    {views.map(view => <button key={view.value} type="button" aria-pressed={value === view.value} onClick={() => onChange(view.value)}>{view.label}</button>)}
  </div>;
}
