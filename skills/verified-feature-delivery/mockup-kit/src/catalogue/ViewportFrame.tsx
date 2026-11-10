import React from 'react';
import { ViewportTag, VIEWPORT_PRESETS } from '../types';

export interface ViewportFrameProps {
  viewport: ViewportTag;
  scale?: number;
  showFrame?: boolean;
  children: React.ReactNode;
}

export const ViewportFrame: React.FC<ViewportFrameProps> = ({
  viewport,
  scale = 1,
  showFrame = true,
  children,
}) => {
  const preset = VIEWPORT_PRESETS[viewport] || VIEWPORT_PRESETS.responsive;
  const isResponsive = preset.tag === 'responsive';

  const frameStyle: React.CSSProperties = {
    width: preset.width,
    height: preset.height,
    maxWidth: isResponsive ? '100%' : undefined,
    transform: scale !== 1 ? `scale(${scale})` : undefined,
    transformOrigin: 'top center',
  };

  return (
    <div className={`vfd-viewport-frame ${showFrame ? 'has-bezel' : ''}`} style={frameStyle}>
      {children}
    </div>
  );
};
