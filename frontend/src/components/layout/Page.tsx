import { Flex } from 'antd';
import type { ReactNode } from 'react';

/**
 * Per-screen content-column widths, mirroring the prototype (ADR 0022): list / dashboard screens
 * fill a wide column, authoring forms sit in a narrow column.
 */
const WIDTHS = { wide: 1200, picker: 880, form: 720 } as const;
type PageWidthName = keyof typeof WIDTHS;

/** Centered page content column. */
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
