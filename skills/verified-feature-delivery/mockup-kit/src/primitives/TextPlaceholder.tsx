import React from 'react';

export interface TextPlaceholderProps {
  lines?: number;
  width?: string | number;
  height?: string | number;
  animated?: boolean;
  color?: string;
  style?: React.CSSProperties;
  className?: string;
}

export const TextPlaceholder: React.FC<TextPlaceholderProps> = ({
  lines = 1,
  width = '100%',
  height = 14,
  animated = true,
  color,
  style,
  className = '',
}) => {
  const lineArray = Array.from({ length: lines });

  return (
    <div className={`vfd-text-placeholder-group ${className}`.trim()} style={style}>
      {lineArray.map((_, index) => {
        // Slightly vary width of the last line if multiple lines
        const lineWidth = lines > 1 && index === lines - 1 && width === '100%' ? '70%' : width;
        const lineStyle: React.CSSProperties = {
          width: typeof lineWidth === 'number' ? `${lineWidth}px` : lineWidth,
          height: typeof height === 'number' ? `${height}px` : height,
          backgroundColor: color,
        };

        return (
          <span
            key={index}
            className={`vfd-placeholder-text ${animated ? 'vfd-placeholder-pulse' : ''}`.trim()}
            style={lineStyle}
          />
        );
      })}
    </div>
  );
};
