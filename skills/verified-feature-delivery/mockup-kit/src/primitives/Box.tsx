import React from 'react';

export interface BoxProps extends React.HTMLAttributes<HTMLDivElement> {
  as?: keyof JSX.IntrinsicElements;
  padding?: string | number;
  margin?: string | number;
  bg?: string;
  border?: string;
  rounded?: string | number;
  width?: string | number;
  height?: string | number;
  shadow?: 'sm' | 'md' | 'lg' | 'xl' | 'none';
  position?: 'relative' | 'absolute' | 'fixed' | 'sticky';
  children?: React.ReactNode;
}

export const Box: React.FC<BoxProps> = ({
  as = 'div',
  padding,
  margin,
  bg,
  border,
  rounded,
  width,
  height,
  shadow,
  position,
  style,
  className = '',
  children,
  ...props
}) => {
  const Component = as as any;

  const customStyle: React.CSSProperties = {
    padding: typeof padding === 'number' ? `${padding}px` : padding,
    margin: typeof margin === 'number' ? `${margin}px` : margin,
    backgroundColor: bg,
    border,
    borderRadius: typeof rounded === 'number' ? `${rounded}px` : rounded,
    width,
    height,
    position,
    ...style,
  };

  const shadowClass = shadow && shadow !== 'none' ? `vfd-shadow-${shadow}` : '';
  const combinedClassName = `vfd-box ${shadowClass} ${className}`.trim();

  return (
    <Component className={combinedClassName} style={customStyle} {...props}>
      {children}
    </Component>
  );
};
