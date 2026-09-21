//Скрипт проверки на странице сервера (цикл Multi-vote)
//
// После каждого сайта голосования фон возвращает вкладку на страницу сервера
// (serverUrl) и внедряет этот скрипт: он дожидается появления элемента,
// заданного как "подтверждение голосов" (voteVerify — CSS-селектор или текст,
// например "Voted" или #vote-ok).
//
// - элемент найден          → {verifyPassed: true}  → следующий сайт голосования
// - элемент не появился за 60с → {verifyFailed: true} → шаг считается несостоявшимся
//
// Здесь нет никакого взаимодействия со страницей — только наблюдение.

const VERIFY_TIMEOUT_MS = 60000
const POLL_MS = 500

let resolveVerify
const verifyPromise = new Promise(resolve => resolveVerify = resolve)

chrome.runtime.onMessage.addListener(function (request) {
    if (request.sendVerify) resolveVerify(request.verify)
})

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

async function run() {
    const spec = await Promise.race([
        verifyPromise,
        new Promise(resolve => setTimeout(() => resolve(null), 60000))
    ])

    //Подтверждение не задано — фон не внедряет этот скрипт, но на всякий случай считаем проверку пройденной
    if (!spec) {
        send({verifyPassed: true})
        return
    }

    const start = Date.now()
    while (Date.now() - start < VERIFY_TIMEOUT_MS) {
        //helpers — в scripts/main/finders.js; ищем любой видимый элемент (не обязательно кнопку)
        if (findersFindElement(spec, {clickableOnly: false})) {
            send({verifyPassed: true})
            return
        }
        await wait(POLL_MS)
    }

    //Подтверждение так и не появилось — голос не считается валидным
    send({verifyFailed: true})
}

// noinspection JSIgnoredPromiseFromCall
run()
