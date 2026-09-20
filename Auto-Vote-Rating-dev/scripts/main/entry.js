//Скрипт "страницы входа" (entry page) — общая, не привязана к какому-либо сайту
//
// Логика:
// 1. Фон (background.js) открывает вкладку с URL проекта entryUrl вместо обычной URL голосования.
// 2. Этот скрипт ожидает появления кнопки entryButton (CSS-селектор или текст кнопки,
//    поддержка динамической загрузки кнопки — опрашиваем страницу каждые POLL_MS).
// 3. Нажимаем кнопку ровно ОДИН раз (защита от двойного клика и петель) и сообщаем фону.
// 4. Ждём редирект:
//    - полная загрузка новой страницы → скрипт умирает вместе со страницей, новую страницу
//      обработает background.js (webNavigation.onCompleted → обычный скрипт голосования);
//    - редирект без полной перезагрузки (SPA, pushState) → сообщаем фону {entryRedirected: true},
//      фон сам внедрит обычный скрипт голосования в текущую страницу;
//    - та же страница (изменился только query/hash) → продолжаем ждать.
// 5. Если появляется CAPTCHA — мы её НЕ решаем (никакого автоматического прохождения):
//    сообщаем фону {captcha: true} (пользователь получит уведомление и увидит вкладку),
//    ПАУЗИРУЕМ и ждём, пока пользователь решит её вручную, затем продолжаем с того же места.

const POLL_MS = 500
//Сколько ждать появления кнопки (она может подгружаться динамически)
const FIND_BUTTON_TIMEOUT_MS = 120000
//Сколько ждать редиректа после нажатия кнопки
const WAIT_REDIRECT_TIMEOUT_MS = 120000

let clicked = false
let captchaReported = false

function send(request) {
    try {
        chrome.runtime.sendMessage(request)
    } catch (error) {
        //Фон мог уснуть или вкладка закрывается — ничего не делаем
    }
}

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

function isVisibleElement(elem) {
    if (!(elem instanceof Element)) return false
    const style = getComputedStyle(elem)
    if (style.display === 'none') return false
    if (style.visibility !== 'visible') return false
    if (style.opacity && style.opacity < 0.5) return false
    if (elem.offsetHeight < 16 || elem.offsetWidth < 16) return false
    if (elem.offsetWidth + elem.offsetHeight + elem.getBoundingClientRect().height + elem.getBoundingClientRect().width === 0) return false
    return true
}

function isDisabledElement(elem) {
    return elem.disabled === true || elem.hasAttribute('disabled') || elem.getAttribute('aria-disabled') === 'true'
}

function normalizeText(text) {
    return (text || '').replace(/\s+/g, ' ').trim().toLowerCase()
}

function getElementText(elem) {
    return (elem.value || elem.textContent || elem.getAttribute('title') || elem.getAttribute('aria-label') || '')
}

//Смотрится ли переданная строка как CSS-селектор (в остальных случаях это текст кнопки)
function isSelectorLike(spec) {
    return /[#[\]>~*^$=]/.test(spec) || /(^|\s)\./.test(spec) || /\s/.test(spec)
}

//Ищем кнопку по entryButton: сначала как CSS-селектор (если похоже), затем по тексту
// (сначала точное совпадение, затем вхождение в текст короткой кнопки)
function findButton(spec) {
    if (!spec) return null
    spec = spec.trim()
    if (!spec) return null

    let candidates = null
    if (isSelectorLike(spec)) {
        try {
            candidates = Array.from(document.querySelectorAll(spec))
        } catch (error) {
            candidates = null
        }
    }

    if (candidates && candidates.length) {
        for (const el of candidates) {
            if (isVisibleElement(el) && !isDisabledElement(el)) return el
        }
        //Селектор задан, но подходящей видимой кнопки нет — пробуем ещё и по тексту
    }

    const elements = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"], [role="button"]'))
    const needle = normalizeText(spec)
    if (!needle) return null
    let fallback = null
    let fallbackLength = Infinity
    for (const el of elements) {
        if (isDisabledElement(el) || !isVisibleElement(el)) continue
        const text = normalizeText(getElementText(el))
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

//Обнаруживаем CAPTCHA на главной странице (iframe или виджет)
// ВАЖНО: мы только ОБНАРУЖИВАЕМ её и ждём ручного прохождения пользователем,
// никакого автоматического нажатия/решения здесь нет
function findCaptcha() {
    const selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="captcha"]',
        '.g-recaptcha',
        '.grecaptcha-badge',
        '.h-captcha',
        '.cf-turnstile',
        '#recaptcha',
        '#challenge-form',
        '#challenge-stage',
        '#challenge-running'
    ]
    for (const selector of selectors) {
        const el = document.querySelector(selector)
        if (el && isVisibleElement(el)) return true
    }
    //Страница-проверка CloudFlare (там обычно нет отдельных виджетов)
    const title = document.querySelector('title')?.textContent || ''
    if (/just a moment|attention required|verifying you are human|checking your browser|проверка браузера/i.test(title)) return true
    return false
}

//origin + pathname без query/hash — по ним определяем "перешли ли мы на другую страницу"
function getOriginPath(href) {
    try {
        const u = new URL(href)
        return u.origin + u.pathname
    } catch (error) {
        return href
    }
}

async function waitConfig() {
    return new Promise(resolve => {
        chrome.runtime.onMessage.addListener(function (request) {
            if (request.sendEntry) resolve(request.project)
        })
        //Фон шлёт настройки сразу после внедрения, но на всякий случай не ждём вечно
        setTimeout(() => resolve(null), 60000)
    })
}

async function run() {
    const project = await waitConfig()
    if (!project || !project.entryButton) {
        send({entryNoConfig: true})
        return
    }

    const originPath = getOriginPath(document.location.href)
    const findStart = Date.now()

    //1) Ждём появления кнопки и нажимаем её ОДИН раз
    while (!clicked) {
        if (findCaptcha()) {
            if (!captchaReported) {
                captchaReported = true
                //Уведомляем пользователя, что нужен ручной CAPTCHA; фон покажет уведомление с вкладкой
                send({captcha: true})
            }
            //Пауза: ждём, пока пользователь решит капчу вручную
            await wait(POLL_MS)
            continue
        }
        captchaReported = false

        const button = findButton(project.entryButton)
        if (button) {
            clicked = true
            //Сообщаем фону до клика: если страница перезагрузится на том же URL,
            //фон поймёт что клик был и запустит обычный скрипт голосования
            send({entryClicked: true})
            button.click()
            break
        }

        if (Date.now() - findStart > FIND_BUTTON_TIMEOUT_MS) {
            send({entryButtonNotFound: true})
            return
        }
        await wait(POLL_MS)
    }

    //2) Ждём редирект после клика
    let lastHref = document.location.href
    const redirectStart = Date.now()
    while (true) {
        if (document.location.href !== lastHref) {
            lastHref = document.location.href
            if (getOriginPath(lastHref) !== originPath) {
                //URL изменился, но страница не перезагрузилась (SPA) —
                //фон внедрит обычный скрипт голосования в текущую страницу
                send({entryRedirected: true})
                return
            }
            //Перешли на ту же страницу (только query/hash) — продолжаем ждать
        }

        if (findCaptcha()) {
            if (!captchaReported) {
                captchaReported = true
                send({captcha: true})
            }
            //Пауза: ждём ручного прохождения капчи пользователем
        }

        if (Date.now() - redirectStart > WAIT_REDIRECT_TIMEOUT_MS) {
            //Клик был, но никуда не перенаправило
            send({entryTimeout: true})
            return
        }
        await wait(POLL_MS)
    }
}

// noinspection JSIgnoredPromiseFromCall
run()
