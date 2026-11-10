import React, { useState } from 'react';
import { Box, Flex, Stack, Card, Button, TextPlaceholder } from '../primitives';
import { Drawer, AnnotationBadge } from '../components';

export const InvoiceSettlementMobileScreen: React.FC = () => {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  return (
    <Box padding={16} bg="#ffffff" style={{ minHeight: 667, width: '100%', maxWidth: 375, margin: '0 auto' }}>
      {/* Mobile Top Bar */}
      <Flex justify="space-between" align="center" style={{ marginBottom: 16, borderBottom: '1px solid #e2e8f0', paddingBottom: 12 }}>
        <h2 style={{ fontSize: '1.1rem', fontWeight: 700, margin: 0, color: '#0f172a' }}>
          Mobile Settlement
        </h2>
        <Button variant="ghost" size="sm" onClick={() => setIsDrawerOpen(true)}>
          ☰ Menu
        </Button>
      </Flex>

      {/* Mobile Balance Card with Annotation 1 */}
      <Box position="relative" style={{ marginBottom: 16 }}>
        <Card variant="elevated" style={{ backgroundColor: '#1e293b', color: '#ffffff' }}>
          <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Available for Immediate Payout</div>
          <div style={{ fontSize: '1.7rem', fontWeight: 700, margin: '4px 0 8px 0', color: '#ffffff' }}>
            $24,850.00
          </div>
          <Flex gap={8}>
            <Button variant="primary" size="sm" style={{ flex: 1 }}>
              Withdraw Now
            </Button>
            <Button
              variant="outline"
              size="sm"
              style={{ color: '#ffffff', borderColor: '#475569' }}
              onClick={() => setIsDrawerOpen(true)}
            >
              Details
            </Button>
          </Flex>
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

      {/* Recent Payout Items */}
      <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: 8, color: '#0f172a' }}>
        Recent Activity
      </div>
      <Stack direction="vertical" gap={10} style={{ marginBottom: 16 }}>
        <Box padding={12} bg="#f8fafc" border="1px solid #e2e8f0" rounded={8}>
          <Flex justify="space-between" align="center">
            <div>
              <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>Batch Payout #BP-109</div>
              <div style={{ fontSize: '0.75rem', color: '#64748b' }}>Cleared via ACH</div>
            </div>
            <div style={{ fontWeight: 700, color: '#059669', fontSize: '0.9rem' }}>+$8,420.00</div>
          </Flex>
        </Box>

        <Box padding={12} bg="#f8fafc" border="1px solid #e2e8f0" rounded={8}>
          <Flex justify="space-between" align="center">
            <div>
              <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>Rolling Reserve Hold</div>
              <div style={{ fontSize: '0.75rem', color: '#64748b' }}>7-day hold cycle</div>
            </div>
            <div style={{ fontWeight: 700, color: '#b91c1c', fontSize: '0.9rem' }}>-$1,200.00</div>
          </Flex>
        </Box>
      </Stack>

      <TextPlaceholder lines={3} height={12} />

      {/* Interactive Mobile Drawer */}
      <Drawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        title="Payout & Policy Breakdown"
        placement="bottom"
      >
        <Stack direction="vertical" gap={12}>
          <div style={{ fontSize: '0.85rem', color: '#475569' }}>
            Merchant Tier: <strong>Level 2 Verified</strong>
          </div>
          <div style={{ fontSize: '0.85rem', color: '#475569' }}>
            Daily Payout Limit: <strong>$50,000.00 USD</strong>
          </div>
          <Button variant="secondary" onClick={() => setIsDrawerOpen(false)}>
            Close Sheet
          </Button>
        </Stack>
      </Drawer>
    </Box>
  );
};
