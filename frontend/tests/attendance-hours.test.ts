import assert from 'node:assert/strict'
import { test } from 'node:test'
import { attendanceHours } from '../src/utils/format.ts'

test('verified module totals use hours and minutes without rounding individual sessions', () => {
  assert.equal(attendanceHours(2 * 3600 + 54 * 60 + 8), '2h 54m')
  assert.equal(attendanceHours(59 + 59), '0h 1m')
  assert.equal(attendanceHours(30 * 3600 + 59 * 60 + 59), '30h 59m')
})

test('missing timing remains N/A and genuine short durations remain distinct', () => {
  assert.equal(attendanceHours(null), 'N/A')
  assert.equal(attendanceHours(undefined), 'N/A')
  assert.equal(attendanceHours(0), '0h 0m')
  assert.equal(attendanceHours(41), '<1m')
})
