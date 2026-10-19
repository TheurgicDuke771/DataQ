import type { ModalFuncProps, ModalProps } from 'antd';
import { render, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Modal, MODAL_TRANSITION } from '../../src/components/shared/Modal';
import { useAppModal } from '../../src/hooks/useAppModal';

// Capture what reaches antd: jsdom does no layout, so the transition is checked as the prop
// antd receives, not as an animation.
const antModalProps = vi.fn<(props: ModalProps) => void>();
const confirm = vi.fn<(config: ModalFuncProps) => void>();

vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const Modal = (props: ModalProps) => {
    antModalProps(props);
    return null;
  };
  const App = { useApp: () => ({ modal: { confirm, info: confirm } }) };
  return { ...actual, Modal, App };
});

afterEach(() => vi.clearAllMocks());

describe('shared Modal (#2466)', () => {
  it('opens with the fade transition, not the zoom', () => {
    render(<Modal open title="Add member" />);
    expect(MODAL_TRANSITION).toBe('ant-fade');
    expect(antModalProps).toHaveBeenCalledWith(
      expect.objectContaining({ transitionName: 'ant-fade', open: true, title: 'Add member' }),
    );
  });

  it('lets a caller choose another transition explicitly', () => {
    render(<Modal open transitionName="ant-zoom" />);
    expect(antModalProps).toHaveBeenCalledWith(
      expect.objectContaining({ transitionName: 'ant-zoom' }),
    );
  });
});

describe('useAppModal (#2466)', () => {
  it('adds the fade transition to confirm dialogs and keeps the rest of the config', () => {
    const { result } = renderHook(() => useAppModal());
    result.current.confirm({ title: 'Delete “orders”?', okType: 'danger' });
    expect(confirm).toHaveBeenCalledWith({
      transitionName: 'ant-fade',
      title: 'Delete “orders”?',
      okType: 'danger',
    });
  });

  it('applies the fade to the other dialog kinds too', () => {
    const { result } = renderHook(() => useAppModal());
    result.current.info({ title: 'Heads up' });
    expect(confirm).toHaveBeenCalledWith({ transitionName: 'ant-fade', title: 'Heads up' });
  });
});
