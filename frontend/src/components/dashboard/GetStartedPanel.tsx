import { CheckCircleFilled } from '@ant-design/icons';
import { Button, Card, Flex, Typography, theme } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { getOnboardingStatus, type OnboardingStatus } from '../../api/dashboard';
import { useCanAuthor, useCanMutateConnections, useWorkspaceRole } from '../../auth/useMe';
import { useAsyncData } from '../../hooks/useAsyncData';

const DISMISSED_KEY = 'dataq.getStarted.dismissed';

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY) === '1';
  } catch {
    return false;
  }
}

interface Step {
  key: keyof Omit<OnboardingStatus, 'complete'>;
  title: string;
  detail: string;
  /** Where the step is done. Absent when this user cannot do it. */
  action?: { label: string; to: string };
  /** Shown instead of the action when this user cannot do the step. */
  blocked?: string;
}

/**
 * The first-run path on `/dashboard` (#1668): connect, create a suite, add a check, run it.
 * Workspace-wide, so it is the same for every member and goes away once all four are done.
 * Hidden while loading and on error — a getting-started prompt is never worth an error banner.
 */
export function GetStartedPanel() {
  const { state } = useAsyncData(() => getOnboardingStatus());
  const [dismissed, setDismissed] = useState(readDismissed);
  const role = useWorkspaceRole();
  const canConnect = useCanMutateConnections();
  const canAuthor = useCanAuthor();
  const navigate = useNavigate();
  const { token } = theme.useToken();

  if (state.status !== 'ok' || state.data.complete || dismissed) return null;
  const status = state.data;
  const known = role !== null;

  const steps: Step[] = [
    {
      key: 'has_datasource',
      title: 'Connect a data source',
      detail: 'A warehouse, database or file store that checks will run against.',
      action: canConnect ? { label: 'Add a connection', to: '/connections/new' } : undefined,
      blocked: known && !canConnect ? 'A workspace admin adds connections.' : undefined,
    },
    {
      key: 'has_suite',
      title: 'Create a suite',
      detail: 'A suite is a set of checks on one table or file.',
      action: canAuthor ? { label: 'New suite', to: '/suites/new' } : undefined,
      blocked: known && !canAuthor ? 'Viewers cannot create suites.' : undefined,
    },
    {
      key: 'has_check',
      title: 'Add a check',
      detail: 'Open the suite and add a rule the data must meet.',
      action: canAuthor ? { label: 'Open suites', to: '/suites' } : undefined,
      blocked: known && !canAuthor ? 'Someone with edit access to a suite adds checks.' : undefined,
    },
    {
      key: 'has_run',
      title: 'Run the suite',
      detail: 'Run it now from the suite, or give it a schedule.',
      action: canAuthor ? { label: 'Open suites', to: '/suites' } : undefined,
      blocked: known && !canAuthor ? 'Someone with edit access to a suite runs it.' : undefined,
    },
  ];
  // Only the first step not yet done offers its action: the later ones depend on it.
  const nextIndex = steps.findIndex((s) => !status[s.key]);
  const doneCount = steps.filter((s) => status[s.key]).length;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISSED_KEY, '1');
    } catch {
      // Private mode or blocked storage: hidden for this visit only.
    }
    setDismissed(true);
  };

  return (
    <Card
      size="small"
      title={`Get started — ${doneCount} of ${steps.length} done`}
      data-testid="get-started-panel"
      extra={
        <Button type="link" size="small" onClick={dismiss}>
          Hide
        </Button>
      }
    >
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {steps.map((step, index) => {
          const done = status[step.key];
          const isNext = index === nextIndex;
          const action = step.action;
          return (
            <li key={step.key} style={{ padding: '8px 0' }}>
              <Flex gap={12} align="flex-start" wrap>
                <span aria-hidden style={{ width: 20, textAlign: 'center' }}>
                  {done ? (
                    <CheckCircleFilled style={{ color: token.colorSuccess }} />
                  ) : (
                    <Typography.Text type="secondary">{index + 1}</Typography.Text>
                  )}
                </span>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <Typography.Text strong={isNext}>{step.title}</Typography.Text>
                  <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                    {done ? 'Done' : step.detail}
                  </Typography.Text>
                  {isNext && step.blocked && (
                    <Typography.Text type="secondary" style={{ display: 'block' }}>
                      {step.blocked}
                    </Typography.Text>
                  )}
                </div>
                {isNext && action && (
                  <Button type="primary" size="small" onClick={() => navigate(action.to)}>
                    {action.label}
                  </Button>
                )}
              </Flex>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
