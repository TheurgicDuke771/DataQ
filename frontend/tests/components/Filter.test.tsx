import { render, screen } from '@testing-library/react';
import { Input, Select } from 'antd';
import { describe, expect, it } from 'vitest';

import { Filter } from '../../src/components/shared/Filter';

describe('Filter', () => {
  it('binds its caption to an antd Select as a real label', () => {
    render(
      <Filter label="Status">
        <Select options={[{ value: 'all', label: 'All' }]} value="all" />
      </Filter>,
    );
    expect(screen.getByRole('combobox', { name: 'Status' })).toBeInTheDocument();
  });

  it('keeps a control id the caller already set', () => {
    render(
      <Filter label="Pipeline">
        <Input id="pipeline-input" />
      </Filter>,
    );
    expect(screen.getByLabelText('Pipeline')).toHaveAttribute('id', 'pipeline-input');
  });

  it('gives two filters with the same caption distinct controls', () => {
    render(
      <>
        <Filter label="Date">
          <Input />
        </Filter>
        <Filter label="Date">
          <Input />
        </Filter>
      </>,
    );
    const [a, b] = screen.getAllByLabelText('Date');
    expect(a.id).not.toBe(b.id);
  });
});
