export type Locale = 'en' | 'ru'

export type TranslationTree = {
  [key: string]: string | string[] | TranslationTree
}
