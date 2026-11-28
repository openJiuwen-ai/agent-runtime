import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import zh from './locales/zh.json';
import en from './locales/en.json';
import { getProductName } from '../utils/env';

const productName = getProductName();

/** Map i18n language code to document.documentElement.lang for native controls. */
export function syncDocumentLang(lng?: string): void {
  const code = (lng ?? i18n.language ?? 'zh').toLowerCase();
  document.documentElement.lang = code.startsWith('zh') ? 'zh-CN' : 'en';
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    fallbackLng: 'zh',
    supportedLngs: ['zh', 'en'],
    detection: {
      order: ['localStorage'],
      caches: ['localStorage'],
    },
    interpolation: { escapeValue: false, defaultVariables: { productName } },
  })
  .then(() => {
    syncDocumentLang(i18n.language);
  });

i18n.on('languageChanged', (lng) => {
  syncDocumentLang(lng);
});

export default i18n;
