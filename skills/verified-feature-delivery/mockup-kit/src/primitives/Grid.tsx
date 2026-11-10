import React from 'react';
import { Box, BoxProps } from './Box';

export interface GridProps extends BoxProps {
  columns?: number | string;
  rows?: number | string;
  gap?: string | number;
  columnGap?: string | number;
  rowGap?: string | number;
  templateColumns?: string;
  templateRows?: string;
}

export const Grid: React.FC<GridProps> = ({
  columns,
  rows,
  gap,
  columnGap,
  rowGap,
  templateColumns,
  templateRows,
  style,
  className = '',
  children,
  ...props
}) => {
  let computedTemplateColumns = templateColumns;
  if (!computedTemplateColumns && columns) {
    computedTemplateColumns = typeof columns === 'number' ? `repeat(${columns}, minmax(0, 1fr))` : columns;
  }

  let computedTemplateRows = templateRows;
  if (!computedTemplateRows && rows) {
    computedTemplateRows = typeof rows === 'number' ? `repeat(${rows}, minmax(0, 1fr))` : rows;
  }

  const gridStyle: React.CSSProperties = {
    gridTemplateColumns: computedTemplateColumns,
    gridTemplateRows: computedTemplateRows,
    gap: typeof gap === 'number' ? `${gap}px` : gap,
    columnGap: typeof columnGap === 'number' ? `${columnGap}px` : columnGap,
    rowGap: typeof rowGap === 'number' ? `${rowGap}px` : rowGap,
    ...style,
  };

  const combinedClassName = `vfd-grid ${className}`.trim();

  return (
    <Box className={combinedClassName} style={gridStyle} {...props}>
      {children}
    </Box>
  );
};
