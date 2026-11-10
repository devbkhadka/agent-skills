import React from 'react';
import { Box, BoxProps } from './Box';

export interface CardProps extends BoxProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  headerRight?: React.ReactNode;
  footer?: React.ReactNode;
  variant?: 'outline' | 'elevated' | 'flat';
  noPadding?: boolean;
}

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  headerRight,
  footer,
  variant = 'outline',
  noPadding = false,
  shadow,
  className = '',
  children,
  ...props
}) => {
  const shadowVal = variant === 'elevated' ? shadow || 'md' : 'none';
  const hasHeader = title || subtitle || headerRight;

  return (
    <Box
      className={`vfd-card vfd-card-${variant} ${className}`.trim()}
      shadow={shadowVal}
      {...props}
    >
      {hasHeader && (
        <div className="vfd-card-header">
          <div>
            {title && <h3 className="vfd-card-title">{title}</h3>}
            {subtitle && <p className="vfd-card-subtitle">{subtitle}</p>}
          </div>
          {headerRight && <div>{headerRight}</div>}
        </div>
      )}
      <div className={noPadding ? '' : 'vfd-card-body'}>{children}</div>
      {footer && <div className="vfd-card-footer">{footer}</div>}
    </Box>
  );
};
