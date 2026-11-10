import { ScreenDefinition } from '../types';
import { OrderReconciliationScreen } from './OrderReconciliationScreen';
import { InvoiceSettlementMobileScreen } from './InvoiceSettlementMobileScreen';

export * from './OrderReconciliationScreen';
export * from './InvoiceSettlementMobileScreen';

export const EXAMPLE_SCREENS: ScreenDefinition[] = [
  {
    id: 'screen_order_reconciliation',
    sliceId: 'slice_settlement_reconciliation',
    title: 'Order Reconciliation & Settlement Ledger',
    description: 'Desktop visual layout specification for multi-jurisdiction settlement reconciliation and dispute adjustment.',
    viewport: 'desktop_1280x800',
    category: 'Settlement',
    component: OrderReconciliationScreen,
    annotations: [
      {
        id: 'ann-1',
        number: 1,
        title: 'Discrepancy Tolerance Threshold',
        businessIntent: 'Variances under $5.00 are automatically written off to the rounding reserve. Any aggregate variance >= $5.00 halts automatic clearing and flags the batch for human ledger review.',
        domainEntityMapping: 'LedgerReconciliationRule.discrepancy_tolerance_limit',
        projectionSource: 'TransactionBatchImportedEvent -> LedgerDiscrepancyProjection',
        subtleRules: 'Tolerance is calculated before currency conversion rate adjustments are finalized.',
      },
      {
        id: 'ann-2',
        number: 2,
        title: 'Multi-Jurisdiction Split Tax Calculation',
        businessIntent: 'Tax liability is split dynamically between warehouse origin state and destination buyer jurisdiction based on economic nexus rules.',
        domainEntityMapping: 'TaxJurisdictionAllocationValueObject',
        projectionSource: 'OrderPlacedEvent -> StateTaxAllocationProjection',
        subtleRules: 'Origin tax rate applies to handling; destination tax rate applies to goods.',
      },
      {
        id: 'ann-3',
        number: 3,
        title: 'Two-Phase Settlement Payout Hold',
        businessIntent: 'State transition into SETTLED is guarded until both gateway bank batch capture and tax split verification emit confirmation tokens.',
        domainEntityMapping: 'SettlementBatchAggregate.hold_status',
        projectionSource: 'BankClearingStatusUpdatedEvent -> SettlementAggregate',
        subtleRules: 'If settlement token is absent after 48h, triggers automatic SLA escalation.',
      },
    ],
  },
  {
    id: 'screen_invoice_settlement_mobile',
    sliceId: 'slice_mobile_settlement',
    title: 'Mobile Merchant Settlement Dashboard',
    description: 'Compact mobile viewport layout for merchant instant payout and reserve ratio inspection.',
    viewport: 'mobile_375x667',
    category: 'Merchant Mobile',
    component: InvoiceSettlementMobileScreen,
    annotations: [
      {
        id: 'ann-m1',
        number: 1,
        title: 'Instant Payout Cap & Reserve Ratio',
        businessIntent: 'Mobile instant payouts are strictly bounded to 80% of net settled funds; remaining 20% remains in rolling 7-day reserve buffer.',
        domainEntityMapping: 'MerchantPayoutPolicy.instant_payout_reserve_fraction',
        projectionSource: 'PayoutRequestedEvent -> MerchantBalanceProjection',
      },
    ],
  },
];
