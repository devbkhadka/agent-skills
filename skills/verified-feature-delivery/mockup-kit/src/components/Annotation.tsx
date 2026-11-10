import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { AnnotationItem } from '../types';

interface AnnotationContextType {
  annotations: AnnotationItem[];
  registerAnnotation: (annotation: AnnotationItem) => void;
  activeAnnotationId: string | number | null;
  setActiveAnnotationId: (id: string | number | null) => void;
}

const AnnotationContext = createContext<AnnotationContextType>({
  annotations: [],
  registerAnnotation: () => {},
  activeAnnotationId: null,
  setActiveAnnotationId: () => {},
});

export const AnnotationProvider: React.FC<{
  initialAnnotations?: AnnotationItem[];
  children: React.ReactNode;
}> = ({ initialAnnotations = [], children }) => {
  const [annotations, setAnnotations] = useState<AnnotationItem[]>(initialAnnotations);
  const [activeAnnotationId, setActiveAnnotationId] = useState<string | number | null>(null);

  const registerAnnotation = (item: AnnotationItem) => {
    setAnnotations((prev) => {
      const exists = prev.find((a) => a.number === item.number || a.id === item.id);
      if (exists) {
        return prev.map((a) => (a.number === item.number || a.id === item.id ? { ...a, ...item } : a));
      }
      return [...prev, item].sort((a, b) => a.number - b.number);
    });
  };

  return (
    <AnnotationContext.Provider
      value={{
        annotations,
        registerAnnotation,
        activeAnnotationId,
        setActiveAnnotationId,
      }}
    >
      {children}
    </AnnotationContext.Provider>
  );
};

export const useAnnotations = () => useContext(AnnotationContext);

export interface AnnotationBadgeProps {
  number: number;
  title: string;
  businessIntent: string;
  domainEntityMapping?: string;
  projectionSource?: string;
  subtleRules?: string;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
  inline?: boolean;
  className?: string;
}

export const AnnotationBadge: React.FC<AnnotationBadgeProps> = ({
  number,
  title,
  businessIntent,
  domainEntityMapping,
  projectionSource,
  subtleRules,
  position = 'top-right',
  inline = false,
  className = '',
}) => {
  const { registerAnnotation, activeAnnotationId, setActiveAnnotationId } = useAnnotations();
  const [isHovered, setIsHovered] = useState(false);
  const badgeRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const annotationItem: AnnotationItem = {
    id: `annotation-${number}`,
    number,
    title,
    businessIntent,
    domainEntityMapping,
    projectionSource,
    subtleRules,
  };

  useEffect(() => {
    registerAnnotation(annotationItem);
  }, [number, title, businessIntent, domainEntityMapping, projectionSource, subtleRules]);

  const isActive = activeAnnotationId === number || activeAnnotationId === `annotation-${number}`;
  const showPopover = isHovered || isActive;

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setActiveAnnotationId(isActive ? null : number);
  };

  const badgeClasses = [
    'vfd-annotation-badge',
    inline ? 'vfd-annotation-badge-inline' : `vfd-annotation-badge-overlay ${position}`,
    isActive ? 'is-active' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      ref={badgeRef}
      className={badgeClasses}
      onClick={handleClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      title={`[${number}] ${title}`}
      role="button"
      tabIndex={0}
      aria-label={`Annotation ${number}: ${title}`}
    >
      <span>{number}</span>

      {showPopover && (
        <div
          ref={popoverRef}
          className="vfd-annotation-popover"
          style={{
            top: position.startsWith('top') ? '100%' : 'auto',
            bottom: position.startsWith('bottom') ? '100%' : 'auto',
            right: position.endsWith('right') ? 0 : 'auto',
            left: position.endsWith('left') ? 0 : 'auto',
            marginTop: 8,
            marginBottom: 8,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="vfd-popover-header">
            <span className="vfd-popover-badge-pill">{number}</span>
            <span className="vfd-popover-title">{title}</span>
            {isActive && (
              <button
                className="vfd-popover-close"
                onClick={() => setActiveAnnotationId(null)}
                aria-label="Close annotation popover"
              >
                ✕
              </button>
            )}
          </div>

          <div className="vfd-popover-section">
            <div className="vfd-popover-label">Business Intent & Rationale</div>
            <div className="vfd-popover-value">{businessIntent}</div>
          </div>

          {domainEntityMapping && (
            <div className="vfd-popover-section">
              <div className="vfd-popover-label">Domain Entity / Lexicon Mapping</div>
              <span className="vfd-popover-mono">{domainEntityMapping}</span>
            </div>
          )}

          {projectionSource && (
            <div className="vfd-popover-section">
              <div className="vfd-popover-label">Projection Source / State Transition</div>
              <span className="vfd-popover-mono">{projectionSource}</span>
            </div>
          )}

          {subtleRules && (
            <div className="vfd-popover-section">
              <div className="vfd-popover-label">Subtle Constraints / Edge Cases</div>
              <div className="vfd-popover-value">{subtleRules}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
