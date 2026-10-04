import { App, Button, Card, Flex, List, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  type Suggestion,
  acceptSuggestion,
  listSuggestions,
  rejectSuggestion,
} from '../../api/suggestions';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';

/**
 * Rules automatic coverage proposes for this table (ADR 0047): each asserts something about the
 * data, so a person decides. Accepting adds the check; rejecting means it is not proposed again.
 * Renders nothing when the queue is empty.
 */
export function SuggestionsPanel({
  suiteId,
  canDecide,
  onAccepted,
}: {
  suiteId: string;
  canDecide: boolean;
  onAccepted: () => void;
}) {
  const { message } = App.useApp();
  const { state, reload } = useAsyncData(() => listSuggestions(suiteId));
  const [busy, setBusy] = useState<string>();

  if (state.status !== 'ok' || state.data.length === 0) return null;

  const decide = async (item: Suggestion, accept: boolean) => {
    setBusy(item.id);
    try {
      if (accept) {
        await acceptSuggestion(item.id);
        onAccepted();
      } else {
        await rejectSuggestion(item.id);
      }
      reload();
    } catch (err) {
      message.error(errorMessage(err, 'Could not record the decision.'));
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <Card
      size="small"
      data-testid="suggestions-panel"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Suggested rules ({state.data.length})</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Proposed from this table’s own data, or by the AI model where marked. Accepting adds the
            check; a rejected rule is not suggested again.
          </Typography.Text>
        </Flex>
      }
    >
      <List
        size="small"
        dataSource={state.data}
        renderItem={(item) => (
          <List.Item
            actions={
              canDecide
                ? [
                    <Button
                      key="accept"
                      size="small"
                      type="primary"
                      loading={busy === item.id}
                      onClick={() => void decide(item, true)}
                    >
                      Accept
                    </Button>,
                    <Button
                      key="reject"
                      size="small"
                      disabled={busy === item.id}
                      onClick={() => void decide(item, false)}
                    >
                      Reject
                    </Button>,
                  ]
                : []
            }
          >
            <List.Item.Meta
              title={
                <Flex gap={8} align="center" wrap>
                  <span>{item.name}</span>
                  {/* An AI-proposed rule passed the same validator, but its reason is the
                      model's wording, not a measured fact like the profile's. */}
                  {item.source === 'llm' && <Tag>AI-suggested</Tag>}
                </Flex>
              }
              description={item.rationale ?? item.expectation_type}
            />
          </List.Item>
        )}
      />
    </Card>
  );
}
