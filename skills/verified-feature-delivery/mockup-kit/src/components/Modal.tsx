import React, { useEffect } from 'react';
import { Button } from '../primitives/Button';

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  footer?: React.ReactNode;
  closeOnBackdropClick?: boolean;
  children: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  size = 'md',
  footer,
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
      <div className={`vfd-modal vfd-modal-${size}`} role="dialog" aria-modal="true">
        {title && (
          <div className="vfd-modal-header">
            <h2 className="vfd-modal-title">{title}</h2>
            <button className="vfd-button vfd-button-ghost vfd-button-sm" onClick={onClose} aria-label="Close modal">
              ✕
            </button>
          </div>
        )}
        <div className="vfd-modal-body">{children}</div>
        {footer && <div className="vfd-modal-footer">{footer}</div>}
      </div>
    </div>
  );
};
