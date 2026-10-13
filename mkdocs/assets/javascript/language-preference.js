/**
 * Viewer language preference: store a locale and translate the current
 * English page in the browser. No locale URLs.
 */
"use strict";

const LupaxaLanguagePreference = (() => {
  const STORAGE_KEY = "lupaxa-lang";
  const FALLBACK_DISMISS_KEY = "lupaxa-lang-fallback-dismissed";
  const CACHE_KEY = "lupaxa-lang-strings";
  const CACHE_MAX_PER_LOCALE = 500;
  const LOCALES = [
    "en",
    "fr",
    "de",
    "es",
    "pt",
    "it",
    "nl",
    "pl",
    "bg",
    "cs",
    "da",
    "el",
    "fi",
    "hr",
    "hu",
    "lt",
    "no",
    "ro",
    "sv",
    "tr",
    "uk",
  ];
  const SKIP_TAGS = new Set(["PRE", "CODE", "SCRIPT", "STYLE", "NOSCRIPT"]);
  const SKIP_CLASSES = new Set(["md-footer", "md-copyright", "notranslate"]);

  const safeStorage = (backing) => {
    if (!backing) {
      return memoryBacking();
    }
    try {
      const probe = "__lupaxa_lang_probe__";
      backing.setItem(probe, "1");
      backing.removeItem(probe);
      return backing;
    } catch (_error) {
      return memoryBacking();
    }
  };

  const memoryBacking = () => {
    const map = new Map();
    return {
      getItem(key) {
        return map.has(key) ? map.get(key) : null;
      },
      setItem(key, value) {
        map.set(key, String(value));
      },
      removeItem(key) {
        map.delete(key);
      },
    };
  };

  const normaliseLocale = (value) =>
    LOCALES.includes(value) ? value : "en";

  const readPreference = (storage) => {
    let raw = null;
    try {
      raw = storage.getItem(STORAGE_KEY);
    } catch (_error) {
      return "en";
    }
    if (raw === null || raw === undefined || raw === "") {
      return "en";
    }
    const locale = normaliseLocale(raw);
    if (locale !== raw) {
      try {
        storage.setItem(STORAGE_KEY, locale);
      } catch (_error) {
        /* ignore */
      }
    }
    return locale;
  };

  const readStringCache = (storage) => {
    if (!storage || typeof storage.getItem !== "function") {
      return {};
    }
    try {
      const raw = storage.getItem(CACHE_KEY);
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  };

  const writeStringCache = (storage, data) => {
    if (!storage || typeof storage.setItem !== "function") {
      return;
    }
    try {
      storage.setItem(CACHE_KEY, JSON.stringify(data));
    } catch (_error) {
      /* quota */
    }
  };

  const pruneLocaleBucket = (bucket) => {
    const keys = Object.keys(bucket);
    if (keys.length <= CACHE_MAX_PER_LOCALE) {
      return bucket;
    }
    const next = {};
    const keep = keys.slice(keys.length - CACHE_MAX_PER_LOCALE);
    for (const key of keep) {
      next[key] = bucket[key];
    }
    return next;
  };

  const rememberTranslations = (storage, locale, pairs) => {
    if (!storage || !locale || !pairs || !pairs.length) {
      return;
    }
    const data = readStringCache(storage);
    const bucket = { ...(data[locale] || {}) };
    for (const [source, translated] of pairs) {
      if (source && translated) {
        bucket[source] = translated;
      }
    }
    data[locale] = pruneLocaleBucket(bucket);
    writeStringCache(storage, data);
  };

  const writePreference = (storage, value) => {
    const locale = normaliseLocale(value);
    try {
      storage.setItem(STORAGE_KEY, locale);
    } catch (_error) {
      /* ignore */
    }
    return locale;
  };

  const isUrlLikeText = (value) => {
    const text = String(value || "").trim();
    return /^(https?:\/\/|www\.)/i.test(text);
  };

  const isSkippableElement = (el) => {
    if (!el || el.nodeType !== 1) {
      return false;
    }
    if (SKIP_TAGS.has(el.tagName)) {
      return true;
    }
    if (el.getAttribute && el.getAttribute("translate") === "no") {
      return true;
    }
    if (!el.classList || typeof el.classList.contains !== "function") {
      return false;
    }
    for (const name of SKIP_CLASSES) {
      if (el.classList.contains(name)) {
        return true;
      }
    }
    return false;
  };

  const shouldSkipNode = (node) => {
    if (!node) {
      return true;
    }
    if (node.nodeType === 3 && isUrlLikeText(node.nodeValue)) {
      return true;
    }
    let el = node.nodeType === 3 ? node.parentElement : node;
    while (el) {
      if (isSkippableElement(el)) {
        return true;
      }
      el = el.parentElement;
    }
    return false;
  };

  const collectTextNodes = (root) => {
    const found = [];
    const visit = (node) => {
      if (!node) {
        return;
      }
      if (node.nodeType === 3) {
        if (String(node.nodeValue || "").trim() && !shouldSkipNode(node)) {
          found.push(node);
        }
        return;
      }
      if (node.nodeType === 1 && isSkippableElement(node)) {
        return;
      }
      const kids = node.childNodes || [];
      for (let i = 0; i < kids.length; i += 1) {
        visit(kids[i]);
      }
    };
    visit(root);
    return found;
  };

  const translatorAvailable = (api) =>
    Boolean(api && typeof api.create === "function");

  const UI_STRINGS = {
    en: {
      fallback: "This page is in English. Your browser can translate it.",
      preparing: "Preparing translation…",
      translating: "Translating…",
      downloading:
        "Downloading the language pack. This can take a moment the first time.",
      needsActivation:
        "Select the language again to finish setting up translation.",
      dismiss: "Dismiss",
    },
    fr: {
      fallback:
        "Cette page est en anglais. Votre navigateur peut la traduire.",
      preparing: "Préparation de la traduction…",
      translating: "Traduction…",
      downloading:
        "Téléchargement du pack de langue. Cela peut prendre un moment la première fois.",
      needsActivation:
        "Sélectionnez à nouveau la langue pour terminer la configuration de la traduction.",
      dismiss: "Fermer",
    },
    de: {
      fallback:
        "Diese Seite ist auf Englisch. Ihr Browser kann sie übersetzen.",
      preparing: "Übersetzung wird vorbereitet…",
      translating: "Übersetzung…",
      downloading:
        "Das Sprachpaket wird heruntergeladen. Das kann beim ersten Mal einen Moment dauern.",
      needsActivation:
        "Wählen Sie die Sprache erneut aus, um die Übersetzung einzurichten.",
      dismiss: "Schließen",
    },
    es: {
      fallback:
        "Esta página está en inglés. Su navegador puede traducirla.",
      preparing: "Preparando la traducción…",
      translating: "Traduciendo…",
      downloading:
        "Descargando el paquete de idioma. La primera vez puede tardar un momento.",
      needsActivation:
        "Seleccione el idioma de nuevo para terminar de configurar la traducción.",
      dismiss: "Cerrar",
    },
    pt: {
      fallback:
        "Esta página está em inglês. O seu navegador pode traduzi-la.",
      preparing: "A preparar a tradução…",
      translating: "A traduzir…",
      downloading:
        "A transferir o pacote de idioma. Pode demorar um pouco da primeira vez.",
      needsActivation:
        "Selecione o idioma novamente para concluir a configuração da tradução.",
      dismiss: "Fechar",
    },
    it: {
      fallback: "Questa pagina è in inglese. Il browser può tradurla.",
      preparing: "Preparazione della traduzione…",
      translating: "Traduzione…",
      downloading:
        "Download del pacchetto lingua. La prima volta può richiedere qualche momento.",
      needsActivation:
        "Seleziona di nuovo la lingua per completare la configurazione della traduzione.",
      dismiss: "Chiudi",
    },
    nl: {
      fallback:
        "Deze pagina is in het Engels. Uw browser kan die vertalen.",
      preparing: "Vertaling voorbereiden…",
      translating: "Vertalen…",
      downloading:
        "Het taalpakket wordt gedownload. Dit kan de eerste keer even duren.",
      needsActivation:
        "Selecteer de taal opnieuw om de vertaling te voltooien.",
      dismiss: "Sluiten",
    },
    pl: {
      fallback:
        "Ta strona jest po angielsku. Twoja przeglądarka może ją przetłumaczyć.",
      preparing: "Przygotowywanie tłumaczenia…",
      translating: "Tłumaczenie…",
      downloading:
        "Pobieranie pakietu językowego. Za pierwszym razem może to chwilę potrwać.",
      needsActivation:
        "Wybierz język ponownie, aby dokończyć konfigurację tłumaczenia.",
      dismiss: "Zamknij",
    },
    bg: {
      fallback:
        "Тази страница е на английски. Браузърът ви може да я преведе.",
      preparing: "Подготовка на превода…",
      translating: "Превеждане…",
      downloading:
        "Изтегляне на езиковия пакет. Първия път може да отнеме малко време.",
      needsActivation:
        "Изберете отново езика, за да завършите настройката на превода.",
      dismiss: "Затвори",
    },
    cs: {
      fallback: "Tato stránka je v angličtině. Prohlížeč ji může přeložit.",
      preparing: "Příprava překladu…",
      translating: "Překládání…",
      downloading:
        "Stahování jazykového balíčku. Poprvé to může chvíli trvat.",
      needsActivation:
        "Vyberte jazyk znovu, abyste dokončili nastavení překladu.",
      dismiss: "Zavřít",
    },
    da: {
      fallback:
        "Denne side er på engelsk. Din browser kan oversætte den.",
      preparing: "Forbereder oversættelse…",
      translating: "Oversætter…",
      downloading:
        "Sprogpakken downloades. Første gang kan det tage et øjeblik.",
      needsActivation:
        "Vælg sproget igen for at færdiggøre opsætningen af oversættelsen.",
      dismiss: "Luk",
    },
    el: {
      fallback:
        "Αυτή η σελίδα είναι στα αγγλικά. Το πρόγραμμα περιήγησης μπορεί να τη μεταφράσει.",
      preparing: "Προετοιμασία μετάφρασης…",
      translating: "Μετάφραση…",
      downloading:
        "Λήψη του πακέτου γλώσσας. Την πρώτη φορά μπορεί να πάρει λίγο χρόνο.",
      needsActivation:
        "Επιλέξτε ξανά τη γλώσσα για να ολοκληρώσετε τη ρύθμιση της μετάφρασης.",
      dismiss: "Κλείσιμο",
    },
    fi: {
      fallback: "Tämä sivu on englanniksi. Selaimesi voi kääntää sen.",
      preparing: "Valmistellaan käännöstä…",
      translating: "Käännetään…",
      downloading:
        "Kielipakettia ladataan. Ensimmäisellä kerralla tämä voi kestää hetken.",
      needsActivation:
        "Valitse kieli uudelleen käännöksen määrityksen viimeistelemiseksi.",
      dismiss: "Sulje",
    },
    hr: {
      fallback:
        "Ova je stranica na engleskom. Vaš preglednik može je prevesti.",
      preparing: "Priprema prijevoda…",
      translating: "Prevođenje…",
      downloading:
        "Preuzimanje jezičnog paketa. Prvi put to može potrajati.",
      needsActivation:
        "Ponovno odaberite jezik kako biste dovršili postavljanje prijevoda.",
      dismiss: "Zatvori",
    },
    hu: {
      fallback: "Ez az oldal angol nyelvű. A böngésző le tudja fordítani.",
      preparing: "Fordítás előkészítése…",
      translating: "Fordítás…",
      downloading:
        "A nyelvi csomag letöltése. Először eltarthat egy ideig.",
      needsActivation:
        "Válassza ki újra a nyelvet a fordítás beállításának befejezéséhez.",
      dismiss: "Bezárás",
    },
    lt: {
      fallback: "Šis puslapis yra anglų kalba. Naršyklė gali jį išversti.",
      preparing: "Ruošiamas vertimas…",
      translating: "Verčiama…",
      downloading:
        "Atsisiunčiamas kalbos paketas. Pirmą kartą tai gali šiek tiek užtrukti.",
      needsActivation:
        "Pasirinkite kalbą dar kartą, kad baigtumėte vertimo sąranką.",
      dismiss: "Uždaryti",
    },
    no: {
      fallback: "Denne siden er på engelsk. Nettleseren kan oversette den.",
      preparing: "Forbereder oversettelse…",
      translating: "Oversetter…",
      downloading:
        "Språkpakken lastes ned. Første gang kan det ta et øyeblikk.",
      needsActivation:
        "Velg språket på nytt for å fullføre oppsettet av oversettelsen.",
      dismiss: "Lukk",
    },
    ro: {
      fallback: "Această pagină este în engleză. Browserul o poate traduce.",
      preparing: "Se pregătește traducerea…",
      translating: "Se traduce…",
      downloading:
        "Se descarcă pachetul lingvistic. Prima dată poate dura un moment.",
      needsActivation:
        "Selectați din nou limba pentru a finaliza configurarea traducerii.",
      dismiss: "Închide",
    },
    sv: {
      fallback:
        "Den här sidan är på engelska. Din webbläsare kan översätta den.",
      preparing: "Förbereder översättning…",
      translating: "Översätter…",
      downloading:
        "Språkpaketet hämtas. Första gången kan det ta en stund.",
      needsActivation:
        "Välj språket igen för att slutföra inställningen av översättningen.",
      dismiss: "Stäng",
    },
    tr: {
      fallback: "Bu sayfa İngilizcedir. Tarayıcınız onu çevirebilir.",
      preparing: "Çeviri hazırlanıyor…",
      translating: "Çeviriliyor…",
      downloading: "Dil paketi indiriliyor. İlk seferde biraz sürebilir.",
      needsActivation:
        "Çeviri kurulumunu bitirmek için dili yeniden seçin.",
      dismiss: "Kapat",
    },
    uk: {
      fallback: "Ця сторінка англійською. Ваш браузер може її перекласти.",
      preparing: "Підготовка перекладу…",
      translating: "Переклад…",
      downloading:
        "Завантаження мовного пакета. Уперше це може зайняти хвилину.",
      needsActivation:
        "Виберіть мову ще раз, щоб завершити налаштування перекладу.",
      dismiss: "Закрити",
    },
  };

  const uiString = (locale, key) => {
    const pack = UI_STRINGS[normaliseLocale(locale)] || UI_STRINGS.en;
    return pack[key] || UI_STRINGS.en[key] || "";
  };
  const NETWORK_ENDPOINT = "https://clients5.google.com/translate_a/t";
  const NETWORK_URL_MAX = 1600;

  const parseNetworkTranslations = (payload) => {
    let data = payload;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch (_error) {
        return [];
      }
    }
    if (!Array.isArray(data)) {
      return [];
    }
    return data.map((item) => (typeof item === "string" ? item : ""));
  };

  const buildNetworkTranslateUrl = (locale, texts) => {
    const params = new URLSearchParams();
    params.set("client", "dict-chrome-ex");
    params.set("sl", "en");
    params.set("tl", locale);
    for (const text of texts || []) {
      params.append("q", text);
    }
    return `${NETWORK_ENDPOINT}?${params.toString()}`;
  };

  const chunkNetworkTexts = (texts, maxLen = NETWORK_URL_MAX) => {
    const chunks = [];
    let current = [];
    let len = 80;
    for (const text of texts || []) {
      const extra = encodeURIComponent(text).length + 3;
      if (current.length && len + extra > maxLen) {
        chunks.push(current);
        current = [];
        len = 80;
      }
      current.push(text);
      len += extra;
    }
    if (current.length) {
      chunks.push(current);
    }
    return chunks;
  };

  const createNetworkTranslatorApi = ({ fetchImpl } = {}) => {
    if (typeof fetchImpl !== "function") {
      return null;
    }
    return {
      kind: "network",
      async availability() {
        return "available";
      },
      async create({ targetLanguage }) {
        const locale = normaliseLocale(targetLanguage);
        const translateMany = async (texts) => {
          const unique = [];
          const seen = new Set();
          for (const text of texts || []) {
            if (text && !seen.has(text)) {
              seen.add(text);
              unique.push(text);
            }
          }
          const out = {};
          for (const chunk of chunkNetworkTexts(unique)) {
            const url = buildNetworkTranslateUrl(locale, chunk);
            const res = await fetchImpl(url);
            if (!res || !res.ok) {
              throw new Error("translate failed");
            }
            const payload =
              typeof res.json === "function" ? await res.json() : [];
            const parts = parseNetworkTranslations(payload);
            for (let i = 0; i < chunk.length; i += 1) {
              if (parts[i]) {
                out[chunk[i]] = parts[i];
              }
            }
          }
          return out;
        };
        return {
          async translate(text) {
            const map = await translateMany([text]);
            return map[text] || "";
          },
          translateMany,
          destroy() {},
        };
      },
    };
  };

  const defaultFetchImpl = () => {
    if (typeof fetch === "function") {
      return fetch.bind(typeof self !== "undefined" ? self : globalThis);
    }
    return null;
  };

  const browserTranslatorApi = (fetchImpl) => {
    if (typeof self !== "undefined" && self.Translator) {
      return self.Translator;
    }
    const fetchFn = fetchImpl || defaultFetchImpl();
    if (!fetchFn) {
      return null;
    }
    return createNetworkTranslatorApi({ fetchImpl: fetchFn });
  };

  const isActiveDownloadProgress = (event) => {
    const loaded = event && typeof event.loaded === "number" ? event.loaded : NaN;
    return loaded >= 0 && loaded < 1;
  };

  const availabilityOf = async (api, options) => {
    if (!translatorAvailable(api)) {
      return "unavailable";
    }
    if (typeof api.availability !== "function") {
      return "available";
    }
    try {
      return await api.availability(options);
    } catch (_error) {
      return "unavailable";
    }
  };

  const shouldShowFallback = (locale, available, dismissed) =>
    locale !== "en" && !available && !dismissed;

  const applyDocumentLang = (doc, locale) => {
    if (doc && doc.documentElement) {
      doc.documentElement.lang = locale;
    }
  };

  let translatedNodes = new WeakSet();
  let activeLocale = "en";
  let translatorCache = { locale: null, translator: null, api: null };

  const resetTranslatedNodes = () => {
    translatedNodes = new WeakSet();
  };

  const rememberLocale = (locale) => {
    if (activeLocale !== locale) {
      resetTranslatedNodes();
      activeLocale = locale;
    }
  };

  const destroyTranslator = (translator) => {
    if (translator && typeof translator.destroy === "function") {
      try {
        translator.destroy();
      } catch (_error) {
        /* ignore */
      }
    }
  };

  const clearTranslatorCache = () => {
    destroyTranslator(translatorCache.translator);
    translatorCache = { locale: null, translator: null, api: null };
  };

  const splitEdgeWhitespace = (value) => {
    const text = String(value ?? "");
    const trimmed = text.trim();
    if (!trimmed) {
      return { lead: "", trimmed: "", trail: "" };
    }
    const start = text.indexOf(trimmed);
    return {
      lead: text.slice(0, start),
      trimmed,
      trail: text.slice(start + trimmed.length),
    };
  };

  const translateTextNodes = async (nodes, translator, isCurrent, cache) => {
    const locale = cache && cache.locale;
    const storage = cache && cache.storage;
    const cached =
      locale && storage ? readStringCache(storage)[locale] || {} : {};
    if (translator && typeof translator.translateMany === "function") {
      const unique = [];
      const seen = new Set();
      for (const node of nodes) {
        if (translatedNodes.has(node)) {
          continue;
        }
        const trimmed = splitEdgeWhitespace(node.nodeValue).trimmed;
        if (trimmed && !cached[trimmed] && !seen.has(trimmed)) {
          seen.add(trimmed);
          unique.push(trimmed);
        }
      }
      if (unique.length) {
        const mapped = await translator.translateMany(unique);
        rememberTranslations(storage, locale, Object.entries(mapped || {}));
        Object.assign(cached, mapped || {});
      }
    }
    const pending = [];
    for (const node of nodes) {
      if (!isCurrent()) {
        return;
      }
      if (translatedNodes.has(node)) {
        continue;
      }
      const original = node.nodeValue;
      const { lead, trimmed, trail } = splitEdgeWhitespace(original);
      if (!trimmed) {
        continue;
      }
      if (cached[trimmed]) {
        node.nodeValue = lead + cached[trimmed] + trail;
        translatedNodes.add(node);
        continue;
      }
      if (!translator) {
        continue;
      }
      try {
        const next = await translator.translate(trimmed);
        if (!isCurrent()) {
          return;
        }
        if (typeof next === "string" && next.length > 0) {
          node.nodeValue = lead + next + trail;
          translatedNodes.add(node);
          cached[trimmed] = next;
          pending.push([trimmed, next]);
        }
      } catch (_error) {
        node.nodeValue = original;
      }
    }
    rememberTranslations(storage, locale, pending);
  };

  const runTranslation = async ({
    locale,
    api,
    roots,
    document: doc,
    generation,
    currentGeneration,
    userActivation = false,
    onProgress,
    onNetwork,
    cacheStorage,
    networkApi,
  }) => {
    rememberLocale(locale);
    if (locale === "en") {
      clearTranslatorCache();
      applyDocumentLang(doc, "en");
      return { status: "skipped", generation };
    }
    const isCurrent = () => currentGeneration() === generation;
    const cache = { locale, storage: cacheStorage };
    const nodes = [];
    for (const root of roots || []) {
      nodes.push(...collectTextNodes(root));
    }
    await translateTextNodes(nodes, null, isCurrent, cache);
    const remaining = nodes.filter((node) => !translatedNodes.has(node));
    if (!remaining.length) {
      applyDocumentLang(doc, locale);
      return { status: "ok", generation };
    }
    const pair = {
      sourceLanguage: "en",
      targetLanguage: locale,
    };
    let effectiveApi = translatorAvailable(api) ? api : networkApi;
    if (!translatorAvailable(effectiveApi)) {
      return { status: "fallback", generation };
    }
    let availability = await availabilityOf(effectiveApi, pair);
    if (!isCurrent()) {
      return { status: "skipped", generation };
    }
    if (
      availability === "unavailable" &&
      effectiveApi !== networkApi &&
      translatorAvailable(networkApi)
    ) {
      if (typeof onNetwork === "function") {
        onNetwork();
      }
      effectiveApi = networkApi;
      availability = await availabilityOf(effectiveApi, pair);
      if (!isCurrent()) {
        return { status: "skipped", generation };
      }
    }
    if (availability === "unavailable") {
      return { status: "fallback", generation };
    }
    if (
      (availability === "downloadable" || availability === "downloading") &&
      !userActivation
    ) {
      return { status: "needs-activation", generation };
    }
    let translator = null;
    if (
      translatorCache.locale === locale &&
      translatorCache.api === effectiveApi &&
      translatorCache.translator
    ) {
      translator = translatorCache.translator;
    } else {
      try {
        translator = await effectiveApi.create({
          ...pair,
          monitor(m) {
            if (!m || typeof m.addEventListener !== "function") {
              return;
            }
            m.addEventListener("downloadprogress", (event) => {
              if (
                availability !== "downloadable" &&
                availability !== "downloading"
              ) {
                return;
              }
              if (
                !isActiveDownloadProgress(event) ||
                typeof onProgress !== "function"
              ) {
                return;
              }
              onProgress(event);
            });
          },
        });
      } catch (_error) {
        return { status: "failed", generation };
      }
      if (!isCurrent()) {
        destroyTranslator(translator);
        return { status: "skipped", generation };
      }
      clearTranslatorCache();
      translatorCache = { locale, translator, api: effectiveApi };
    }
    if (!isCurrent()) {
      return { status: "skipped", generation };
    }
    try {
      await translateTextNodes(remaining, translator, isCurrent, cache);
    } catch (_error) {
      return { status: "failed", generation };
    }
    if (!isCurrent()) {
      return { status: "skipped", generation };
    }
    applyDocumentLang(doc, locale);
    return { status: "ok", generation };
  };

  let generation = 0;

  const onPickerChange = async (value, { storage, reload, apply }) => {
    const locale = writePreference(storage, value);
    if (locale === "en") {
      reload();
      return locale;
    }
    await apply(locale);
    return locale;
  };

  const attach = (deps) => {
    const doc = deps.document;
    const storage = safeStorage(deps.storage);
    const session = safeStorage(deps.sessionStorage);
    const reload = deps.reload;
    const api =
      deps.translatorApi !== undefined
        ? deps.translatorApi
        : browserTranslatorApi(deps.fetchImpl);
    const networkApi =
      deps.networkApi !== undefined
        ? deps.networkApi
        : deps.translatorApi !== undefined
          ? null
          : api && api.kind === "network"
            ? null
            : createNetworkTranslatorApi({
                fetchImpl: deps.fetchImpl || defaultFetchImpl(),
              });
    const picker = doc.getElementById("lupaxa-lang");
    if (!picker) {
      return;
    }
    const note = doc.getElementById("lupaxa-lang-fallback");
    const dismiss = doc.getElementById("lupaxa-lang-fallback-dismiss");

    const setNoteHidden = (hidden) => {
      if (note) {
        note.hidden = hidden;
      }
    };

    const setNoteText = (text) => {
      const el =
        note && typeof note.querySelector === "function"
          ? note.querySelector(".lupaxa-lang-fallback__text")
          : null;
      if (el) {
        el.textContent = text;
      }
    };

    const dismissed = () =>
      session.getItem(FALLBACK_DISMISS_KEY) === "1";

    const apply = async (locale, { userActivation = false } = {}) => {
      generation += 1;
      const myGeneration = generation;
      if (dismiss) {
        dismiss.textContent = uiString(locale, "dismiss");
      }
      if (locale !== "en") {
        if (api && api.kind === "network") {
          setNoteText(uiString(locale, "translating"));
          setNoteHidden(false);
        } else {
          setNoteHidden(true);
        }
      }
      const roots = [];
      const header = doc.querySelector(".md-header");
      const main = doc.querySelector(".md-main");
      if (header) {
        roots.push(header);
      }
      if (main) {
        roots.push(main);
      }
      const result = await runTranslation({
        locale,
        api,
        roots,
        document: doc,
        generation: myGeneration,
        currentGeneration: () => generation,
        userActivation,
        cacheStorage: storage,
        networkApi,
        onNetwork() {
          setNoteText(uiString(locale, "translating"));
          setNoteHidden(false);
        },
        onProgress() {
          setNoteText(uiString(locale, "downloading"));
          setNoteHidden(false);
        },
      });
      if (result.status === "ok" || result.status === "skipped") {
        setNoteHidden(true);
      } else if (result.status === "needs-activation") {
        setNoteText(uiString(locale, "needsActivation"));
        setNoteHidden(dismissed());
      } else if (result.status === "fallback" || result.status === "failed") {
        if (result.status === "failed" && typeof console !== "undefined") {
          console.warn("Lupaxa language preference: translation failed");
        }
        setNoteText(uiString(locale, "fallback"));
        setNoteHidden(!shouldShowFallback(locale, false, dismissed()));
      } else {
        setNoteHidden(true);
      }
      if (picker) {
        picker.value = locale;
      }
      return result;
    };

    if (picker && !picker.dataset.lupaxaLangBound) {
      picker.dataset.lupaxaLangBound = "1";
      picker.addEventListener("change", () => {
        onPickerChange(picker.value, {
          storage,
          reload,
          apply: (locale) => apply(locale, { userActivation: true }),
        });
      });
    }
    if (dismiss && !dismiss.dataset.lupaxaLangBound) {
      dismiss.dataset.lupaxaLangBound = "1";
      dismiss.addEventListener("click", () => {
        session.setItem(FALLBACK_DISMISS_KEY, "1");
        setNoteHidden(true);
      });
    }

    const locale = readPreference(storage);
    if (picker) {
      picker.value = locale;
    }
    return apply(locale);
  };

  return {
    STORAGE_KEY,
    FALLBACK_DISMISS_KEY,
    CACHE_KEY,
    LOCALES,
    safeStorage,
    readPreference,
    writePreference,
    isUrlLikeText,
    isSkippableElement,
    shouldSkipNode,
    collectTextNodes,
    uiString,
    parseNetworkTranslations,
    buildNetworkTranslateUrl,
    createNetworkTranslatorApi,
    browserTranslatorApi,
    translatorAvailable,
    shouldShowFallback,
    applyDocumentLang,
    translateTextNodes,
    runTranslation,
    onPickerChange,
    attach,
  };
})();

if (typeof window !== "undefined") {
  window.LupaxaLanguagePreference = LupaxaLanguagePreference;
}

if (
  typeof window !== "undefined" &&
  window.document &&
  window.LupaxaPageLifecycle &&
  typeof window.LupaxaPageLifecycle.onPageRender === "function"
) {
  window.LupaxaPageLifecycle.onPageRender(() => {
    LupaxaLanguagePreference.attach({
      document: window.document,
      storage: window.localStorage,
      sessionStorage: window.sessionStorage,
      reload: () => window.location.reload(),
    });
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = LupaxaLanguagePreference;
}
