export function evidenceText(raw = '', limit = 0): string {
  if (!raw || raw.startsWith('%PDF') || raw.startsWith('PK') || raw.startsWith('\xd0\xcf')) return ''

  let text = raw
  if (/<\/?[a-z][^>]*>/i.test(raw)) {
    const document = new DOMParser().parseFromString(raw, 'text/html')
    const table = document.querySelector('table')
    text = table
      ? Array.from(table.rows)
          .map((row) => Array.from(row.cells).map((cell) => cell.textContent?.trim()).filter(Boolean).join(' ｜ '))
          .filter(Boolean)
          .join('\n')
      : document.body.textContent ?? ''
  }

  text = text.replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim()
  return limit > 0 && text.length > limit ? `${text.slice(0, limit).trim()}…` : text
}
