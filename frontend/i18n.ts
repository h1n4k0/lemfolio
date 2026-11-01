import { english } from './locales/en.js';

/** Chinese source text is the message key, so an untranslated string still reads correctly. */
export type Locale = 'zh' | 'en';

function detect(): Locale {
  if (typeof document === 'undefined') return 'zh';
  try { const stored = localStorage.getItem('lemfolio.locale'); if (stored === 'zh' || stored === 'en') return stored; } catch { /* follows the browser */ }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}
// Fixed for the page's lifetime: labels in module constants are read once, and switching reloads.
export const locale = detect();

/** Text that arrives at run time, such as the reasons a workflow projection reports. */
export const translated = (text: string): string => locale === 'en' ? english[text] ?? text : text;
export const t = (text: string, ...values: unknown[]): string => translated(text).replace(/\{(\d+)\}/g, (_, index: string) => String(values[Number(index)]));

export function switchLocale() {
  try { localStorage.setItem('lemfolio.locale', locale === 'zh' ? 'en' : 'zh'); } catch { return; }
  location.reload();
}
