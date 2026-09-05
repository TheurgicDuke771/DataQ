import { Tabs, Typography } from 'antd';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { Page } from '../../components/layout/Page';

/** The admin sub-pages, in tab order. The key IS the route segment (#1694). */
const ADMIN_TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'members', label: 'Members' },
  { key: 'suites', label: 'Suites' },
  { key: 'settings', label: 'Settings' },
  { key: 'compliance', label: 'Compliance' },
  { key: 'integrations', label: 'Integrations' },
] as const;

const KEYS = ADMIN_TABS.map((t) => t.key) as readonly string[];

/** Admin control centre shell: title + URL-backed tabs over the routed sub-pages. */
export function AdminLayout() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const segment = pathname.replace(/^\/admin\/?/, '').split('/')[0];
  const active = KEYS.includes(segment) ? segment : 'overview';

  return (
    <Page gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Admin
      </Typography.Title>
      <Tabs
        activeKey={active}
        onChange={(key) => navigate(`/admin/${key}`)}
        items={ADMIN_TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      <Outlet />
    </Page>
  );
}
