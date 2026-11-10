/** A one-pixel tolerance avoids a lingering cue at fractional zoom/scroll positions. */
export function scrollEdges({ scrollLeft, clientWidth, scrollWidth }: {
  scrollLeft: number; clientWidth: number; scrollWidth: number
}) {
  const end = Math.max(0, scrollWidth - clientWidth)
  const position = Math.max(0, Math.min(scrollLeft, end))
  return {
    left: clientWidth > 0 && position > 1,
    right: clientWidth > 0 && end - position > 1,
  }
}
