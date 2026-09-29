import { RobotOutlined } from '@ant-design/icons';
import { Tag, Tooltip } from 'antd';

/** Marks a suite or check that automatic coverage created and maintains (ADR 0047). */
export function AutomaticTag({ origin }: { origin?: string }) {
  if (origin !== 'auto') return null;
  return (
    <Tooltip title="Created by automatic coverage. Edits you make are kept; a check you delete is not added back.">
      <Tag icon={<RobotOutlined />} style={{ marginInlineEnd: 0 }}>
        Automatic
      </Tag>
    </Tooltip>
  );
}
