//Helpers communs aux scripts de page d'entrée (entry.js) et de vérification (verify.js)
//Monde isolé, aucune API chrome — uniquement le DOM
//
//Chaque fonction cherche un élément VISIBLE et cliquable correspondant à une
//spécification donnée : soit un CSS-sélecteur (ex. #vote-btn, .vote-button),
//soit un texte (ex. Vote, Voter) — d'abord le texte exact, à défaut le plus
//court des textes contenant la spécification (évite les faux positifs)

function findersIsVisibleElement(elem) {
    if (!(elem instanceof Element)) return false
    const style = getComputedStyle(elem)
    if (style.display === 'none') return false
    if (style.visibility !== 'visible') return false
    if (style.opacity && style.opacity < 0.5) return false
    if (elem.offsetHeight < 16 || elem.offsetWidth < 16) return false
    if (elem.offsetWidth + elem.offsetHeight + elem.getBoundingClientRect().height + elem.getBoundingClientRect().width === 0) return false
    return true
}

function findersIsDisabledElement(elem) {
    return elem.disabled === true || elem.hasAttribute('disabled') || elem.getAttribute('aria-disabled') === 'true'
}

function findersNormalizeText(text) {
    return (text || '').replace(/\s+/g, ' ').trim().toLowerCase()
}

function findersGetElementText(elem) {
    return (elem.value || elem.textContent || elem.getAttribute('title') || elem.getAttribute('aria-label') || '')
}

//Смотрится ли переданная строка как CSS-селектор (в остальных случаях это текст)
function findersIsSelectorLike(spec) {
    return /[#[\]>~*^$=]/.test(spec) || /(^|\s)\./.test(spec) || /\s/.test(spec)
}

//Ищет первый видимый элемент по спецификации (CSS-селектор или текст)
//options.clickableOnly=true — искать только кликабельные элементы (кнопка на странице входа)
//options.clickableOnly=false — искать любой элемент с текстом (проверка на странице сервера)
function findersFindElement(spec, options) {
    options = options || {}
    if (!spec) return null
    spec = String(spec).trim()
    if (!spec) return null

    let candidates = null
    if (findersIsSelectorLike(spec)) {
        try {
            candidates = Array.from(document.querySelectorAll(spec))
        } catch (error) {
            candidates = null
        }
    }

    if (candidates && candidates.length) {
        for (const el of candidates) {
            if (findersIsVisibleElement(el) && !(options.clickableOnly && findersIsDisabledElement(el))) return el
        }
        //Селектор задан, но подходящего видимого элемента нет — пробуем ещё и по тексту
    }

    const selector = options.clickableOnly
        ? 'a, button, input[type="button"], input[type="submit"], [role="button"]'
        : 'a, button, input[type="button"], input[type="submit"], [role="button"], span, h1, h2, h3, h4, h5, h6, p, li, div'
    const elements = Array.from(document.querySelectorAll(selector))
    const needle = findersNormalizeText(spec)
    if (!needle) return null
    let fallback = null
    let fallbackLength = Infinity
    for (const el of elements) {
        if (!findersIsVisibleElement(el)) continue
        if (options.clickableOnly && findersIsDisabledElement(el)) continue
        const text = findersNormalizeText(findersGetElementText(el))
        if (!text) continue
        //1er visible avec le texte exact
        if (text === needle) return el
        //à défaut, le plus court contenant le texte (évite les faux positifs)
        if (text.includes(needle) && text.length < fallbackLength) {
            fallback = el
            fallbackLength = text.length
        }
    }
    return fallback
}
