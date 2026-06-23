import { RightOutlined } from '@ant-design/icons';
import { Button, Card, Flex, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { CONNECTION_TYPE_LABELS, type ConnectionType } from '../api/connections';
import { Page } from '../components/layout/Page';
import { ConnectionForm } from '../components/connections/ConnectionForm';
import {
  CONNECTION_BLURB,
  type SourceGroup,
  sourcesByCategory,
} from '../components/connections/connectionSources';
import { ConnectionTypeAvatar } from '../components/connections/connectionVisuals';

/**
 * Dedicated full-page add-connection flow (GX-Cloud style): step 1 picks a source
 * from the categorized grid (Orchestration first — ADR 0022), step 2 fills the
 * type-specific form (shared with the edit page via `ConnectionForm`). Editing an
 * existing connection is the dedicated `/connections/:id/edit` page.
 */
export function ConnectionNew() {
  const navigate = useNavigate();
  const [type, setType] = useState<ConnectionType>();

  return (
    <Page width={type ? 'form' : 'picker'}>
      <Flex justify="space-between" align="center" gap={12}>
        <Flex vertical gap={2}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {type ? `New ${CONNECTION_TYPE_LABELS[type]} connection` : 'New connection'}
          </Typography.Title>
          {!type && (
            <Typography.Text type="secondary">
              Select a source or orchestration provider to connect.
            </Typography.Text>
          )}
        </Flex>
        <Button onClick={() => (type ? setType(undefined) : navigate('/connections'))}>
          {type ? 'Back' : 'Cancel'}
        </Button>
      </Flex>

      {type ? (
        <Card size="small">
          <ConnectionForm
            type={type}
            onCancel={() => setType(undefined)}
            onSaved={() => navigate('/connections')}
          />
        </Card>
      ) : (
        <Flex vertical gap={28}>
          {sourcesByCategory().map((group) => (
            <SourceSection key={group.category} group={group} onPick={setType} />
          ))}
        </Flex>
      )}
    </Page>
  );
}

function SourceSection({
  group,
  onPick,
}: {
  group: SourceGroup;
  onPick: (type: ConnectionType) => void;
}) {
  return (
    <Flex vertical gap={group.note ? 6 : 12}>
      <Typography.Text
        type="secondary"
        strong
        style={{ fontSize: 12, letterSpacing: '0.05em', textTransform: 'uppercase' }}
      >
        {group.category}
      </Typography.Text>
      {group.note && (
        <Typography.Text type="secondary" style={{ maxWidth: 560 }}>
          {group.note}
        </Typography.Text>
      )}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
          gap: 14,
        }}
      >
        {group.types.map((type) => (
          <SourceCard key={type} type={type} onPick={onPick} />
        ))}
      </div>
    </Flex>
  );
}

function SourceCard({
  type,
  onPick,
}: {
  type: ConnectionType;
  onPick: (t: ConnectionType) => void;
}) {
  return (
    <Card
      hoverable
      className="dq-card--interactive"
      styles={{ body: { padding: 20 } }}
      onClick={() => onPick(type)}
      aria-label={`Add ${CONNECTION_TYPE_LABELS[type]} connection`}
    >
      {/* Vertical layout matching the Connections / Suites cards: avatar top-left,
          chevron affordance top-right, then name + blurb. */}
      <Flex vertical gap={14}>
        <Flex justify="space-between" align="flex-start">
          <ConnectionTypeAvatar type={type} size={48} />
          <RightOutlined style={{ color: '#bfbfbf', marginTop: 4 }} />
        </Flex>
        <Flex vertical gap={2} style={{ minWidth: 0 }}>
          <Typography.Text strong style={{ fontSize: 15 }}>
            {CONNECTION_TYPE_LABELS[type]}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }} ellipsis>
            {CONNECTION_BLURB[type]}
          </Typography.Text>
        </Flex>
      </Flex>
    </Card>
  );
}
