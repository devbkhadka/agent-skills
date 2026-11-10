import React, { useState } from 'react';
import { ScreenDefinition, ViewportTag, VIEWPORT_PRESETS } from '../types';
import { AnnotationProvider } from '../components/Annotation';
import { ViewportFrame } from './ViewportFrame';
import { AnnotationInspector } from './AnnotationInspector';

export interface ScreenCatalogueProps {
  featureSlug: string;
  featureTitle: string;
  screens: ScreenDefinition[];
  defaultScreenId?: string;
}

export const ScreenCatalogue: React.FC<ScreenCatalogueProps> = ({
  featureSlug,
  featureTitle,
  screens,
  defaultScreenId,
}) => {
  const [selectedScreenId, setSelectedScreenId] = useState<string>(
    defaultScreenId || (screens.length > 0 ? screens[0].id : '')
  );
  const [customViewport, setCustomViewport] = useState<ViewportTag | null>(null);
  const [zoomScale, setZoomScale] = useState<number>(1);
  const [showInspector, setShowInspector] = useState<boolean>(true);

  const activeScreen = screens.find((s) => s.id === selectedScreenId) || screens[0];
  const activeViewport = customViewport || (activeScreen ? activeScreen.viewport : 'desktop_1280x800');
  const ActiveComponent = activeScreen ? activeScreen.component : null;

  return (
    <div className="vfd-catalogue-root">
      {/* Sidebar: Screen catalogue listing */}
      <aside className="vfd-catalogue-sidebar" aria-label="Screen Catalogue Navigation">
        <div className="vfd-catalogue-header">
          <h1>{featureTitle}</h1>
          <p>Feature: {featureSlug}</p>
        </div>

        <div className="vfd-catalogue-screens-list">
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', marginBottom: '8px' }}>
            Feature Slices & Screens ({screens.length})
          </div>

          {screens.map((screen) => {
            const isSelected = screen.id === selectedScreenId;
            const annotationCount = screen.annotations ? screen.annotations.length : 0;

            return (
              <button
                key={screen.id}
                type="button"
                className={`vfd-screen-card-btn ${isSelected ? 'is-active' : ''}`}
                onClick={() => {
                  setSelectedScreenId(screen.id);
                  setCustomViewport(null); // Reset viewport override on screen change
                }}
              >
                <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '2px' }}>{screen.title}</div>
                <div style={{ fontSize: '0.78rem', color: '#94a3b8', marginBottom: '6px' }}>
                  Slice: {screen.sliceId}
                </div>
                <div className="vfd-screen-meta-row">
                  <span className="vfd-viewport-tag">{screen.viewport}</span>
                  {annotationCount > 0 && (
                    <span className="vfd-annotation-count-pill">
                      {annotationCount} {annotationCount === 1 ? 'rule' : 'rules'}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* Main Viewport Workspace */}
      <main className="vfd-catalogue-viewport-area">
        <div className="vfd-viewport-toolbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontWeight: 600, fontSize: '0.9rem', color: '#ffffff' }}>
              {activeScreen?.title || 'No Screen Selected'}
            </span>
            <span className="vfd-viewport-tag">{activeViewport}</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {/* Viewport Preset Switcher */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem' }}>
              <label htmlFor="viewport-select" style={{ color: '#94a3b8' }}>
                Viewport:
              </label>
              <select
                id="viewport-select"
                value={activeViewport}
                onChange={(e) => setCustomViewport(e.target.value as ViewportTag)}
                style={{
                  backgroundColor: '#0f172a',
                  color: '#ffffff',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  padding: '4px 8px',
                  fontSize: '0.8rem',
                }}
              >
                {Object.values(VIEWPORT_PRESETS).map((preset) => (
                  <option key={preset.tag} value={preset.tag}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Zoom / Scale Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem' }}>
              <label htmlFor="zoom-select" style={{ color: '#94a3b8' }}>
                Scale:
              </label>
              <select
                id="zoom-select"
                value={zoomScale}
                onChange={(e) => setZoomScale(parseFloat(e.target.value))}
                style={{
                  backgroundColor: '#0f172a',
                  color: '#ffffff',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  padding: '4px 8px',
                  fontSize: '0.8rem',
                }}
              >
                <option value={1}>100%</option>
                <option value={0.9}>90%</option>
                <option value={0.8}>80%</option>
                <option value={0.75}>75%</option>
                <option value={0.65}>65%</option>
                <option value={0.5}>50%</option>
              </select>
            </div>

            {/* Inspector Toggle Button */}
            <button
              className="vfd-button vfd-button-secondary vfd-button-sm"
              onClick={() => setShowInspector(!showInspector)}
              style={{
                backgroundColor: showInspector ? '#312e81' : '#0f172a',
                borderColor: showInspector ? '#6366f1' : '#334155',
                color: '#ffffff',
              }}
            >
              Annotations ({activeScreen?.annotations?.length || 0})
            </button>
          </div>
        </div>

        {/* Viewport Canvas Frame with Annotation Provider */}
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <div className="vfd-viewport-canvas">
            {ActiveComponent && (
              <AnnotationProvider initialAnnotations={activeScreen.annotations || []}>
                <ViewportFrame viewport={activeViewport} scale={zoomScale}>
                  <ActiveComponent />
                </ViewportFrame>
              </AnnotationProvider>
            )}
          </div>

          {/* Context Annotation Inspector Side Panel */}
          {showInspector && (
            <AnnotationProvider initialAnnotations={activeScreen?.annotations || []}>
              <AnnotationInspector
                screenAnnotations={activeScreen?.annotations}
                isOpen={showInspector}
                onToggle={() => setShowInspector(false)}
              />
            </AnnotationProvider>
          )}
        </div>
      </main>
    </div>
  );
};
