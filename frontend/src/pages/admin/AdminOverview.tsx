import { AppstoreOutlined, KeyOutlined, TeamOutlined } from '@ant-design/icons';
import { Col, Flex, Row, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { listAdminAccess, listAdminSuites, listAdminUsers } from '../../api/admin';
import { MetricCard } from '../../components/dashboard/MetricCard';
import { useAsyncData } from '../../hooks/useAsyncData';
import { count } from './asyncHelpers';
import { Section } from './parts';

/** Workspace shape at a glance. Health signals are #1885/#1696 — until they exist
 *  this page says so rather than rendering a green tick nothing measured. */
export function AdminOverview() {
  const suites = useAsyncData(listAdminSuites);
  const users = useAsyncData(listAdminUsers);
  const access = useAsyncData(listAdminAccess);

  return (
    <Flex vertical gap={16}>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Suites"
            value={count(suites.state)}
            loading={suites.state.status === 'loading'}
            icon={<AppstoreOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Members"
            value={count(users.state)}
            loading={users.state.status === 'loading'}
            icon={<TeamOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Access grants"
            value={count(access.state)}
            loading={access.state.status === 'loading'}
            icon={<KeyOutlined />}
          />
        </Col>
      </Row>

      <Section title="Needs attention">
        <Typography.Text type="secondary">
          Not monitored yet — poll staleness, beat heartbeat, queue depth and datasource credential
          health have no read API, so this workspace has no health feed to show. Nothing here means
          nothing is being watched, not that everything is fine.
        </Typography.Text>
        <Flex gap={16} wrap>
          <Link to="/admin/members">Members &amp; access grants</Link>
          <Link to="/admin/suites">All suites</Link>
          <Link to="/admin/compliance">Audit log &amp; deployment posture</Link>
          <Link to="/admin/integrations">Inbound webhooks</Link>
        </Flex>
      </Section>
    </Flex>
  );
}
