import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { RequireUuidParams } from '../../src/components/RequireUuidParams';
import { isUuid, uuidOrNull } from '../../src/utils/uuid';

const GOOD = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/results/:runId/compare/:otherRunId"
          element={
            <RequireUuidParams>
              <div>page body</div>
            </RequireUuidParams>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RequireUuidParams', () => {
  it('renders the page when every id is a UUID', () => {
    at(`/results/${GOOD}/compare/${GOOD}`);
    expect(screen.getByText('page body')).toBeInTheDocument();
  });

  it('renders the 404 state when any id is malformed', () => {
    at(`/results/${GOOD}/compare/not-a-uuid`);
    expect(screen.queryByText('page body')).not.toBeInTheDocument();
    expect(screen.getByText('404 — Not found')).toBeInTheDocument();
  });
});

describe('uuid helpers', () => {
  it('accepts UUIDs and nulls anything else', () => {
    expect(isUuid(GOOD)).toBe(true);
    expect(uuidOrNull(GOOD.toUpperCase())).toBe(GOOD.toUpperCase());
    for (const bad of ['', 'x', `${GOOD}/x`, `${GOOD}\n`, null, undefined]) {
      expect(uuidOrNull(bad)).toBeNull();
    }
  });
});
