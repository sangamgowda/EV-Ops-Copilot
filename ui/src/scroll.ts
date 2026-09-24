// Scroll anchoring. Follow new tokens only while the reader is already
// at the bottom; once they scroll up to read, leave the view alone.

/** How close to the bottom (px) still counts as "at the bottom". */
export const STICK_THRESHOLD = 80;

export function isNearBottom(el: { scrollTop: number; scrollHeight: number; clientHeight: number }): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD;
}
