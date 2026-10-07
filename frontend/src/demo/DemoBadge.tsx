import { App, Flex, Tag, Typography } from 'antd';
import { useEffect } from 'react';

import { NOT_RECORDED_MESSAGE } from './adapter';
import { INSTALL_GUIDE_URL } from './flag';
import { subscribe, type DemoNotice } from './notices';

const INSTALL_LINK = (
  <a href={INSTALL_GUIDE_URL} target="_blank" rel="noreferrer">
    Run DataQ yourself in 5 minutes
  </a>
);

const NOTICES: Record<DemoNotice, { title: string; body: string }> = {
  'read-only': {
    title: 'Read-only demo',
    body: 'Nothing was changed. Saving, running and testing need a real DataQ.',
  },
  'not-recorded': { title: 'Not in the demo', body: NOT_RECORDED_MESSAGE },
  approximate: {
    title: 'Showing the default view',
    body: 'The demo replays recorded responses, so this filter or page is not applied.',
  },
};

/** The shell's "this is a demo" label, and the one place the adapter's refusals are explained. */
export function DemoBadge({ compact }: { compact: boolean }) {
  const { notification } = App.useApp();

  useEffect(
    () =>
      subscribe((kind) => {
        const notice = NOTICES[kind];
        // Keyed: a page that fires five refused requests shows one notice, not five.
        notification.info({
          key: `demo-${kind}`,
          title: notice.title,
          description: (
            <Flex vertical gap={4}>
              <span>{notice.body}</span>
              {INSTALL_LINK}
            </Flex>
          ),
          placement: 'bottomRight',
          duration: 8,
        });
      }),
    [notification],
  );

  return (
    <Flex align="center" gap={8} style={{ whiteSpace: 'nowrap' }}>
      <Tag color="gold" style={{ marginInlineEnd: 0 }}>
        {compact ? 'Demo' : 'Read-only demo · sample data'}
      </Tag>
      {!compact && <Typography.Text style={{ fontSize: 13 }}>{INSTALL_LINK}</Typography.Text>}
    </Flex>
  );
}
