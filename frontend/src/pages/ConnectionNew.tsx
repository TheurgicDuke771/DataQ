import { Button, Card, Flex, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  CONNECTION_KIND_LABELS,
  CONNECTION_KINDS,
  CONNECTION_TYPE_LABELS,
  type ConnectionKind,
  type ConnectionType,
  typesOfKind,
} from '../api/connections';
import { ConnectionForm } from '../components/connections/ConnectionForm';

/**
 * Dedicated full-page add-connection flow (GX-Cloud style): pick a type from the
 * datasource / orchestration sections, then fill the type-specific form (shared
 * with the edit page via `ConnectionForm`). Editing an existing connection is the
 * dedicated `/connections/:id/edit` page.
 */
export function ConnectionNew() {
  const navigate = useNavigate();
  const [type, setType] = useState<ConnectionType>();

  return (
    <Flex vertical gap={24} style={{ maxWidth: 640 }}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {type ? `New ${CONNECTION_TYPE_LABELS[type]} connection` : 'New connection'}
        </Typography.Title>
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
        <Flex vertical gap={24}>
          {CONNECTION_KINDS.map((kind) => (
            <TypeSection key={kind} kind={kind} types={typesOfKind(kind)} onPick={setType} />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

function TypeSection({
  kind,
  types,
  onPick,
}: {
  kind: ConnectionKind;
  types: ConnectionType[];
  onPick: (type: ConnectionType) => void;
}) {
  return (
    <Flex vertical gap={12}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        {CONNECTION_KIND_LABELS[kind]}
      </Typography.Title>
      <Flex wrap gap={12}>
        {types.map((type) => (
          <Card
            key={type}
            hoverable
            size="small"
            style={{ minWidth: 200 }}
            onClick={() => onPick(type)}
          >
            <Typography.Text strong>{CONNECTION_TYPE_LABELS[type]}</Typography.Text>
          </Card>
        ))}
      </Flex>
    </Flex>
  );
}
