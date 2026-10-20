import { Modal as AntModal, type ModalProps } from 'antd';

/** The transition every dialog opens with (#2466). antd's default zooms the dialog in from
 *  a small scale, and a Select inside it sizes its dropdown from the trigger's on-screen
 *  width when it opens: clicked during the zoom, the dropdown stays a sliver of the field.
 *  A fade never scales, so the measurement is always the real width. */
export const MODAL_TRANSITION = 'ant-fade';

/** antd's Modal, opening with a fade instead of a zoom. Use it for every dialog; ESLint
 *  rejects importing `Modal` from antd directly. For `confirm()`-style dialogs, use
 *  `useAppModal`. */
export function Modal(props: ModalProps) {
  return <AntModal {...props} transitionName={props.transitionName ?? MODAL_TRANSITION} />;
}
