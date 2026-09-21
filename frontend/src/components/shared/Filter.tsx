import { Flex, Typography } from 'antd';
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react';

/** A labelled filter control — one `secondary` caption above the control, so a
 *  growing filter bar stays scannable and wraps cleanly on narrow viewports. The caption is a
 *  real `<label>` bound to the control's `id` (antd forwards `id` to the inner input). */
export function Filter({ label, children }: { label: string; children: ReactNode }) {
  const generatedId = useId();
  const child = isValidElement(children) ? (children as ReactElement<{ id?: string }>) : null;
  const controlId = child?.props.id ?? generatedId;
  return (
    <Flex vertical gap={4}>
      <label htmlFor={controlId} style={{ lineHeight: 1 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Typography.Text>
      </label>
      {child ? cloneElement(child, { id: controlId }) : children}
    </Flex>
  );
}
