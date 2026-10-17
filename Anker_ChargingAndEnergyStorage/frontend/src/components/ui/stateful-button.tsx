// Adapted from Aceternity UI's public Stateful Button registry (2026-10-16).
// https://ui.aceternity.com/registry/stateful-button.json
// Adds real error state, duplicate-click prevention and reduced-motion support.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { CheckIcon, DownloadSimpleIcon, WarningCircleIcon } from '@phosphor-icons/react';
import { cn } from '../../lib/utils';

export function StatefulButton({ onAction, disabled, className, children }: {
  onAction: () => void | Promise<void>; disabled?: boolean; className?: string; children: ReactNode;
}) {
  const [state, setState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reduced = useReducedMotion();
  useEffect(() => () => clearTimeout(timer.current), []);
  return <motion.button type="button" className={cn('button stateful-button', className)}
    disabled={disabled || state === 'loading'} aria-busy={state === 'loading'}
    whileTap={reduced ? undefined : { scale: .98 }}
    onClick={async () => {
      if (state === 'loading') return;
      clearTimeout(timer.current); setState('loading');
      try { await onAction(); setState('success'); }
      catch { setState('error'); }
      timer.current = setTimeout(() => setState('idle'), 2200);
    }}>
    <AnimatePresence mode="wait" initial={false}>
      <motion.span className="stateful-button-content" key={state}
        initial={reduced ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
        exit={reduced ? undefined : { opacity: 0, y: -4 }} transition={{ duration: reduced ? 0 : .14 }}>
        {state === 'success' ? <CheckIcon size={17}/> : state === 'error' ? <WarningCircleIcon size={17}/> : <DownloadSimpleIcon size={17}/>}
        {state === 'success' ? '总结已导出' : state === 'error' ? '导出失败，请重试' : state === 'loading' ? '正在整理' : children}
      </motion.span>
    </AnimatePresence>
  </motion.button>;
}
