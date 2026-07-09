import {
  BuildOutlined,
  CloudOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  NodeIndexOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { Flex } from 'antd';
import type { ReactNode } from 'react';

import type { ConnectionType } from '../../api/connections';

/**
 * Per-datasource visual identity (icon + brand-ish accent) so the connection
 * list reads as recognisable products rather than a wall of identical cards.
 * Single source for the glyph + colour — the card avatar and anywhere else that
 * wants a type marker both read from here.
 */
const TYPE_VISUAL: Record<ConnectionType, { icon: ReactNode; color: string }> = {
  snowflake: { icon: <CloudOutlined />, color: '#29b5e8' },
  adls_gen2: { icon: <FolderOpenOutlined />, color: '#0078d4' },
  s3: { icon: <InboxOutlined />, color: '#ff9900' },
  unity_catalog: { icon: <TableOutlined />, color: '#ff3621' },
  iceberg: { icon: <DatabaseOutlined />, color: '#2596be' },
  adf: { icon: <DeploymentUnitOutlined />, color: '#0078d4' },
  airflow: { icon: <NodeIndexOutlined />, color: '#017cee' },
  dbt: { icon: <BuildOutlined />, color: '#ff694b' },
};

/** A rounded-square icon avatar tinted with the datasource's accent colour. */
export function ConnectionTypeAvatar({ type, size = 40 }: { type: ConnectionType; size?: number }) {
  const { icon, color } = TYPE_VISUAL[type];
  return (
    <Flex
      align="center"
      justify="center"
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: 10,
        // A soft tint of the accent (≈12% alpha) behind the full-strength glyph.
        background: `${color}1f`,
        color,
        fontSize: size * 0.5,
      }}
    >
      {icon}
    </Flex>
  );
}
