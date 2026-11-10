import React from 'react';
import { Box, BoxProps } from './Box';

export interface FlexProps extends BoxProps {
  direction?: 'row' | 'row-reverse' | 'column' | 'column-reverse';
  align?: 'flex-start' | 'flex-end' | 'center' | 'baseline' | 'stretch';
  justify?: 'flex-start' | 'flex-end' | 'center' | 'space-between' | 'space-around' | 'space-evenly';
  wrap?: 'nowrap' | 'wrap' | 'wrap-reverse';
  gap?: string | number;
  inline?: boolean;
}

export const Flex: React.FC<FlexProps> = ({
  direction = 'row',
  align = 'stretch',
  justify = 'flex-start',
  wrap = 'nowrap',
  gap,
  inline = false,
  style,
  className = '',
  children,
  ...props
}) => {
  const flexStyle: React.CSSProperties = {
    flexDirection: direction,
    alignItems: align,
    justifyContent: justify,
    flexWrap: wrap,
    gap: typeof gap === 'number' ? `${gap}px` : gap,
    ...style,
  };

  const flexClass = inline ? 'vfd-inline-flex' : 'vfd-flex';
  const combinedClassName = `${flexClass} ${className}`.trim();

  return (
    <Box className={combinedClassName} style={flexStyle} {...props}>
      {children}
    </Box>
  );
};
