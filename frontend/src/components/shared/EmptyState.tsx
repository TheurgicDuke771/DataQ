import { Button, Empty, Typography } from 'antd';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * An empty list that says what goes here and what to do next (#1668). `action` is passed only
 * when the caller may take it; otherwise `note` says who can, so a missing button never reads
 * as a bug.
 */
export function EmptyState({
  title,
  description,
  action,
  note,
  compact = false,
}: {
  title: string;
  description?: ReactNode;
  action?: { label: string; to?: string; onClick?: () => void };
  note?: string;
  compact?: boolean;
}) {
  const navigate = useNavigate();
  return (
    <Empty
      image={compact ? Empty.PRESENTED_IMAGE_SIMPLE : undefined}
      description={
        <>
          <Typography.Text strong style={{ display: 'block' }}>
            {title}
          </Typography.Text>
          {description && <Typography.Text type="secondary">{description}</Typography.Text>}
          {note && (
            <Typography.Text type="secondary" style={{ display: 'block' }}>
              {note}
            </Typography.Text>
          )}
        </>
      }
    >
      {action && (
        <Button type="primary" onClick={action.onClick ?? (() => action.to && navigate(action.to))}>
          {action.label}
        </Button>
      )}
    </Empty>
  );
}
