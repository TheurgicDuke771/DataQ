import { App, type ModalFuncProps } from 'antd';
import { useMemo } from 'react';

import { MODAL_TRANSITION } from '../components/shared/Modal';

/** `App.useApp().modal`, with every dialog opening with the app's fade instead of a zoom
 *  (#2466), and a destructive one opening on Cancel (#1672). ESLint rejects taking `modal`
 *  from `App.useApp()` directly. */
export function useAppModal() {
  const { modal } = App.useApp();
  return useMemo(() => {
    const withFade = (config: ModalFuncProps): ModalFuncProps => {
      // A destructive confirm opens on Cancel, not on its danger button (#1672): antd focuses
      // OK by default, so Enter pressed as it opens would delete.
      const danger = config.okType === 'danger' || config.okButtonProps?.danger === true;
      const chosen = config.focusable?.autoFocusButton;
      return {
        ...config,
        transitionName: config.transitionName ?? MODAL_TRANSITION,
        focusable: {
          ...config.focusable,
          autoFocusButton: chosen !== undefined ? chosen : danger ? 'cancel' : undefined,
        },
      };
    };
    return {
      confirm: (config: ModalFuncProps) => modal.confirm(withFade(config)),
      info: (config: ModalFuncProps) => modal.info(withFade(config)),
      success: (config: ModalFuncProps) => modal.success(withFade(config)),
      error: (config: ModalFuncProps) => modal.error(withFade(config)),
      warning: (config: ModalFuncProps) => modal.warning(withFade(config)),
    };
  }, [modal]);
}
