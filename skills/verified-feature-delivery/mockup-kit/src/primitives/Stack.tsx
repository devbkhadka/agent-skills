import React from 'react';
import { Box, BoxProps } from './Box';

export interface StackProps extends BoxProps {
  direction?: 'vertical' | 'horizontal';
  gap?: string | number;
  align?: 'flex-start' | 'flex-end' | 'center' | 'baseline' | 'stretch';
  justify?: 'flex-start' | 'flex-end' | 'center' | 'space-between' | 'space-around' | 'space-evenly';
  divider?: React.ReactNode;
}

export const Stack: React.FC<StackProps> = ({
  direction = 'vertical',
  gap = 12,
  align = 'stretch',
  justify = 'flex-start',
  divider,
  style,
  className = '',
  children,
  ...props
}) => {
  const stackStyle: React.CSSProperties = {
    flexDirection: direction === 'vertical' ? 'column' : 'row',
    alignItems: align,
    justifyContent: justify,
    gap: typeof gap === 'number' ? `${gap}px` : gap,
    ...style,
  };

  const stackClass = `vfd-stack vfd-stack-${direction}`;
  const combinedClassName = `${stackClass} ${className}`.trim();

  // If a divider is provided, interleave it between children
  const validChildren = React.Children.toArray(children).filter(Boolean);
  const elements = divider
    ? validChildren.flatMap((child, index) =>
        index < validChildren.length - 1
          ? [child, React.cloneElement(divider as React.ReactElement, { key: `divider-${index}` })]
          : [child]
      )
    : children;

  return (
    <Box className={combinedClassName} style={stackStyle} {...props}>
      {elements}
    </Box>
  );
};
