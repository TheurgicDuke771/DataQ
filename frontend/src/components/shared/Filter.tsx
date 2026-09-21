import { Flex, Typography } from 'antd';
import {
  Children,
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from 'react';

/** A labelled filter control — one `secondary` caption above the control, so a
 *  growing filter bar stays scannable and wraps cleanly on narrow viewports. The caption is a
 *  real `<label>` bound to the control's `id` (antd forwards `id` to the inner input). The
 *  control is the FIRST element child; anything after it (a validation hint) renders as-is. */
export function Filter({ label, children }: { label: string; children: ReactNode }) {
  const generatedId = useId();
  const nodes = Children.toArray(children);
  const controlIndex = nodes.findIndex((n) => isValidElement(n));
  const control = nodes[controlIndex] as ReactElement<{ id?: string }> | undefined;
  const controlId = control?.props.id ?? generatedId;
  return (
    <Flex vertical gap={4}>
      <label htmlFor={controlId} style={{ lineHeight: 1 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Typography.Text>
      </label>
      {nodes.map((n, i) =>
        i === controlIndex && control ? cloneElement(control, { id: controlId }) : n,
      )}
    </Flex>
  );
}
