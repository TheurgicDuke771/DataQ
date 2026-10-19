import type { ModalFuncProps, ModalProps } from 'antd';
import { render, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Modal, MODAL_TRANSITION } from '../../src/components/shared/Modal';
import { useAppModal } from '../../src/hooks/useAppModal';

// Capture what reaches antd: jsdom does no layout, so the transition is checked as the prop
// antd receives, not as an animation.
const antModalProps = vi.fn<(props: ModalProps) => void>();
// One spy per dialog kind, so a method wired to the wrong antd call fails its own test.
const modalApi = {
  confirm: vi.fn<(config: ModalFuncProps) => void>(),
  info: vi.fn<(config: ModalFuncProps) => void>(),
  success: vi.fn<(config: ModalFuncProps) => void>(),
  error: vi.fn<(config: ModalFuncProps) => void>(),
  warning: vi.fn<(config: ModalFuncProps) => void>(),
};

vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const Modal = (props: ModalProps) => {
    antModalProps(props);
    return null;
  };
  const App = { useApp: () => ({ modal: modalApi }) };
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

  it('keeps the fade when a wrapper forwards an undefined transitionName', () => {
    render(<Modal open transitionName={undefined} />);
    expect(antModalProps).toHaveBeenCalledWith(
      expect.objectContaining({ transitionName: 'ant-fade' }),
    );
  });
});

describe('useAppModal (#2466)', () => {
  it.each(['confirm', 'info', 'success', 'error', 'warning'] as const)(
    '%s() opens with the fade and keeps the rest of the config',
    (kind) => {
      const { result } = renderHook(() => useAppModal());
      result.current[kind]({ title: 'Delete “orders”?', okType: 'danger' });
      expect(modalApi[kind]).toHaveBeenCalledWith({
        transitionName: 'ant-fade',
        title: 'Delete “orders”?',
        okType: 'danger',
      });
      for (const other of Object.keys(modalApi) as (keyof typeof modalApi)[]) {
        if (other !== kind) expect(modalApi[other]).not.toHaveBeenCalled();
      }
    },
  );

  it('keeps the fade when a caller forwards an undefined transitionName', () => {
    const { result } = renderHook(() => useAppModal());
    result.current.confirm({ title: 'Sure?', transitionName: undefined });
    expect(modalApi.confirm).toHaveBeenCalledWith({ title: 'Sure?', transitionName: 'ant-fade' });
  });
});
