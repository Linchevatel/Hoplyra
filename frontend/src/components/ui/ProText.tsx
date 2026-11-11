import { ProMark } from './ProMark'

/** Вставляет выделенный ProMark вместо слова «Pro» в тексте */
export function ProText({ children }: { children: string }) {
  const parts = children.split(/(\bPro\b)/g)
  return (
    <>
      {parts.map((part, i) =>
        part === 'Pro' ? <ProMark key={i} size="sm" className="mx-0.5 -translate-y-px" /> : part,
      )}
    </>
  )
}
