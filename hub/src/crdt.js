import * as Y from 'yjs';

export const encode = bytes => btoa(Array.from(bytes, value => String.fromCharCode(value)).join(''));
export const decode = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
export const snapshot = doc => encode(Y.encodeStateAsUpdate(doc));
export function document(state) {
  const doc = new Y.Doc();
  if (state) Y.applyUpdate(doc, decode(state));
  return doc;
}
export function replaceText(doc, next) {
  const text = doc.getText('body');
  const old = text.toString();
  let start = 0, end = 0;
  while (start < old.length && start < next.length && old[start] === next[start]) start++;
  while (end < old.length-start && end < next.length-start && old.at(-end-1) === next.at(-end-1)) end++;
  doc.transact(() => {
    if (old.length-start-end) text.delete(start, old.length-start-end);
    if (next.length-start-end) text.insert(start, next.slice(start,next.length-end));
  });
}
export { Y };
