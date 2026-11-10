import React, { useState, useRef, useEffect } from 'react';

export interface DropdownItem {
  id: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  danger?: boolean;
}

export interface DropdownProps {
  trigger: React.ReactNode;
  items: DropdownItem[];
  placement?: 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end';
  className?: string;
}

export const Dropdown: React.FC<DropdownProps> = ({
  trigger,
  items,
  placement = 'bottom-start',
  className = '',
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    }
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [isOpen]);

  const getPlacementStyle = (): React.CSSProperties => {
    switch (placement) {
      case 'bottom-end':
        return { top: '100%', right: 0, marginTop: 4 };
      case 'top-start':
        return { bottom: '100%', left: 0, marginBottom: 4 };
      case 'top-end':
        return { bottom: '100%', right: 0, marginBottom: 4 };
      case 'bottom-start':
      default:
        return { top: '100%', left: 0, marginTop: 4 };
    }
  };

  return (
    <div
      ref={containerRef}
      className={`vfd-dropdown-container ${className}`.trim()}
      style={{ position: 'relative', display: 'inline-block' }}
    >
      <div onClick={() => setIsOpen(!isOpen)} style={{ display: 'inline-block' }}>
        {trigger}
      </div>
      {isOpen && (
        <div className="vfd-dropdown-menu" style={getPlacementStyle()} role="menu">
          {items.map((item) => (
            <div
              key={item.id}
              className={`vfd-dropdown-item ${item.danger ? 'is-danger' : ''}`}
              role="menuitem"
              onClick={() => {
                if (!item.disabled) {
                  if (item.onClick) item.onClick();
                  setIsOpen(false);
                }
              }}
              style={{
                opacity: item.disabled ? 0.5 : 1,
                cursor: item.disabled ? 'not-allowed' : 'pointer',
                color: item.danger ? 'var(--vfd-status-danger-text)' : undefined,
              }}
            >
              {item.icon && <span>{item.icon}</span>}
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
