import { Skeleton } from 'antd';
import { Suspense, lazy } from 'react';

// Monaco lives in its own lazy chunk, pulled in only when a SQL field renders.
const LazySqlEditor = lazy(() => import('./SqlEditorField'));

export interface SqlEditorControlProps {
  value?: string;
  onChange?: (value: string) => void;
  /** The field's name for assistive technology: the label the Form.Item shows. */
  ariaLabel?: string;
  // What antd's Form.Item hands its child: help/extra/error ids, validation state.
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false';
  'aria-required'?: boolean | 'true' | 'false';
}

/** The SQL editor as a Form.Item child. Form.Item clones its only child to add `value`,
 *  `onChange` and the aria props, so the lazy boundary has to sit INSIDE this component: with
 *  `<Suspense>` as the Form.Item's child, those props went to Suspense and the editor never saw
 *  the form's value or reported a change. */
export function SqlEditorControl(props: SqlEditorControlProps) {
  return (
    <Suspense fallback={<Skeleton.Input active block style={{ height: 180 }} />}>
      <LazySqlEditor {...props} />
    </Suspense>
  );
}
