"""Mockup Bundle Generator: compiles standalone Screen Catalogue HTML/JS bundles.

Outputs self-contained, browser-inspectable HTML bundles under
`.verified-feature-delivery/<slug>/mockups/index.html` for direct human review.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

BUNDLE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${FEATURE_TITLE} — Screen Catalogue (${FEATURE_SLUG})</title>
  <style>
${STYLES_CSS}
  </style>
  <!-- Self-contained React 18 UMD & Babel standalone for direct browser inspection -->
  <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js" crossorigin></script>
</head>
<body>
  <div id="root"></div>

  <script type="text/babel">
    const { useState, useEffect, useRef, createContext, useContext } = React;

    // Viewport Definitions
    const VIEWPORT_PRESETS = {
      desktop_1280x800: { tag: 'desktop_1280x800', label: 'Desktop (1280 × 800)', width: 1280, height: 800 },
      desktop_1440x900: { tag: 'desktop_1440x900', label: 'Wide Desktop (1440 × 900)', width: 1440, height: 900 },
      laptop_1024x768: { tag: 'laptop_1024x768', label: 'Laptop (1024 × 768)', width: 1024, height: 768 },
      tablet_768x1024: { tag: 'tablet_768x1024', label: 'Tablet (768 × 1024)', width: 768, height: 1024 },
      mobile_375x667: { tag: 'mobile_375x667', label: 'Mobile Standard (375 × 667)', width: 375, height: 667 },
      mobile_390x844: { tag: 'mobile_390x844', label: 'Mobile Modern (390 × 844)', width: 390, height: 844 },
      responsive: { tag: 'responsive', label: 'Responsive Full Width', width: '100%', height: '100%' },
    };

    // Annotation Context & Badges
    const AnnotationContext = createContext({
      annotations: [],
      registerAnnotation: () => {},
      activeAnnotationId: null,
      setActiveAnnotationId: () => {},
    });

    const AnnotationProvider = ({ initialAnnotations = [], children }) => {
      const [annotations, setAnnotations] = useState(initialAnnotations);
      const [activeAnnotationId, setActiveAnnotationId] = useState(null);

      const registerAnnotation = (item) => {
        setAnnotations((prev) => {
          const exists = prev.find((a) => a.number === item.number || a.id === item.id);
          if (exists) {
            return prev.map((a) => (a.number === item.number || a.id === item.id ? { ...a, ...item } : a));
          }
          return [...prev, item].sort((a, b) => a.number - b.number);
        });
      };

      return (
        <AnnotationContext.Provider value={{ annotations, registerAnnotation, activeAnnotationId, setActiveAnnotationId }}>
          {children}
        </AnnotationContext.Provider>
      );
    };

    const useAnnotations = () => useContext(AnnotationContext);

    const AnnotationBadge = ({
      number,
      title,
      businessIntent,
      domainEntityMapping,
      projectionSource,
      subtleRules,
      position = 'top-right',
      inline = false,
    }) => {
      const { registerAnnotation, activeAnnotationId, setActiveAnnotationId } = useAnnotations();
      const [isHovered, setIsHovered] = useState(false);

      useEffect(() => {
        registerAnnotation({
          id: `annotation-${number}`,
          number,
          title,
          businessIntent,
          domainEntityMapping,
          projectionSource,
          subtleRules,
        });
      }, [number, title, businessIntent, domainEntityMapping, projectionSource, subtleRules]);

      const isActive = activeAnnotationId === number || activeAnnotationId === `annotation-${number}`;
      const showPopover = isHovered || isActive;

      const handleClick = (e) => {
        e.stopPropagation();
        setActiveAnnotationId(isActive ? null : number);
      };

      return (
        <div
          className={`vfd-annotation-badge ${inline ? 'vfd-annotation-badge-inline' : 'vfd-annotation-badge-overlay ' + position} ${isActive ? 'is-active' : ''}`}
          onClick={handleClick}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          title={`[${number}] ${title}`}
          role="button"
          tabIndex={0}
        >
          <span>{number}</span>
          {showPopover && (
            <div
              className="vfd-annotation-popover"
              style={{
                top: position.startsWith('top') ? '100%' : 'auto',
                bottom: position.startsWith('bottom') ? '100%' : 'auto',
                right: position.endsWith('right') ? 0 : 'auto',
                left: position.endsWith('left') ? 0 : 'auto',
                marginTop: 8,
                marginBottom: 8,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="vfd-popover-header">
                <span className="vfd-popover-badge-pill">{number}</span>
                <span className="vfd-popover-title">{title}</span>
                {isActive && (
                  <button className="vfd-popover-close" onClick={() => setActiveAnnotationId(null)}>✕</button>
                )}
              </div>
              <div className="vfd-popover-section">
                <div className="vfd-popover-label">Business Intent & Rationale</div>
                <div className="vfd-popover-value">{businessIntent}</div>
              </div>
              {domainEntityMapping && (
                <div className="vfd-popover-section">
                  <div className="vfd-popover-label">Domain Entity / Lexicon Mapping</div>
                  <span className="vfd-popover-mono">{domainEntityMapping}</span>
                </div>
              )}
              {projectionSource && (
                <div className="vfd-popover-section">
                  <div className="vfd-popover-label">Projection Source / State Transition</div>
                  <span className="vfd-popover-mono">{projectionSource}</span>
                </div>
              )}
              {subtleRules && (
                <div className="vfd-popover-section">
                  <div className="vfd-popover-label">Subtle Constraints</div>
                  <div className="vfd-popover-value">{subtleRules}</div>
                </div>
              )}
            </div>
          )}
        </div>
      );
    };

    // Minimal Layout Primitives
    const Box = ({ as: Component = 'div', padding, margin, bg, border, rounded, width, height, shadow, position, style, className = '', children, ...props }) => {
      const customStyle = {
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
      return (
        <Component className={`vfd-box ${shadowClass} ${className}`.trim()} style={customStyle} {...props}>
          {children}
        </Component>
      );
    };

    const Flex = ({ direction = 'row', align = 'stretch', justify = 'flex-start', wrap = 'nowrap', gap, inline = false, style, className = '', children, ...props }) => {
      const flexStyle = {
        flexDirection: direction,
        alignItems: align,
        justifyContent: justify,
        flexWrap: wrap,
        gap: typeof gap === 'number' ? `${gap}px` : gap,
        ...style,
      };
      const flexClass = inline ? 'vfd-inline-flex' : 'vfd-flex';
      return (
        <Box className={`${flexClass} ${className}`.trim()} style={flexStyle} {...props}>
          {children}
        </Box>
      );
    };

    const Grid = ({ columns, rows, gap, columnGap, rowGap, templateColumns, templateRows, style, className = '', children, ...props }) => {
      let computedTemplateColumns = templateColumns;
      if (!computedTemplateColumns && columns) {
        computedTemplateColumns = typeof columns === 'number' ? `repeat(${columns}, minmax(0, 1fr))` : columns;
      }
      const gridStyle = {
        gridTemplateColumns: computedTemplateColumns,
        gap: typeof gap === 'number' ? `${gap}px` : gap,
        columnGap: typeof columnGap === 'number' ? `${columnGap}px` : columnGap,
        rowGap: typeof rowGap === 'number' ? `${rowGap}px` : rowGap,
        ...style,
      };
      return (
        <Box className={`vfd-grid ${className}`.trim()} style={gridStyle} {...props}>
          {children}
        </Box>
      );
    };

    const Stack = ({ direction = 'vertical', gap = 12, align = 'stretch', justify = 'flex-start', divider, style, className = '', children, ...props }) => {
      const stackStyle = {
        flexDirection: direction === 'vertical' ? 'column' : 'row',
        alignItems: align,
        justifyContent: justify,
        gap: typeof gap === 'number' ? `${gap}px` : gap,
        ...style,
      };
      return (
        <Box className={`vfd-stack vfd-stack-${direction} ${className}`.trim()} style={stackStyle} {...props}>
          {children}
        </Box>
      );
    };

    const Card = ({ title, subtitle, headerRight, footer, variant = 'outline', shadow, className = '', children, ...props }) => {
      const shadowVal = variant === 'elevated' ? shadow || 'md' : 'none';
      const hasHeader = title || subtitle || headerRight;
      return (
        <Box className={`vfd-card vfd-card-${variant} ${className}`.trim()} shadow={shadowVal} {...props}>
          {hasHeader && (
            <div className="vfd-card-header">
              <div>
                {title && <h3 className="vfd-card-title">{title}</h3>}
                {subtitle && <p className="vfd-card-subtitle">{subtitle}</p>}
              </div>
              {headerRight && <div>{headerRight}</div>}
            </div>
          )}
          <div className="vfd-card-body">{children}</div>
          {footer && <div className="vfd-card-footer">{footer}</div>}
        </Box>
      );
    };

    const Button = ({ variant = 'secondary', size = 'md', className = '', disabled, children, ...props }) => (
      <button className={`vfd-button vfd-button-${variant} vfd-button-${size} ${className}`.trim()} disabled={disabled} {...props}>
        {children}
      </button>
    );

    const TextPlaceholder = ({ lines = 1, width = '100%', height = 14, animated = true }) => (
      <div>
        {Array.from({ length: lines }).map((_, i) => (
          <span
            key={i}
            className={`vfd-placeholder-text ${animated ? 'vfd-placeholder-pulse' : ''}`}
            style={{ width: lines > 1 && i === lines - 1 && width === '100%' ? '70%' : width, height: `${height}px` }}
          />
        ))}
      </div>
    );

    // Scoped Overlays
    const Modal = ({ isOpen, onClose, title, size = 'md', footer, children }) => {
      if (!isOpen) return null;
      return (
        <div className="vfd-overlay-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
          <div className={`vfd-modal vfd-modal-${size}`} role="dialog" aria-modal="true">
            {title && (
              <div className="vfd-modal-header">
                <h2 className="vfd-modal-title">{title}</h2>
                <button className="vfd-button vfd-button-ghost vfd-button-sm" onClick={onClose}>✕</button>
              </div>
            )}
            <div className="vfd-modal-body">{children}</div>
            {footer && <div className="vfd-modal-footer">{footer}</div>}
          </div>
        </div>
      );
    };

    const Accordion = ({ items, defaultExpandedIds = [] }) => {
      const [expandedIds, setExpandedIds] = useState(defaultExpandedIds);
      const toggle = (id) => {
        setExpandedIds((prev) => prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]);
      };
      return (
        <div className="vfd-accordion">
          {items.map((item) => {
            const isExpanded = expandedIds.includes(item.id);
            return (
              <div key={item.id} className="vfd-accordion-item">
                <button type="button" className="vfd-accordion-header" onClick={() => toggle(item.id)}>
                  <span>{item.title}</span>
                  <span className={`vfd-accordion-chevron ${isExpanded ? 'is-open' : ''}`}>▼</span>
                </button>
                {isExpanded && <div className="vfd-accordion-body">{item.content}</div>}
              </div>
            );
          })}
        </div>
      );
    };

    // Registered Screens Manifest
    const MANIFEST_SCREENS = ${SCREENS_JSON};

    // Example Screen Implementation
    const OrderReconciliationScreen = () => {
      const [isModalOpen, setIsModalOpen] = useState(false);
      const accordionItems = [
        {
          id: 'audit-trail',
          title: 'Ledger Audit Trail & State Transitions (3 Events)',
          content: (
            <Stack direction="vertical" gap={8}>
              <div style={{ fontSize: '0.82rem', padding: '8px', background: '#fff', border: '1px solid #e2e8f0', borderRadius: 4 }}>
                <strong>2026-08-30 14:00:00</strong> — <code>BatchIngested</code>: 1,420 orders imported from Gateway A.
              </div>
              <div style={{ fontSize: '0.82rem', padding: '8px', background: '#fff', border: '1px solid #e2e8f0', borderRadius: 4 }}>
                <strong>2026-08-30 14:02:15</strong> — <code>DiscrepancyFlagged</code>: Order #REC-9821 variance exceeds tolerance.
              </div>
            </Stack>
          ),
        },
      ];

      return (
        <Box padding={24} bg="#f8fafc" style={{ minHeight: '100%', minWidth: 1200 }}>
          <Flex justify="space-between" align="center" style={{ marginBottom: 20 }}>
            <div>
              <h1 style={{ fontSize: '1.4rem', fontWeight: 700, margin: 0 }}>Order Reconciliation & Settlement Ledger</h1>
              <p style={{ fontSize: '0.85rem', color: '#64748b', margin: '4px 0 0 0' }}>Feature Slice: <code>slice_settlement_reconciliation</code></p>
            </div>
            <Flex gap={10}>
              <Button variant="outline">Export CSV</Button>
              <Button variant="primary" onClick={() => setIsModalOpen(true)}>+ Create Manual Adjustment</Button>
            </Flex>
          </Flex>

          <Grid columns={3} gap={16} style={{ marginBottom: 24 }}>
            <Card variant="outline" title="Total Batch Volume">
              <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>$1,842,950.00</div>
              <div style={{ fontSize: '0.8rem', color: '#059669', marginTop: 4 }}>1,420 Transactions Ingested</div>
            </Card>
            <Card variant="outline" title="Matched & Cleared">
              <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>$1,841,720.50</div>
              <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: 4 }}>99.93% Auto-reconciliation Rate</div>
            </Card>
            <Box position="relative">
              <Card variant="outline" title="Disputed Variance">
                <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#b91c1c' }}>$1,229.50</div>
                <div style={{ fontSize: '0.8rem', color: '#b91c1c', marginTop: 4 }}>3 Batches Require Review</div>
              </Card>
              <AnnotationBadge
                number={1}
                title="Discrepancy Tolerance Threshold"
                businessIntent="Variances under $5.00 are automatically written off to the rounding reserve. Any aggregate variance >= $5.00 halts automatic clearing and flags the batch for human ledger review."
                domainEntityMapping="LedgerReconciliationRule.discrepancy_tolerance_limit"
                projectionSource="TransactionBatchImportedEvent -> LedgerDiscrepancyProjection"
                position="top-right"
              />
            </Box>
          </Grid>

          <Card title="Flagged Reconciliation Line Items" style={{ marginBottom: 24 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.88rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e2e8f0', textAlign: 'left' }}>
                  <th style={{ padding: '10px 12px' }}>Order Ref</th>
                  <th style={{ padding: '10px 12px' }}>Merchant</th>
                  <th style={{ padding: '10px 12px' }}>Gross Amount</th>
                  <th style={{ padding: '10px 12px', position: 'relative' }}>
                    Tax Allocation Split
                    <AnnotationBadge
                      number={2}
                      title="Multi-Jurisdiction Split Tax Calculation"
                      businessIntent="Tax liability is split dynamically between warehouse origin state and destination buyer jurisdiction based on economic nexus rules."
                      domainEntityMapping="TaxJurisdictionAllocationValueObject"
                      projectionSource="OrderPlacedEvent -> StateTaxAllocationProjection"
                      position="top-right"
                    />
                  </th>
                  <th style={{ padding: '10px 12px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                  <td style={{ padding: '12px' }}><code>#REC-9821</code></td>
                  <td style={{ padding: '12px' }}>Apex Logistics Corp</td>
                  <td style={{ padding: '12px' }}>$4,500.00</td>
                  <td style={{ padding: '12px' }}>WA: $280.00 / CA: $140.00</td>
                  <td style={{ padding: '12px' }}>
                    <Button variant="primary" size="sm" onClick={() => setIsModalOpen(true)}>Inspect & Adjust</Button>
                  </td>
                </tr>
              </tbody>
            </table>
          </Card>

          <Card title="System Audit & Invariant Logs">
            <Box position="relative">
              <Accordion items={accordionItems} defaultExpandedIds={['audit-trail']} />
              <AnnotationBadge
                number={3}
                title="Two-Phase Settlement Payout Hold"
                businessIntent="State transition into SETTLED is guarded until both gateway bank batch capture and tax split verification emit confirmation tokens."
                domainEntityMapping="SettlementBatchAggregate.hold_status"
                projectionSource="BankClearingStatusUpdatedEvent -> SettlementAggregate"
                position="top-right"
              />
            </Box>
          </Card>

          <Modal
            isOpen={isModalOpen}
            onClose={() => setIsModalOpen(false)}
            title="Adjust Discrepancy #REC-9821"
            footer={
              <>
                <Button variant="secondary" onClick={() => setIsModalOpen(false)}>Cancel</Button>
                <Button variant="primary" onClick={() => { alert('Adjustment simulated.'); setIsModalOpen(false); }}>Post Adjustment</Button>
              </>
            }
          >
            <Stack direction="vertical" gap={12}>
              <div>
                <label style={{ fontSize: '0.85rem', fontWeight: 600 }}>Category</label>
                <select style={{ width: '100%', padding: '8px', borderRadius: 4, border: '1px solid #cbd5e1' }}>
                  <option>Tax Jurisdiction Reallocation</option>
                  <option>Exchange Rate Float Difference</option>
                </select>
              </div>
              <TextPlaceholder lines={2} />
            </Stack>
          </Modal>
        </Box>
      );
    };

    const InvoiceSettlementMobileScreen = () => {
      return (
        <Box padding={16} bg="#ffffff" style={{ minHeight: 667, width: 375, margin: '0 auto' }}>
          <Flex justify="space-between" align="center" style={{ marginBottom: 16, borderBottom: '1px solid #e2e8f0', paddingBottom: 12 }}>
            <h2 style={{ fontSize: '1.1rem', fontWeight: 700, margin: 0 }}>Mobile Settlement</h2>
            <Button variant="ghost" size="sm">☰</Button>
          </Flex>
          <Box position="relative" style={{ marginBottom: 16 }}>
            <Card variant="elevated" style={{ backgroundColor: '#1e293b', color: '#ffffff' }}>
              <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Available for Immediate Payout</div>
              <div style={{ fontSize: '1.7rem', fontWeight: 700, margin: '4px 0 8px 0', color: '#ffffff' }}>$24,850.00</div>
              <Button variant="primary" size="sm" style={{ width: '100%' }}>Withdraw Now</Button>
            </Card>
            <AnnotationBadge
              number={1}
              title="Instant Payout Cap & Reserve Ratio"
              businessIntent="Mobile instant payouts are strictly bounded to 80% of net settled funds; remaining 20% remains in rolling 7-day reserve buffer."
              domainEntityMapping="MerchantPayoutPolicy.instant_payout_reserve_fraction"
              projectionSource="PayoutRequestedEvent -> MerchantBalanceProjection"
              position="top-right"
            />
          </Box>
          <TextPlaceholder lines={3} height={12} />
        </Box>
      );
    };

    const SCREEN_COMPONENTS = {
      screen_order_reconciliation: OrderReconciliationScreen,
      screen_invoice_settlement_mobile: InvoiceSettlementMobileScreen,
    };

    // Central Screen Catalogue Shell
    const App = () => {
      const screens = MANIFEST_SCREENS;
      const [selectedScreenId, setSelectedScreenId] = useState(screens[0]?.id || '');
      const [customViewport, setCustomViewport] = useState(null);
      const [zoomScale, setZoomScale] = useState(1);
      const [showInspector, setShowInspector] = useState(true);

      const activeScreen = screens.find((s) => s.id === selectedScreenId) || screens[0];
      const activeViewport = customViewport || activeScreen?.viewport || 'desktop_1280x800';
      const preset = VIEWPORT_PRESETS[activeViewport] || VIEWPORT_PRESETS.desktop_1280x800;
      const ActiveComponent = SCREEN_COMPONENTS[activeScreen?.id] || OrderReconciliationScreen;

      return (
        <div className="vfd-catalogue-root">
          <aside className="vfd-catalogue-sidebar">
            <div className="vfd-catalogue-header">
              <h1>${FEATURE_TITLE}</h1>
              <p>Feature: ${FEATURE_SLUG}</p>
            </div>
            <div className="vfd-catalogue-screens-list">
              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', marginBottom: '8px' }}>
                Feature Slices & Screens ({screens.length})
              </div>
              {screens.map((screen) => (
                <button
                  key={screen.id}
                  type="button"
                  className={`vfd-screen-card-btn ${screen.id === selectedScreenId ? 'is-active' : ''}`}
                  onClick={() => { setSelectedScreenId(screen.id); setCustomViewport(null); }}
                >
                  <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '2px' }}>{screen.title}</div>
                  <div style={{ fontSize: '0.78rem', color: '#94a3b8', marginBottom: '6px' }}>Slice: {screen.sliceId}</div>
                  <div className="vfd-screen-meta-row">
                    <span className="vfd-viewport-tag">{screen.viewport}</span>
                    {screen.annotations?.length > 0 && (
                      <span className="vfd-annotation-count-pill">{screen.annotations.length} rules</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </aside>

          <main className="vfd-catalogue-viewport-area">
            <div className="vfd-viewport-toolbar">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <span style={{ fontWeight: 600, fontSize: '0.9rem', color: '#ffffff' }}>{activeScreen?.title}</span>
                <span className="vfd-viewport-tag">{activeViewport}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem' }}>
                  <label style={{ color: '#94a3b8' }}>Viewport:</label>
                  <select
                    value={activeViewport}
                    onChange={(e) => setCustomViewport(e.target.value)}
                    style={{ backgroundColor: '#0f172a', color: '#fff', border: '1px solid #334155', borderRadius: 4, padding: '4px 8px' }}
                  >
                    {Object.values(VIEWPORT_PRESETS).map((p) => (
                      <option key={p.tag} value={p.tag}>{p.label}</option>
                    ))}
                  </select>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem' }}>
                  <label style={{ color: '#94a3b8' }}>Scale:</label>
                  <select
                    value={zoomScale}
                    onChange={(e) => setZoomScale(parseFloat(e.target.value))}
                    style={{ backgroundColor: '#0f172a', color: '#fff', border: '1px solid #334155', borderRadius: 4, padding: '4px 8px' }}
                  >
                    <option value={1}>100%</option>
                    <option value={0.9}>90%</option>
                    <option value={0.8}>80%</option>
                    <option value={0.75}>75%</option>
                    <option value={0.65}>65%</option>
                    <option value={0.5}>50%</option>
                  </select>
                </div>
                <button
                  className="vfd-button vfd-button-secondary vfd-button-sm"
                  onClick={() => setShowInspector(!showInspector)}
                  style={{ backgroundColor: showInspector ? '#312e81' : '#0f172a', borderColor: showInspector ? '#6366f1' : '#334155', color: '#fff' }}
                >
                  Annotations ({activeScreen?.annotations?.length || 0})
                </button>
              </div>
            </div>

            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
              <div className="vfd-viewport-canvas">
                <AnnotationProvider initialAnnotations={activeScreen?.annotations || []}>
                  <div
                    className="vfd-viewport-frame"
                    style={{
                      width: preset.width,
                      height: preset.height,
                      maxWidth: activeViewport === 'responsive' ? '100%' : undefined,
                      transform: zoomScale !== 1 ? `scale(${zoomScale})` : undefined,
                      transformOrigin: 'top center',
                    }}
                  >
                    <ActiveComponent />
                  </div>
                </AnnotationProvider>
              </div>

              {showInspector && (
                <AnnotationProvider initialAnnotations={activeScreen?.annotations || []}>
                  <aside className="vfd-annotation-inspector">
                    <div className="vfd-inspector-header">
                      <div>
                        <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: 0, color: '#fff' }}>
                          Domain Annotations ({activeScreen?.annotations?.length || 0})
                        </h2>
                        <p style={{ fontSize: '0.75rem', margin: '2px 0 0 0', color: '#94a3b8' }}>
                          Selective context & subtle business rules
                        </p>
                      </div>
                    </div>
                    <div className="vfd-inspector-body">
                      {(activeScreen?.annotations || []).map((item) => (
                        <div key={item.id} className="vfd-inspector-item">
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <span className="vfd-popover-badge-pill">{item.number}</span>
                            <span style={{ fontWeight: 600, fontSize: '0.88rem', color: '#fff' }}>{item.title}</span>
                          </div>
                          <div style={{ fontSize: '0.82rem', color: '#cbd5e1', marginBottom: '8px', lineHeight: 1.4 }}>
                            {item.businessIntent}
                          </div>
                          {item.domainEntityMapping && (
                            <div style={{ marginTop: '6px' }}>
                              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 700 }}>
                                Entity Mapping
                              </div>
                              <span className="vfd-popover-mono" style={{ backgroundColor: '#0f172a', color: '#a5b4fc' }}>
                                {item.domainEntityMapping}
                              </span>
                            </div>
                          )}
                          {item.projectionSource && (
                            <div style={{ marginTop: '6px' }}>
                              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 700 }}>
                                Projection Source
                              </div>
                              <span className="vfd-popover-mono" style={{ backgroundColor: '#0f172a', color: '#a5b4fc' }}>
                                {item.projectionSource}
                              </span>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </aside>
                </AnnotationProvider>
              )}
            </div>
          </main>
        </div>
      );
    };

    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(<App />);
  </script>
</body>
</html>
"""


def default_sample_screens() -> List[Dict[str, Any]]:
    return [
        {
            "id": "screen_order_reconciliation",
            "sliceId": "slice_settlement_reconciliation",
            "title": "Order Reconciliation & Settlement Ledger",
            "description": "Desktop visual layout specification for multi-jurisdiction settlement reconciliation and dispute adjustment.",
            "viewport": "desktop_1280x800",
            "category": "Settlement",
            "annotations": [
                {
                    "id": "ann-1",
                    "number": 1,
                    "title": "Discrepancy Tolerance Threshold",
                    "businessIntent": "Variances under $5.00 are automatically written off to the rounding reserve. Any aggregate variance >= $5.00 halts automatic clearing and flags the batch for human ledger review.",
                    "domainEntityMapping": "LedgerReconciliationRule.discrepancy_tolerance_limit",
                    "projectionSource": "TransactionBatchImportedEvent -> LedgerDiscrepancyProjection",
                    "subtleRules": "Tolerance is calculated before currency conversion rate adjustments are finalized.",
                },
                {
                    "id": "ann-2",
                    "number": 2,
                    "title": "Multi-Jurisdiction Split Tax Calculation",
                    "businessIntent": "Tax liability is split dynamically between warehouse origin state and destination buyer jurisdiction based on economic nexus rules.",
                    "domainEntityMapping": "TaxJurisdictionAllocationValueObject",
                    "projectionSource": "OrderPlacedEvent -> StateTaxAllocationProjection",
                    "subtleRules": "Origin tax rate applies to handling; destination tax rate applies to goods.",
                },
                {
                    "id": "ann-3",
                    "number": 3,
                    "title": "Two-Phase Settlement Payout Hold",
                    "businessIntent": "State transition into SETTLED is guarded until both gateway bank batch capture and tax split verification emit confirmation tokens.",
                    "domainEntityMapping": "SettlementBatchAggregate.hold_status",
                    "projectionSource": "BankClearingStatusUpdatedEvent -> SettlementAggregate",
                    "subtleRules": "If settlement token is absent after 48h, triggers automatic SLA escalation.",
                },
            ],
        },
        {
            "id": "screen_invoice_settlement_mobile",
            "sliceId": "slice_mobile_settlement",
            "title": "Mobile Merchant Settlement Dashboard",
            "description": "Compact mobile viewport layout for merchant instant payout and reserve ratio inspection.",
            "viewport": "mobile_375x667",
            "category": "Merchant Mobile",
            "annotations": [
                {
                    "id": "ann-m1",
                    "number": 1,
                    "title": "Instant Payout Cap & Reserve Ratio",
                    "businessIntent": "Mobile instant payouts are strictly bounded to 80% of net settled funds; remaining 20% remains in rolling 7-day reserve buffer.",
                    "domainEntityMapping": "MerchantPayoutPolicy.instant_payout_reserve_fraction",
                    "projectionSource": "PayoutRequestedEvent -> MerchantBalanceProjection",
                },
            ],
        },
    ]


def render_mockup_bundle(
    feature_slug: str,
    feature_title: str = "Verified Feature Mockup Studio",
    screens: Optional[List[Dict[str, Any]]] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    """Renders a self-contained static HTML bundle for browser inspection."""
    if screens is None:
        screens = default_sample_screens()

    css_path = Path(__file__).resolve().parent.parent / "mockup-kit" / "src" / "styles.css"
    if css_path.is_file():
        styles_css = css_path.read_text(encoding="utf-8")
    else:
        styles_css = "/* styles */"

    screens_json = json.dumps(screens, indent=2)

    html_content = BUNDLE_TEMPLATE
    html_content = html_content.replace("${FEATURE_SLUG}", feature_slug)
    html_content = html_content.replace("${FEATURE_TITLE}", feature_title)
    html_content = html_content.replace("${STYLES_CSS}", styles_css)
    html_content = html_content.replace("${SCREENS_JSON}", screens_json)

    if out_dir is None:
        out_dir = Path.cwd() / ".verified-feature-delivery" / feature_slug / "mockups"
    
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(html_content, encoding="utf-8")
    return out_file
