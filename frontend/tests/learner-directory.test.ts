import assert from 'node:assert/strict'
import { test } from 'node:test'
import { completionLabel, exportPath, downloadModules } from '../src/api/learnerDirectory.ts'
import { attendanceHours, dateTime, pct } from '../src/utils/format.ts'

test('directory percentages keep known zero completions distinct from unknown denominators', () => {
  assert.equal(completionLabel(35, 130, 26.9), '35 / 130 (26.9%)')
  assert.equal(completionLabel(114, 150, 76), '114 / 150 (76%)')
  assert.equal(completionLabel(0, 12, 0), '0 / 12 (0%)')
  assert.equal(completionLabel(0, 0, null), 'N/A')
  assert.equal(completionLabel(0, undefined, undefined), 'N/A')
  assert.equal(attendanceHours(10448), '2h 54m')
  assert.equal(attendanceHours(null), 'N/A')
})

test('export uses explicit selected module IDs, stable order and a single CSV module', () => {
  assert.equal(exportPath([154268, 83334, 83334], 'xlsx'), 'learners/export?courses=83334%2C154268&file_type=xlsx')
  assert.equal(exportPath([135945], 'csv'), 'learners/export?courses=135945&file_type=csv')
  assert.throws(() => exportPath([], 'xlsx'))
  assert.throws(() => exportPath([999], 'xlsx'))
  assert.throws(() => exportPath([83334, 121642], 'csv'))
})

test('downloads use the session and fetch fresh file contents, not displayed paginated rows', async () => {
  const calls: unknown[] = []
  const request = (async (path, init) => {
    calls.push([path, init])
    return new Response('synthetic-file', { status: 200 })
  }) as typeof fetch
  const result = await downloadModules([83334], 'csv', request)
  assert.equal(await result.blob.text(), 'synthetic-file')
  assert.equal(result.filename, 'alfanar-learners.csv')
  assert.deepEqual(calls, [['/api/learners/export?courses=83334&file_type=csv', { credentials: 'same-origin', cache: 'no-store' }]])
})

test('failed or unauthorized downloads never become successful file downloads', async () => {
  for (const status of [401, 403, 500]) {
    await assert.rejects(downloadModules([83334], 'xlsx', (async () => new Response('{}', { status })) as typeof fetch))
  }
})

test('directory dates use UTC calendar boundaries and the exported en-GB display', () => {
  assert.equal(dateTime('2030-01-01T00:15:42+03:00', false), '31 Dec 2029')
  assert.equal(dateTime('2030-09-16T13:30:00Z', false), '16 Sept 2030')
  assert.equal(pct(95), '95%')
  assert.equal(pct(95.2), '95.2%')
  assert.equal(pct(null), '\u2014')
})
