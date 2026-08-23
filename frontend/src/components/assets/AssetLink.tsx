import { DatabaseOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import { useNavigate } from 'react-router-dom';

/**
 * A small "Asset" link chip (#773, navigation inversion) — surfaces the asset a suite or run
 * resolves to and navigates to `/assets/:assetId` on click.
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
