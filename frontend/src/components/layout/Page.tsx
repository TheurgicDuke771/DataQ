import { Flex } from 'antd';
import type { ReactNode } from 'react';

/**
 * Per-screen content-column widths, mirroring the prototype (ADR 0022): list /
 * dashboard screens fill a wide column, authoring forms sit in a narrow column,
 * and the connection source picker is in between.
 */
const WIDTHS = { wide: 1200, picker: 880, form: 720 } as const;
type PageWidthName = keyof typeof WIDTHS;

/**
 * Centered page content column. The prototype wraps every screen in
 * `maxWidth + margin: 0 auto`, so content is centred in the canvas instead of
 * hugging the sider on wide displays. This component is the one place that
 * contract lives — pages use it as their root rather than each re-deriving the
 * `marginInline: 'auto'` dance. `width` takes a named preset (`wide` default,
 * `form`, `picker`) or a raw pixel number for one-off columns.
 */
export function Page({
  width = 'wide',
  gap = 24,
  children,
}: {
  width?: PageWidthName | number;
  /** Vertical gap between the page's stacked sections (header, body, …). */
  gap?: number;
  children: ReactNode;
}) {
  const maxWidth = typeof width === 'number' ? width : WIDTHS[width];
  return (
    <Flex vertical gap={gap} style={{ width: '100%', maxWidth, marginInline: 'auto' }}>
      {children}
    </Flex>
  );
}
