import { createContext, useContext, type CSSProperties, type Key, type ReactNode } from 'react';
import { Flex, Typography, theme } from 'antd';

/**
 * A minimal stand-in for antd's `List`, which is deprecated in antd v6 and slated
 * for removal in v7 ("The `List` component is deprecated…"). It covers only the
 * subset the app actually used — `dataSource` + `renderItem`, `Item` (with
 * `actions`), and `Item.Meta` (title/description) — rendered as plain flex rows so
 * no deprecated component is on the tree. Visuals mirror antd's borderless list:
 * a hairline `colorSplit` divider between rows and size-aware vertical padding,
 * pulled from the live theme token so it tracks the app's antd theme.
 *
 * Intentionally NOT reintroducing the full List API (pagination, loadMore, grid,
 * bordered box, avatar-less split dots between actions): add only what a real call
 * site needs, so this stays a thin shim rather than a re-implementation.
 */

type ListSize = 'default' | 'small';

const SizeContext = createContext<ListSize>('default');

export interface SimpleListProps<T> {
  dataSource: readonly T[];
  renderItem: (item: T, index: number) => ReactNode;
  /** Row React key — a field name or a deriver. Falls back to the index. */
  rowKey?: keyof T | ((item: T) => Key);
  size?: ListSize;
  className?: string;
  style?: CSSProperties;
}

function SimpleListRoot<T>({
  dataSource,
  renderItem,
  rowKey,
  size = 'default',
  className,
  style,
}: SimpleListProps<T>) {
  const { token } = theme.useToken();

  const keyFor = (item: T, index: number): Key => {
    if (typeof rowKey === 'function') return rowKey(item);
    if (rowKey != null) return item[rowKey] as unknown as Key;
    return index;
  };

  return (
    <SizeContext.Provider value={size}>
      <div className={className} style={style} role="list">
        {dataSource.map((item, index) => (
          <div
            key={keyFor(item, index)}
            role="listitem"
            style={{
              // Hairline divider between rows (not after the last) — antd's
              // borderless-list look.
              borderBlockEnd:
                index < dataSource.length - 1 ? `1px solid ${token.colorSplit}` : undefined,
            }}
          >
            {renderItem(item, index)}
          </div>
        ))}
      </div>
    </SizeContext.Provider>
  );
}

export interface SimpleListItemProps {
  children?: ReactNode;
  /** Right-aligned controls (buttons, switches, tags). */
  actions?: ReactNode[];
  onClick?: React.MouseEventHandler<HTMLDivElement>;
  className?: string;
  style?: CSSProperties;
}

function SimpleListItem({ children, actions, onClick, className, style }: SimpleListItemProps) {
  const { token } = theme.useToken();
  const size = useContext(SizeContext);
  const paddingBlock = size === 'small' ? token.paddingXS : token.paddingSM;
  const hasActions = !!actions && actions.length > 0;

  return (
    <div
      className={className}
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: token.padding,
        paddingBlock,
        // A caller-supplied style wins (e.g. the Suites master row overrides
        // padding + sets its own selected background).
        ...style,
      }}
    >
      {/* Mirror antd's List.Item: with actions, children live in a flex-1 "main"
          region and the actions sit right; without, the bare children are the
          flex items themselves and `space-between` spreads them (how a two-child
          row lands its label left / status right). */}
      {hasActions ? (
        <>
          <div style={{ flex: '1 1 auto', minWidth: 0 }}>{children}</div>
          <Flex align="center" gap={token.paddingSM} style={{ flexShrink: 0 }}>
            {actions}
          </Flex>
        </>
      ) : (
        children
      )}
    </div>
  );
}

export interface SimpleListItemMetaProps {
  title?: ReactNode;
  description?: ReactNode;
}

function SimpleListItemMeta({ title, description }: SimpleListItemMetaProps) {
  const { token } = theme.useToken();
  return (
    <Flex vertical style={{ flex: 1, minWidth: 0, gap: token.paddingXXS }}>
      {title != null && <Typography.Text>{title}</Typography.Text>}
      {description != null && (
        <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
          {description}
        </Typography.Text>
      )}
    </Flex>
  );
}

const Item = Object.assign(SimpleListItem, { Meta: SimpleListItemMeta });

/** antd-`List`-shaped shim: `<SimpleList>` with `.Item` and `.Item.Meta`. */
// A compound component (function + `.Item` statics) reads to the react-refresh
// rule as a non-component export; it's the standard antd-style shape and safe here.
// eslint-disable-next-line react-refresh/only-export-components
export const SimpleList = Object.assign(SimpleListRoot, { Item });

export default SimpleList;
