import React, { useEffect } from 'react';

export interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  placement?: 'left' | 'right' | 'bottom';
  closeOnBackdropClick?: boolean;
  children: React.ReactNode;
}

export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  placement = 'right',
  closeOnBackdropClick = true,
  children,
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="vfd-overlay-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget && closeOnBackdropClick) {
          onClose();
        }
      }}
    >
      <div className={`vfd-drawer vfd-drawer-${placement}`} role="dialog" aria-modal="true">
        {title && (
          <div className="vfd-modal-header">
            <h3 className="vfd-modal-title">{title}</h3>
            <button className="vfd-button vfd-button-ghost vfd-button-sm" onClick={onClose} aria-label="Close drawer">
              ✕
            </button>
          </div>
        )}
        <div className="vfd-modal-body">{children}</div>
      </div>
    </div>
  );
};
