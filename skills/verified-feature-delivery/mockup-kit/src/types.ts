/**
 * Types definition for React Mockup Kit & Screen Catalogue
 * Supporting visual layout specification, scoped overlay interactivity,
 * and targeted numbered annotations for non-obvious domain context.
 */

import React from 'react';

/** Standard Viewport Presets */
export type ViewportTag =
  | 'desktop_1280x800'
  | 'desktop_1440x900'
  | 'laptop_1024x768'
  | 'tablet_768x1024'
  | 'mobile_375x667'
  | 'mobile_390x844'
  | 'responsive';

export interface ViewportDimensions {
  tag: ViewportTag;
  label: string;
  width: number | string;
  height: number | string;
  deviceType: 'desktop' | 'laptop' | 'tablet' | 'mobile' | 'responsive';
}

export const VIEWPORT_PRESETS: Record<ViewportTag, ViewportDimensions> = {
  desktop_1280x800: {
    tag: 'desktop_1280x800',
    label: 'Desktop (1280 × 800)',
    width: 1280,
    height: 800,
    deviceType: 'desktop',
  },
  desktop_1440x900: {
    tag: 'desktop_1440x900',
    label: 'Wide Desktop (1440 × 900)',
    width: 1440,
    height: 900,
    deviceType: 'desktop',
  },
  laptop_1024x768: {
    tag: 'laptop_1024x768',
    label: 'Laptop / Tablet Landscape (1024 × 768)',
    width: 1024,
    height: 768,
    deviceType: 'laptop',
  },
  tablet_768x1024: {
    tag: 'tablet_768x1024',
    label: 'Tablet Portrait (768 × 1024)',
    width: 768,
    height: 1024,
    deviceType: 'tablet',
  },
  mobile_375x667: {
    tag: 'mobile_375x667',
    label: 'Mobile Standard (375 × 667)',
    width: 375,
    height: 667,
    deviceType: 'mobile',
  },
  mobile_390x844: {
    tag: 'mobile_390x844',
    label: 'Mobile Modern (390 × 844)',
    width: 390,
    height: 844,
    deviceType: 'mobile',
  },
  responsive: {
    tag: 'responsive',
    label: 'Responsive Full Width',
    width: '100%',
    height: '100%',
    deviceType: 'responsive',
  },
};

/**
 * Targeted Annotation Badge Context Definition.
 * Strictly used for non-obvious UI elements, complex business rules,
 * subtle domain mappings, or hidden state transitions.
 */
export interface AnnotationItem {
  id: string | number;
  number: number;
  title: string;
  businessIntent: string;
  domainEntityMapping?: string;
  projectionSource?: string;
  subtleRules?: string;
  elementRefId?: string;
}

/** Screen Definition in the Catalogue */
export interface ScreenDefinition {
  id: string;
  sliceId: string;
  title: string;
  description: string;
  viewport: ViewportTag;
  category?: string;
  component: React.ComponentType<any>;
  annotations: AnnotationItem[];
}

/** Screen Catalogue Manifest */
export interface ScreenCatalogueManifest {
  featureSlug: string;
  featureTitle: string;
  version: string;
  description: string;
  screens: ScreenDefinition[];
}
