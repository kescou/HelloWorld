import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, buildUrl, request } from './client'

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

describe('buildUrl', () => {
  it('prefixes /api and stays relative', () => {
    expect(buildUrl('/accounts')).toBe('/api/accounts')
    expect(buildUrl('accounts')).toBe('/api/accounts')
  })
  it('drops empty params and encodes values', () => {
    expect(buildUrl('/transactions', { q: 'REWE & Co', account_id: 3, type: undefined, category_id: null, date_from: '', limit: 0 })).toBe('/api/transactions?q=REWE%20%26%20Co&account_id=3&limit=0')
    expect(buildUrl('/portfolio/allocation', { by: 'sector', lookthrough: false })).toBe('/api/portfolio/allocation?by=sector&lookthrough=false')
  })
})

describe('request', () => {
  it('returns parsed JSON and sends JSON bodies', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ id: 7 }))
    vi.stubGlobal('fetch', fetchMock)
    const res = await api.transfer({ from_account_id: 1, to_account_id: 2, amount: 50, date: '2026-11-12' })
    expect(res).toEqual({ id: 7 })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/transactions/transfer')
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toMatchObject({ amount: 50 })
  })
  it('handles 204 without a body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(api.deleteTransaction(1)).resolves.toBeUndefined()
  })
  it('maps FastAPI error details to ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'Konto hat Transaktionen' }, 409)))
    await expect(api.deleteAccount(1)).rejects.toMatchObject({ name: 'ApiError', status: 409, message: 'Konto hat Transaktionen' })
  })
  it('uses the message of object details (409 duplicate)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: { message: 'Diese Buchung existiert bereits.', existing_id: 12 } }, 409)))
    const err = await api.createTransaction({ account_id: 1, type: 'deposit', booking_date: '2026-11-12', amount: 5 }).catch((e: unknown) => e)
    expect(err).toMatchObject({ status: 409, message: 'Diese Buchung existiert bereits.', detail: { existing_id: 12 } })
  })
  it('passes include_flagged / force as query parameters', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json({ accepted: 0, skipped: 0, duplicates: 0, errors: [] }))
    vi.stubGlobal('fetch', fetchMock)
    await api.acceptAll(3, true)
    await api.acceptAll(3)
    await api.acceptDraft(9, true)
    await api.refreshBackground('all')
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(['/api/documents/3/accept-all?include_flagged=true', '/api/documents/3/accept-all', '/api/documents/drafts/9/accept?force=true', '/api/market/refresh?wait=false'])
  })
  it('flattens validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: [{ loc: ['body', 'amount'], msg: 'Field required', type: 'missing' }] }, 422)))
    const err: unknown = await request('POST', '/transactions', { body: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe('amount: Field required')
  })
  it('reports an unreachable server in plain German', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.accounts()).rejects.toMatchObject({ status: 0, message: expect.stringContaining('nicht erreichbar') })
  })
  it('uploads files as multipart without a JSON content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json([]))
    vi.stubGlobal('fetch', fetchMock)
    await api.uploadDocuments([new File(['x'], 'a.pdf', { type: 'application/pdf' })], 3)
    const init = fetchMock.mock.calls[0][1]
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).getAll('files')).toHaveLength(1)
    expect((init.body as FormData).get('account_id')).toBe('3')
    expect(init.headers['Content-Type']).toBeUndefined()
  })
})
