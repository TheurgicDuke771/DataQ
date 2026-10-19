import { App, type ModalFuncProps } from 'antd';
import { useMemo } from 'react';

import { MODAL_TRANSITION } from '../components/shared/Modal';

/** `App.useApp().modal`, with every dialog opening with the app's fade instead of a zoom
 *  (#2466). ESLint rejects taking `modal` from `App.useApp()` directly. */
export function useAppModal() {
  const { modal } = App.useApp();
  return useMemo(() => {
    const withFade = (config: ModalFuncProps): ModalFuncProps => ({
      transitionName: MODAL_TRANSITION,
      ...config,
    });
    return {
      confirm: (config: ModalFuncProps) => modal.confirm(withFade(config)),
      info: (config: ModalFuncProps) => modal.info(withFade(config)),
      success: (config: ModalFuncProps) => modal.success(withFade(config)),
      error: (config: ModalFuncProps) => modal.error(withFade(config)),
      warning: (config: ModalFuncProps) => modal.warning(withFade(config)),
    };
  }, [modal]);
}
