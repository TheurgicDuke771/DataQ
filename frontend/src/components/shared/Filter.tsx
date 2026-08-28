import { Flex, Typography } from 'antd';
import type { ReactNode } from 'react';

/** A labelled filter control — one `secondary` caption above the control, so a
 *  growing filter bar stays scannable and wraps cleanly on narrow viewports. */
export function Filter({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Flex vertical gap={4}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
      </Typography.Text>
      {children}
    </Flex>
  );
}
