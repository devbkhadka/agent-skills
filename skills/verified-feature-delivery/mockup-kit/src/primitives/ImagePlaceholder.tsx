import React from 'react';

export interface ImagePlaceholderProps {
  width?: string | number;
  height?: string | number;
  aspectRatio?: string | number;
  label?: string;
  className?: string;
  style?: React.CSSProperties;
}

export const ImagePlaceholder: React.FC<ImagePlaceholderProps> = ({
  width = '100%',
  height = 180,
  aspectRatio,
  label = 'Image / Visual Asset Placeholder',
  className = '',
  style,
}) => {
  const customStyle: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: aspectRatio ? undefined : typeof height === 'number' ? `${height}px` : height,
    aspectRatio: aspectRatio ? String(aspectRatio) : undefined,
    ...style,
  };

  return (
    <div className={`vfd-placeholder-image ${className}`.trim()} style={customStyle}>
      <span>{label}</span>
    </div>
  );
};
