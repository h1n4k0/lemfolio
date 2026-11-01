// Extracts located problems from a latexmk log produced with -file-line-error.
export interface LogProblem { severity: 'error' | 'warning'; file: string | null; line: number | null; message: string }

const fileLine = /^(?:\.\/)?([^\s:][^:\n]*\.(?:tex|ltx|sty|cls|bib|bbl)):(\d+):\s*(.*)$/;
const warning = /^(?:(?:LaTeX|Package|Class)(?: [\w.-]+)? Warning|Overfull|Underfull)\b.*$/;

export function parseLatexLog(log: string | null | undefined): LogProblem[] {
  if (!log) return [];
  const problems: LogProblem[] = [], seen = new Set<string>();
  const add = (problem: LogProblem) => {
    const key = `${problem.severity}|${problem.file}|${problem.line}|${problem.message}`;
    if (!seen.has(key) && problems.length < 200) { seen.add(key); problems.push(problem); }
  };
  // latexmk concatenates every pass; only the last LaTeX pass describes the final document.
  const passes = [...log.matchAll(/^Run number \d+ of rule '(?:pdf|xe|lua)?latex'/gm)];
  const lines = log.slice(passes.at(-1)?.index ?? 0).split(/\r?\n/);
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const located = fileLine.exec(line);
    if (located) { add({severity: 'error', file: located[1], line: Number(located[2]), message: located[3]}); continue; }
    if (line.startsWith('! ')) {
      const context = lines.slice(index + 1, index + 12).find(item => /^l\.\d+/.test(item));
      add({severity: 'error', file: null, line: context ? Number(/^l\.(\d+)/.exec(context)![1]) : null, message: line.slice(2)});
      continue;
    }
    if (warning.test(line)) {
      // TeX wraps log lines at 79 columns, so a warning may continue on the next line.
      const message = line.length >= 79 && lines[index + 1] ? line + lines[index + 1].trimStart() : line;
      const at = /(?:on input line|at lines?) (\d+)/.exec(message);
      add({severity: 'warning', file: null, line: at ? Number(at[1]) : null, message: message.replace(/\s+/g, ' ').trim()});
    }
  }
  return problems.sort((a, b) => Number(a.severity === 'warning') - Number(b.severity === 'warning'));
}
