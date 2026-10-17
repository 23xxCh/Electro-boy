import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { useReducedMotion } from 'motion/react';

// The package's public component export avoids downloading the entire catalog.
const ThreeUIArc = lazy(() => import('@designcodeio/threeui/components/PredictiveArcCanvas').then(module => ({ default: module.PredictiveArcCanvas })));

class CanvasBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? null : this.props.children; }
}

/** Ambient light texture only. All measured values remain in HTML/SVG above it. */
export default function EnergyAtmosphere() {
  const reduced = useReducedMotion();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const media = window.matchMedia('(min-width: 768px)');
    const update = () => setReady(media.matches);
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return <div className="energy-atmosphere" aria-hidden="true" data-renderer={ready && !reduced ? 'threeui-predictive-arc' : 'static-fallback'}>
    {ready && !reduced && <CanvasBoundary><Suspense fallback={null}>
      <ThreeUIArc mode="light" speed={.18} spacing={9} dotSize={2} archHeight={1.4} thickness={.65} brightness={.5} hue={32} saturation={.25}/>
    </Suspense></CanvasBoundary>}
  </div>;
}
