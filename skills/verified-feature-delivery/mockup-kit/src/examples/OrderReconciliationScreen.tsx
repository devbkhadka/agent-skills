import React, { useState } from 'react';
import { Box, Flex, Grid, Stack, Card, Button, TextPlaceholder } from '../primitives';
import { Modal, Accordion, Dropdown, AnnotationBadge } from '../components';

export const OrderReconciliationScreen: React.FC = () => {
  const [isAdjustmentModalOpen, setIsAdjustmentModalOpen] = useState(false);
  const [selectedDisputeId, setSelectedDisputeId] = useState<string | null>(null);

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
          <div style={{ fontSize: '0.82rem', padding: '8px', background: '#fff', border: '1px solid #e2e8f0', borderRadius: 4 }}>
            <strong>2026-08-30 14:05:00</strong> — <code>SettlementHoldApplied</code>: Payout lock placed on merchant account #M-402.
          </div>
        </Stack>
      ),
    },
    {
      id: 'settlement-rules',
      title: 'Active Settlement Policy & Currency Rounding Rules',
      content: (
        <div style={{ fontSize: '0.85rem', color: '#475569' }}>
          Standard ISO-4217 2-decimal fractional rounding applies with banker&apos;s round-to-even on terminal tax fractions.
        </div>
      ),
    },
  ];

  return (
    <Box padding={24} bg="#f8fafc" style={{ minHeight: '100%', minWidth: 1200 }}>
      {/* Top Header / Breadcrumbs */}
      <Flex justify="space-between" align="center" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: '1.4rem', fontWeight: 700, margin: 0, color: '#0f172a' }}>
            Order Reconciliation & Settlement Ledger
          </h1>
          <p style={{ fontSize: '0.85rem', color: '#64748b', margin: '4px 0 0 0' }}>
            Feature Slice: <code>slice_settlement_reconciliation</code> (Tracer-bullet M1)
          </p>
        </div>
        <Flex gap={10}>
          <Button variant="outline">Export CSV</Button>
          <Button variant="primary" onClick={() => setIsAdjustmentModalOpen(true)}>
            + Create Manual Adjustment
          </Button>
        </Flex>
      </Flex>

      {/* Summary KPI Cards Grid */}
      <Grid columns={3} gap={16} style={{ marginBottom: 24 }}>
        <Card variant="outline" title="Total Batch Volume">
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#0f172a' }}>$1,842,950.00</div>
          <div style={{ fontSize: '0.8rem', color: '#059669', marginTop: 4 }}>1,420 Transactions Ingested</div>
        </Card>

        <Card variant="outline" title="Matched & Cleared">
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#0f172a' }}>$1,841,720.50</div>
          <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: 4 }}>99.93% Auto-reconciliation Rate</div>
        </Card>

        {/* Card with Targeted Annotation Badge 1 */}
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
            subtleRules="Tolerance is calculated before currency conversion rate adjustments are finalized."
            position="top-right"
          />
        </Box>
      </Grid>

      {/* Main Reconciliation Table */}
      <Card
        title="Flagged Reconciliation Line Items"
        subtitle="Review outstanding transactions with tax split anomalies or gateway mismatches"
        style={{ marginBottom: 24 }}
        headerRight={
          <Dropdown
            trigger={<Button variant="outline" size="sm">Filter Status ▼</Button>}
            items={[
              { id: 'all', label: 'All Items' },
              { id: 'disputed', label: 'Flagged Only' },
              { id: 'cleared', label: 'Cleared Only' },
            ]}
          />
        }
      >
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.88rem' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e2e8f0', textAlign: 'left', color: '#475569' }}>
              <th style={{ padding: '10px 12px' }}>Order Ref</th>
              <th style={{ padding: '10px 12px' }}>Merchant</th>
              <th style={{ padding: '10px 12px' }}>Gross Amount</th>
              <th style={{ padding: '10px 12px', position: 'relative' }}>
                Tax Allocation Split
                {/* Targeted Annotation Badge 2 */}
                <AnnotationBadge
                  number={2}
                  title="Multi-Jurisdiction Split Tax Calculation"
                  businessIntent="Tax liability is split dynamically between warehouse origin state and destination buyer jurisdiction based on economic nexus rules."
                  domainEntityMapping="TaxJurisdictionAllocationValueObject"
                  projectionSource="OrderPlacedEvent -> StateTaxAllocationProjection"
                  subtleRules="Origin tax rate applies to handling; destination tax rate applies to goods."
                  position="top-right"
                />
              </th>
              <th style={{ padding: '10px 12px' }}>Discrepancy</th>
              <th style={{ padding: '10px 12px' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
              <td style={{ padding: '12px' }}><code>#REC-9821</code></td>
              <td style={{ padding: '12px' }}>Apex Logistics Corp</td>
              <td style={{ padding: '12px' }}>$4,500.00</td>
              <td style={{ padding: '12px' }}>WA: $280.00 / CA: $140.00</td>
              <td style={{ padding: '12px', color: '#b91c1c', fontWeight: 600 }}>+$420.00</td>
              <td style={{ padding: '12px' }}>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setSelectedDisputeId('#REC-9821');
                    setIsAdjustmentModalOpen(true);
                  }}
                >
                  Inspect & Adjust
                </Button>
              </td>
            </tr>
            <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
              <td style={{ padding: '12px' }}><code>#REC-9824</code></td>
              <td style={{ padding: '12px' }}>Beacon Retailers</td>
              <td style={{ padding: '12px' }}>$1,200.00</td>
              <td style={{ padding: '12px' }}>NY: $96.00</td>
              <td style={{ padding: '12px', color: '#b91c1c', fontWeight: 600 }}>+$809.50</td>
              <td style={{ padding: '12px' }}>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setSelectedDisputeId('#REC-9824');
                    setIsAdjustmentModalOpen(true);
                  }}
                >
                  Inspect & Adjust
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      {/* Collapsible History Section */}
      <Card title="System Audit & Invariant Logs" style={{ marginBottom: 24 }}>
        <Box position="relative">
          <Accordion items={accordionItems} defaultExpandedIds={['audit-trail']} />
          {/* Targeted Annotation Badge 3 */}
          <AnnotationBadge
            number={3}
            title="Two-Phase Settlement Payout Hold"
            businessIntent="State transition into SETTLED is guarded until both gateway bank batch capture and tax split verification emit confirmation tokens."
            domainEntityMapping="SettlementBatchAggregate.hold_status"
            projectionSource="BankClearingStatusUpdatedEvent -> SettlementAggregate"
            subtleRules="If settlement token is absent after 48h, triggers automatic SLA escalation."
            position="top-right"
          />
        </Box>
      </Card>

      {/* Scoped Interactive Modal */}
      <Modal
        isOpen={isAdjustmentModalOpen}
        onClose={() => setIsAdjustmentModalOpen(false)}
        title={`Adjust Discrepancy ${selectedDisputeId || '#REC-9821'}`}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setIsAdjustmentModalOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                alert('Adjustment applied to ledger simulation.');
                setIsAdjustmentModalOpen(false);
              }}
            >
              Post Adjustment
            </Button>
          </>
        }
      >
        <Stack direction="vertical" gap={16}>
          <div>
            <label style={{ fontSize: '0.85rem', fontWeight: 600, display: 'block', marginBottom: 4 }}>
              Adjustment Category
            </label>
            <select style={{ width: '100%', padding: '8px', borderRadius: 6, border: '1px solid #cbd5e1' }}>
              <option>Tax Jurisdiction Reallocation</option>
              <option>Exchange Rate Float Difference</option>
              <option>Gateway Fee Reversal</option>
            </select>
          </div>

          <div>
            <label style={{ fontSize: '0.85rem', fontWeight: 600, display: 'block', marginBottom: 4 }}>
              Adjustment Amount ($)
            </label>
            <input
              type="number"
              defaultValue="420.00"
              style={{ width: '100%', padding: '8px', borderRadius: 6, border: '1px solid #cbd5e1' }}
            />
          </div>

          <Box padding={12} bg="#f1f5f9" rounded={6}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#475569', textTransform: 'uppercase', marginBottom: 4 }}>
              Impacted Domain Entities
            </div>
            <TextPlaceholder lines={2} height={10} animated={false} />
          </Box>
        </Stack>
      </Modal>
    </Box>
  );
};
