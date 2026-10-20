export interface Violation { file: string; line: number; rule: string; text: string }
export function lintCss(css: string, file?: string): Violation[]
export function styleOf(source: string, file: string): string
export function lintProject(): Violation[]
export function lintContrast(sources?: { palettes?: string; semantic?: string; append?: string }): Violation[]
