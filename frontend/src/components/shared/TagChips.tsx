import { Tag as AntTag } from 'antd';

/**
 * An entity's tags as small chips. Renders nothing for no tags — an empty row would read as a
 * layout gap, and "untagged" is not information the row needs to spell out. The group is a list
 * with an accessible name so a screen reader announces "Tags, 2 items" rather than two bare words.
 */
export function TagChips({
  tags,
  label = 'Tags',
  size = 'default',
}: {
  tags: string[] | undefined;
  /** The list's accessible name — qualify it when several lists share a view. */
  label?: string;
  size?: 'default' | 'small';
}) {
  if (!tags || tags.length === 0) return null;
  return (
    <ul
      aria-label={label}
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 4,
        listStyle: 'none',
        margin: 0,
        padding: 0,
        minWidth: 0,
      }}
    >
      {tags.map((tag) => (
        <li key={tag} style={{ display: 'inline-flex' }}>
          <AntTag
            color="geekblue"
            style={{
              marginInlineEnd: 0,
              fontSize: size === 'small' ? 11 : undefined,
              lineHeight: size === 'small' ? '18px' : undefined,
            }}
          >
            {tag}
          </AntTag>
        </li>
      ))}
    </ul>
  );
}
