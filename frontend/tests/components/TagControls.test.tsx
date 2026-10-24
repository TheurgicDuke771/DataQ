import { App as AntApp, Form } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { updateAsset } from '../../src/api/assets';
import { listTags, normalizeTagName, type Tag } from '../../src/api/tags';
import { AssetTagsBlock } from '../../src/components/assets/AssetTagsBlock';
import { TagChips } from '../../src/components/shared/TagChips';
import { TagFilter } from '../../src/components/shared/TagFilter';
import { TagPicker } from '../../src/components/shared/TagPicker';

vi.mock('../../src/api/tags', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/tags')>();
  return { ...actual, listTags: vi.fn() };
});
vi.mock('../../src/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/assets')>();
  return { ...actual, updateAsset: vi.fn() };
});

const mockListTags = vi.mocked(listTags);
const mockUpdateAsset = vi.mocked(updateAsset);

function tag(name: string): Tag {
  return {
    id: `t-${name}`,
    name,
    created_at: '2026-10-24T00:00:00Z',
    usage: { suites: 1, assets: 0, connections: 0, checks: 0 },
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('normalizeTagName', () => {
  it('matches the server: lower-case, trimmed, single-spaced', () => {
    expect(normalizeTagName('  Core   Data ')).toBe('core data');
    expect(normalizeTagName('team:Finance')).toBe('team:finance');
    expect(normalizeTagName('   ')).toBe('');
  });
});

describe('TagChips', () => {
  it('renders a named list of chips', () => {
    render(<TagChips tags={['daily', 'finance']} label="orders tags" />);
    const list = screen.getByRole('list', { name: 'orders tags' });
    expect(
      within(list)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['daily', 'finance']);
  });

  it('renders nothing for no tags — an empty list would read as a gap', () => {
    const { container } = render(<TagChips tags={[]} />);
    expect(container).toBeEmptyDOMElement();
    const { container: absent } = render(<TagChips tags={undefined} />);
    expect(absent).toBeEmptyDOMElement();
  });
});

describe('TagPicker', () => {
  it('offers the workspace vocabulary and normalizes a typed name before onChange', async () => {
    mockListTags.mockResolvedValue([tag('finance'), tag('daily')]);
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<TagPicker aria-label="Tags" value={[]} onChange={onChange} />);
    const input = await screen.findByRole('combobox', { name: 'Tags' });
    await waitFor(() => expect(mockListTags).toHaveBeenCalledTimes(1));

    await user.click(input);
    expect(await screen.findByTitle('finance')).toBeInTheDocument();
    expect(screen.getByTitle('daily')).toBeInTheDocument();

    // jsdom's antd Select never commits a typed value on Enter (no layout → no active option
    // to pick), so the test clicks the option the typed text created, as a user also can.
    await user.type(input, '  Core  Data');
    await user.click(await screen.findByTitle('Core Data'));
    expect(onChange).toHaveBeenLastCalledWith(['core data']);
  });

  it('collapses a duplicate that differs only by case', async () => {
    mockListTags.mockResolvedValue([tag('finance')]);
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<TagPicker aria-label="Tags" value={['finance']} onChange={onChange} />);
    const input = await screen.findByRole('combobox', { name: 'Tags' });
    await user.type(input, 'Finance');
    await user.click(await screen.findByTitle('Finance'));
    expect(onChange).toHaveBeenLastCalledWith(['finance']);
  });

  it('degrades to free typing when the vocabulary cannot be loaded', async () => {
    mockListTags.mockRejectedValue(new Error('boom'));
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<TagPicker aria-label="Tags" value={[]} onChange={onChange} />);
    const input = await screen.findByRole('combobox', { name: 'Tags' });
    await waitFor(() => expect(mockListTags).toHaveBeenCalledTimes(1));
    await user.click(input);
    expect(
      await screen.findByText('Existing tags could not be loaded — type a name to add one.'),
    ).toBeInTheDocument();
    await user.type(input, 'new-one');
    await user.click(await screen.findByTitle('new-one'));
    expect(onChange).toHaveBeenLastCalledWith(['new-one']);
  });

  it('takes its accessible name from a Form.Item label', async () => {
    mockListTags.mockResolvedValue([]);
    render(
      <Form>
        <Form.Item name="tags" label="Tags (optional)">
          <TagPicker />
        </Form.Item>
      </Form>,
    );
    expect(await screen.findByRole('combobox', { name: 'Tags (optional)' })).toBeInTheDocument();
  });
});

describe('TagFilter', () => {
  it('is labelled, lists the vocabulary and reports the chosen tags', async () => {
    mockListTags.mockResolvedValue([tag('finance'), tag('daily')]);
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<TagFilter value={[]} onChange={onChange} />);
    const control = await screen.findByRole('combobox', { name: 'Tags (all of)' });
    await user.click(control);
    await user.click(await screen.findByTitle('daily'));
    expect(onChange).toHaveBeenCalledWith(['daily'], expect.anything());
  });

  it('says so when the workspace has no tags yet', async () => {
    mockListTags.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<TagFilter value={[]} onChange={vi.fn()} />);
    const control = await screen.findByRole('combobox', { name: 'Tags (all of)' });
    await waitFor(() => expect(mockListTags).toHaveBeenCalledTimes(1));
    await user.click(control);
    expect(await screen.findByText('No tags in this workspace yet')).toBeInTheDocument();
  });
});

describe('AssetTagsBlock', () => {
  it('shows chips to everyone and hides the editor from non-admins', () => {
    render(
      <AntApp>
        <AssetTagsBlock assetId="a1" tags={['gold']} canEdit={false} onChanged={vi.fn()} />
      </AntApp>,
    );
    expect(screen.getByRole('list', { name: 'Asset tags' })).toHaveTextContent('gold');
    expect(screen.queryByRole('button', { name: 'Manage asset tags' })).toBeNull();
  });

  it('renders nothing for an untagged asset a non-admin views', () => {
    const { container } = render(
      <AntApp>
        <AssetTagsBlock assetId="a1" tags={[]} canEdit={false} onChanged={vi.fn()} />
      </AntApp>,
    );
    expect(container.textContent).toBe('');
  });

  it('lets an admin save a new tag set through PATCH /assets/{id}', async () => {
    mockListTags.mockResolvedValue([tag('gold')]);
    mockUpdateAsset.mockResolvedValue({} as never);
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(
      <AntApp>
        <AssetTagsBlock assetId="a1" tags={['gold']} canEdit onChanged={onChanged} />
      </AntApp>,
    );
    await user.click(screen.getByRole('button', { name: 'Manage asset tags' }));
    const picker = await screen.findByRole('combobox', { name: 'Asset tags' });
    await user.type(picker, 'Finance{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(mockUpdateAsset).toHaveBeenCalledWith('a1', { tags: ['gold', 'finance'] }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});
