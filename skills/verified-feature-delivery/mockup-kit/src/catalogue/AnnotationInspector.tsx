import React from 'react';
import { useAnnotations } from '../components/Annotation';
import { AnnotationItem } from '../types';

export interface AnnotationInspectorProps {
  screenAnnotations?: AnnotationItem[];
  isOpen?: boolean;
  onToggle?: () => void;
}

export const AnnotationInspector: React.FC<AnnotationInspectorProps> = ({
  screenAnnotations,
  isOpen = true,
  onToggle,
}) => {
  const { annotations: contextAnnotations, activeAnnotationId, setActiveAnnotationId } = useAnnotations();
  const annotations = screenAnnotations && screenAnnotations.length > 0 ? screenAnnotations : contextAnnotations;

  if (!isOpen) return null;

  return (
    <aside className="vfd-annotation-inspector" aria-label="Targeted Context Annotations Inspector">
      <div className="vfd-inspector-header">
        <div>
          <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: 0, color: '#ffffff' }}>
            Domain Annotations ({annotations.length})
          </h2>
          <p style={{ fontSize: '0.75rem', margin: '2px 0 0 0', color: '#94a3b8' }}>
            Selective context & subtle business rules
          </p>
        </div>
        {onToggle && (
          <button
            className="vfd-button vfd-button-ghost vfd-button-sm"
            onClick={onToggle}
            style={{ color: '#94a3b8' }}
          >
            ✕
          </button>
        )}
      </div>

      <div className="vfd-inspector-body">
        {annotations.length === 0 ? (
          <div style={{ color: '#94a3b8', fontSize: '0.85rem', textAlign: 'center', padding: '30px 10px' }}>
            No non-obvious annotations declared on this screen.
          </div>
        ) : (
          annotations.map((item) => {
            const isSelected = activeAnnotationId === item.number || activeAnnotationId === item.id;

            return (
              <div
                key={item.id || item.number}
                className={`vfd-inspector-item ${isSelected ? 'is-selected' : ''}`}
                onClick={() => setActiveAnnotationId(isSelected ? null : item.number)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                  <span className="vfd-popover-badge-pill">{item.number}</span>
                  <span style={{ fontWeight: 600, fontSize: '0.88rem', color: '#ffffff' }}>{item.title}</span>
                </div>

                <div style={{ fontSize: '0.82rem', color: '#cbd5e1', marginBottom: '8px', lineHeight: 1.4 }}>
                  {item.businessIntent}
                </div>

                {item.domainEntityMapping && (
                  <div style={{ marginTop: '6px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 700 }}>
                      Entity Mapping
                    </div>
                    <span className="vfd-popover-mono" style={{ backgroundColor: '#0f172a', color: '#a5b4fc' }}>
                      {item.domainEntityMapping}
                    </span>
                  </div>
                )}

                {item.projectionSource && (
                  <div style={{ marginTop: '6px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 700 }}>
                      Projection Source
                    </div>
                    <span className="vfd-popover-mono" style={{ backgroundColor: '#0f172a', color: '#a5b4fc' }}>
                      {item.projectionSource}
                    </span>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
};
