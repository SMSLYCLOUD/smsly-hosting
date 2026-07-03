/**
 * Tests for the small helper functions exported from src/lib/api.ts.
 *
 * We deliberately do NOT test the axios instance or its interceptors —
 * those would re-test the axios wrapper. The helpers below contain
 * non-trivial logic worth pinning down.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { downloadBlob, extractDataList } from '@/lib/api';

describe('downloadBlob', () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let removeChildSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURL = vi.fn(() => 'blob:mock-url');
    revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    appendChildSpy = vi.spyOn(document.body, 'appendChild');
    removeChildSpy = vi.spyOn(document.body, 'removeChild');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    appendChildSpy.mockRestore();
    removeChildSpy.mockRestore();
  });

  it('creates a hidden anchor with the right href and download attribute, clicks it, then removes it', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click');

    const payload = new Blob(['hello,world\n']);
    downloadBlob(payload, 'reports/path/to/file.csv');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const createdBlob = createObjectURL.mock.calls[0]?.[0] as Blob;
    expect(createdBlob).toBeInstanceOf(Blob);

    const appended = appendChildSpy.mock.calls
      .map((call) => call[0])
      .find((el): el is HTMLAnchorElement => el instanceof HTMLAnchorElement);
    expect(appended).toBeDefined();
    expect(appended!.href).toBe('blob:mock-url');
    expect(appended!.getAttribute('download')).toBe('file.csv');

    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
    expect(removeChildSpy).toHaveBeenCalled();

    clickSpy.mockRestore();
  });
});

describe('extractDataList', () => {
  it('returns the array when response.data is already an array', () => {
    const arr = [{ id: 1 }, { id: 2 }];
    expect(extractDataList({ data: arr })).toBe(arr);
  });

  it('returns response.data.results when it is an array', () => {
    const arr = [{ id: 1 }];
    expect(extractDataList({ data: { results: arr } })).toBe(arr);
  });

  it('returns [] when response.data is { results: [] }', () => {
    expect(extractDataList({ data: { results: [] } })).toEqual([]);
  });

  it('returns [] when response.data is null', () => {
    expect(extractDataList({ data: null })).toEqual([]);
  });

  it('returns [] when response.data is a non-object primitive', () => {
    expect(extractDataList({ data: 42 })).toEqual([]);
    expect(extractDataList({ data: 'string' })).toEqual([]);
  });

  it('returns [] when response.data is an object without results', () => {
    expect(extractDataList({ data: { count: 1 } })).toEqual([]);
  });
});
