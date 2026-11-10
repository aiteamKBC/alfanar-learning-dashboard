import assert from 'node:assert/strict'
import { test } from 'node:test'
import { scrollEdges } from '../src/components/learners/tableScroll.ts'

test('a fitting or hidden table has no scroll cues', () => {
  for (const clientWidth of [0, 1540, 2000]) {
    assert.deepEqual(scrollEdges({ scrollLeft: 0, clientWidth, scrollWidth: 1540 }), { left: false, right: false })
  }
})

test('cues track the start, middle and end of the table', () => {
  const geometry = { clientWidth: 800, scrollWidth: 1540 }
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: 0 }), { left: false, right: true })
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: 370 }), { left: true, right: true })
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: 740 }), { left: true, right: false })
})

test('fractional zoom and elastic overscroll do not leave a false end cue', () => {
  const geometry = { clientWidth: 800, scrollWidth: 1540 }
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: 739.5 }), { left: true, right: false })
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: 800 }), { left: true, right: false })
  assert.deepEqual(scrollEdges({ ...geometry, scrollLeft: -20 }), { left: false, right: true })
})

test('resize, zoom and changing table content recompute the available directions', () => {
  assert.deepEqual(scrollEdges({ scrollLeft: 0, clientWidth: 1416, scrollWidth: 1800 }), { left: false, right: true })
  assert.deepEqual(scrollEdges({ scrollLeft: 384, clientWidth: 1900, scrollWidth: 1900 }), { left: false, right: false })
  assert.deepEqual(scrollEdges({ scrollLeft: 0, clientWidth: 358, scrollWidth: 1800 }), { left: false, right: true })
  assert.deepEqual(scrollEdges({ scrollLeft: 384, clientWidth: 1416, scrollWidth: 1800 }), { left: true, right: false })
  assert.deepEqual(scrollEdges({ scrollLeft: 384, clientWidth: 1416, scrollWidth: 2000 }), { left: true, right: true })
})
