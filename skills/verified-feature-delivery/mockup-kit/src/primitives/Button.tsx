import React from 'react';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  icon?: React.ReactNode;
  children?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'secondary',
  size = 'md',
  icon,
  className = '',
  disabled,
  children,
  ...props
}) => {
  const btnClass = `vfd-button vfd-button-${variant} vfd-button-${size} ${className}`.trim();

  return (
    <button className={btnClass} disabled={disabled} {...props}>
      {icon && <span className="vfd-button-icon">{icon}</span>}
      {children}
    </button>
  );
};
