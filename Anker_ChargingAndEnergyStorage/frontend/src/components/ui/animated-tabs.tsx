// Adapted from Aceternity UI's public Tabs registry (retrieved 2026-10-16).
// https://ui.aceternity.com/registry/tabs.json
// Controlled selection, unique layout IDs, keyboard navigation and reduced motion
// replace the demo's stacked cards; the existing energy view stays the only truth.
import { useId } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { cn } from '../../lib/utils';

type Tab = { title: string; value: string };
export function AnimatedTabs({ tabs, value, onChange, className }: {
  tabs: Tab[]; value: string; onChange: (value: string) => void; className?: string;
}) {
  const group = useId();
  const reduced = useReducedMotion();
  return <div className={cn('family-tabs', className)} role="tablist" aria-label="选择家庭">
    {tabs.map((tab, index) => <button key={tab.value} type="button" role="tab"
      aria-selected={value === tab.value} aria-controls="energy-content"
      id={`family-${tab.value}`} tabIndex={value === tab.value ? 0 : -1}
      className="family-tab" onClick={() => onChange(tab.value)}
      onKeyDown={event => {
        let target = index;
        if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') target = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') target = 0;
        else if (event.key === 'End') target = tabs.length - 1;
        else return;
        event.preventDefault(); onChange(tabs[target].value);
        document.getElementById(`family-${tabs[target].value}`)?.focus();
      }}>
      {value === tab.value && <motion.span className="family-tab-highlight" layoutId={`family-tab-${group}`}
        transition={reduced ? { duration: 0 } : { type: 'spring', stiffness: 420, damping: 38 }}/>}
      <span className="family-tab-label">{tab.title}</span>
    </button>)}
  </div>;
}
