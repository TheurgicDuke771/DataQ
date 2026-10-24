import { EditOutlined } from '@ant-design/icons';
import { App, Button, Flex, Typography } from 'antd';
import { useState } from 'react';

import { updateAsset } from '../../api/assets';
import { errorMessage } from '../../utils/errors';
import { Modal } from '../shared/Modal';
import { TagChips } from '../shared/TagChips';
import { TagPicker } from '../shared/TagPicker';

/**
 * An asset's tags: chips for every member, and the workspace-Admin-only editor (asset metadata is
 * the Admin axis, like its description and owner — the backend 403s everyone else).
 */
export function AssetTagsBlock({
  assetId,
  tags,
  canEdit,
  onChanged,
}: {
  assetId: string;
  tags: string[] | undefined;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const current = tags ?? [];

  const openEditor = () => {
    setDraft(current);
    setEditing(true);
  };

  const onSave = async () => {
    setSaving(true);
    try {
      await updateAsset(assetId, { tags: draft });
      message.success('Tags updated');
      setEditing(false);
      onChanged();
    } catch (err) {
      message.error(`Update failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  if (current.length === 0 && !canEdit) return null;
  return (
    <>
      <Flex gap={8} align="center" wrap>
        <Typography.Text type="secondary" id="asset-tags-label">
          Tags
        </Typography.Text>
        {current.length > 0 ? (
          <TagChips tags={current} label="Asset tags" />
        ) : (
          <Typography.Text type="secondary">None yet.</Typography.Text>
        )}
        {canEdit && (
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            aria-label="Manage asset tags"
            onClick={openEditor}
          >
            Manage
          </Button>
        )}
      </Flex>
      <Modal
        title="Edit asset tags"
        open={editing}
        onOk={() => void onSave()}
        okText="Save"
        confirmLoading={saving}
        onCancel={() => setEditing(false)}
        destroyOnHidden
      >
        <TagPicker aria-label="Asset tags" value={draft} onChange={setDraft} />
      </Modal>
    </>
  );
}
