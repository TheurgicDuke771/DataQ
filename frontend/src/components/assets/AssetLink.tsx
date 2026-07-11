import { DatabaseOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import { useNavigate } from 'react-router-dom';

/**
 * A small "Asset" link chip (#773, navigation inversion) — surfaces the asset a
 * suite or run resolves to and navigates to `/assets/:assetId` on click. Renders
 * nothing when `assetId` is null (a targetless/unresolvable suite has no asset).
 *
 * Deliberately label-only ("Asset"): the suite/run read carries just `asset_id`,
 * not the asset's name, and this stays a cheap link rather than triggering an
 * extra fetch per row. The asset page itself shows the full identity.
 */
export function AssetLink({ assetId }: { assetId: string | null | undefined }) {
  const navigate = useNavigate();
  if (!assetId) return null;
  return (
    <Tag
      icon={<DatabaseOutlined />}
      color="blue"
      onClick={() => navigate(`/assets/${assetId}`)}
      style={{ cursor: 'pointer', marginInlineEnd: 0 }}
    >
      Asset
    </Tag>
  );
}
