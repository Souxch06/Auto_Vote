// Test fonctionnel de scripts/main/entry.js — chaque instance tourne dans un
// contexte vm isolé (comme un vrai content script : son propre chrome, son document)
'use strict'
const fs = require('fs')
const path = require('path')
const assert = require('assert')
const vm = require('vm')

const ROOT = path.join(__dirname, '..', 'Auto-Vote-Rating-dev')
const realSetTimeout = setTimeout
const realSetInterval = setInterval
const realClearInterval = clearInterval

// ---------- Mini DOM ----------
class Element {}

class FakeElement extends Element {
    constructor(opts = {}) {
        super()
        this.textContent = opts.text || ''
        this.value = opts.value
        this.attrs = opts.attrs || {}
        this.disabled = !!opts.disabled
        this.clicked = 0
        this.visible = opts.visible !== false
    }
    hasAttribute(n) { return n in this.attrs }
    getAttribute(n) { return this.attrs[n] != null ? String(this.attrs[n]) : null }
    getBoundingClientRect() {
        return this.visible ? {width: 100, height: 30, top: 0, left: 0} : {width: 0, height: 0, top: 0, left: 0}
    }
    get offsetHeight() { return this.visible ? 30 : 0 }
    get offsetWidth() { return this.visible ? 100 : 0 }
    click() { this.clicked++ }
}

function matches(el, sel) {
    let m
    if (sel.startsWith('#')) return el.attrs.id === sel.slice(1)
    if (sel.startsWith('.')) return (el.attrs.class || '').split(' ').includes(sel.slice(1))
    if (m = sel.match(/^([a-z-]+)\[src\*="(.+)"\]$/)) return el.attrs.tag === m[1] && (el.attrs.src || '').includes(m[2])
    if (m = sel.match(/^([a-z-]+)\[type="(.+)"\]$/)) return el.attrs.tag === m[1] && el.attrs.type === m[2]
    if (m = sel.match(/^\[role="(.+)"\]$/)) return el.attrs.role === m[1]
    if (/^[a-z-]+$/.test(sel)) return el.attrs.tag === sel
    return false
}

function makeDocument(opts) {
    return {
        location: {href: opts.href},
        _buttons: opts.buttons || [],
        _captchas: opts.captchaEls || [],
        _title: opts.title || null,
        querySelector(sel) {
            if (sel === 'title') return this._title ? {textContent: this._title} : null
            for (const el of [...this._buttons, ...this._captchas]) {
                if (matches(el, sel)) return el
            }
            return null
        },
        querySelectorAll(sel) {
            if (sel === 'a, button, input[type="button"], input[type="submit"], [role="button"]') {
                return this._buttons
            }
            return this._buttons.filter(el => matches(el, sel))
        }
    }
}

const fakeComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: 1})

// ---------- Temps simulé (partagé) ----------
let now = 0
const simSetTimeout = (fn, ms) => realSetTimeout(() => { now += (ms || 0); fn() }, Math.min(ms || 0, 5))
function tick(ms) { return new Promise(r => realSetTimeout(r, ms || 5)) }

async function waitMsg(sent, pred, label, timeoutMs = 5000) {
    const end = process.hrtime.bigint() + BigInt(timeoutMs) * 1000000n
    while (true) {
        if (sent.some(pred)) return sent.find(pred)
        if (process.hrtime.bigint() > end) assert.fail(label + ' — messages: ' + JSON.stringify(sent.slice(0, 5)))
        await tick()
    }
}

// ---------- Chargement d'entry.js dans un contexte isolé ----------
function loadEntry(doc, sent) {
    let docRef = doc
    const listeners = []
    const sandbox = {
        chrome: {
            runtime: {
                onMessage: {addListener: fn => listeners.push(fn)},
                sendMessage: req => sent.push(req)
            }
        },
        Element,
        getComputedStyle: fakeComputedStyle,
        URL,
        setTimeout: simSetTimeout,
        clearTimeout: () => {},
        console
    }
    sandbox.Date = {now: () => now}
    Object.defineProperty(sandbox, 'document', {get: () => docRef, configurable: true})
    vm.createContext(sandbox)
    const file = path.join(ROOT, 'scripts/main/entry.js')
    vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox, {filename: file})
    return {
        listeners,
        setDocument: d => { docRef = d }
    }
}

function sendConfig(env, project) {
    simSetTimeout(() => { for (const l of env.listeners) l({sendEntry: true, project, settings: {}}) }, 1)
}

// ---------- Tests ----------
const results = []
async function test(name, fn) {
    try {
        await fn()
        results.push(['PASS', name])
    } catch (e) {
        results.push(['FAIL', name + ' :: ' + (e.message || e)])
    }
}

;(async () => {
    await test('texte "Vote" : clic unique, entryClicked puis entryRedirected (SPA)', async () => {
        const sent = []
        const btn = new FakeElement({text: 'Voter maintenant'})
        const other = new FakeElement({text: 'Re-votez plus tard'})
        const doc = makeDocument({href: 'https://skyofskill.net/vote', buttons: [btn, other]})
        const env = loadEntry(doc, sent)
        sendConfig(env, {entryUrl: '/vote', entryButton: 'Vote'})
        await waitMsg(sent, m => m.entryClicked, 'entryClicked jamais envoyé')
        assert.strictEqual(btn.clicked, 1, 'clic unique')
        assert.strictEqual(other.clicked, 0, 'bonne bouton choisi (pas "Re-votez plus tard")')
        // SPA : le path change sans rechargement
        doc.location.href = 'https://skyofskill.net/servers/123/vote'
        await waitMsg(sent, m => m.entryRedirected, 'entryRedirected jamais envoyé')
        const iClicked = sent.findIndex(m => m.entryClicked)
        const iRedirected = sent.findIndex(m => m.entryRedirected)
        assert.ok(iClicked < iRedirected, 'ordre des messages')
    })

    await test('bouton chargé dynamiquement (apparaît après quelques polls)', async () => {
        const sent = []
        const doc = makeDocument({href: 'https://x.com/vote', buttons: []})
        const env = loadEntry(doc, sent)
        sendConfig(env, {entryButton: 'Vote'})
        const btn = new FakeElement({text: 'Vote'})
        let ticks = 0
        const poller = realSetInterval(() => {
            ticks++
            if (ticks === 3) doc._buttons.push(btn)
        }, 5)
        try {
            await waitMsg(sent, m => m.entryClicked, 'bouton dynamique jamais cliqué')
        } finally {
            realClearInterval(poller)
        }
        assert.strictEqual(btn.clicked, 1)
    })

    await test('sélecteur CSS #vote-btn', async () => {
        const sent = []
        const btn = new FakeElement({text: 'Voter', attrs: {id: 'vote-btn'}})
        const doc = makeDocument({href: 'https://x.com/vote', buttons: [btn]})
        const env = loadEntry(doc, sent)
        sendConfig(env, {entryButton: '#vote-btn'})
        await waitMsg(sent, m => m.entryClicked, 'sélecteur #vote-btn non trouvé')
        assert.strictEqual(btn.clicked, 1)
    })

    await test('sélecteur CSS .vote-button (classe)', async () => {
        const sent = []
        const btn = new FakeElement({text: 'Go', attrs: {class: 'btn vote-button'}})
        const doc = makeDocument({href: 'https://x.com/vote', buttons: [btn]})
        const env = loadEntry(doc, sent)
        sendConfig(env, {entryButton: '.vote-button'})
        await waitMsg(sent, m => m.entryClicked, 'sélecteur .vote-button non trouvé')
        assert.strictEqual(btn.clicked, 1)
    })

    await test('bouton invisible ou disabled : ignoré, le visible est cliqué', async () => {
        const sent = []
        const hidden = new FakeElement({text: 'Vote', visible: false})
        const disabled = new FakeElement({text: 'Vote', disabled: true})
        const good = new FakeElement({text: 'Vote', attrs: {id: 'good'}})
        const doc = makeDocument({href: 'https://x.com/vote', buttons: [hidden, disabled, good]})
        const env = loadEntry(doc, sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.entryClicked, 'bouton visible jamais cliqué')
        assert.strictEqual(good.clicked, 1)
        assert.strictEqual(hidden.clicked, 0)
        assert.strictEqual(disabled.clicked, 0)
    })

    await test('CAPTCHA : pause + un seul avertissement + reprise après résolution manuelle', async () => {
        const sent = []
        const btn = new FakeElement({text: 'Vote'})
        const captcha = new FakeElement({attrs: {tag: 'iframe', src: 'https://www.google.com/recaptcha/api2/anchor'}})
        const env = loadEntry(makeDocument({href: 'https://x.com/vote', buttons: [btn], captchaEls: [captcha]}), sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.captcha, 'captcha non signalé')
        assert.strictEqual(btn.clicked, 0, 'pas de clic pendant la pause')
        await tick(50)
        assert.strictEqual(sent.filter(m => m.captcha).length, 1, 'un seul avertissement pendant la pause')
        // l'utilisateur résout le captcha : il disparaît du DOM
        env.setDocument(makeDocument({href: 'https://x.com/vote', buttons: [btn]}))
        await waitMsg(sent, m => m.entryClicked, 'pas de clic après résolution du captcha')
        assert.strictEqual(btn.clicked, 1)
    })

    await test('page CloudFlare (titre "Just a moment") détectée comme CAPTCHA', async () => {
        const sent = []
        const env = loadEntry(makeDocument({href: 'https://x.com/vote', buttons: [], title: 'Just a moment...'}), sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.captcha, 'page CF non détectée')
    })

    await test('hCaptcha en iframe détectée', async () => {
        const sent = []
        const captcha = new FakeElement({attrs: {tag: 'iframe', src: 'https://js.hcaptcha.com/1/api.js'}})
        const env = loadEntry(makeDocument({href: 'https://x.com/vote', buttons: [], captchaEls: [captcha]}), sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.captcha, 'hCaptcha non détectée')
    })

    await test('pas de bouton → entryButtonNotFound', async () => {
        const sent = []
        const env = loadEntry(makeDocument({href: 'https://x.com/vote', buttons: []}), sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.entryButtonNotFound, 'entryButtonNotFound jamais envoyé', 15000)
        assert.ok(!sent.some(m => m.entryClicked))
    })

    await test('clic fait mais pas de redirection → entryTimeout', async () => {
        const sent = []
        const btn = new FakeElement({text: 'Vote'})
        const env = loadEntry(makeDocument({href: 'https://x.com/vote', buttons: [btn]}), sent)
        sendConfig(env, {entryButton: 'Vote'})
        await waitMsg(sent, m => m.entryClicked, 'pas de clic')
        await waitMsg(sent, m => m.entryTimeout, 'entryTimeout jamais envoyé', 15000)
    })

    await test('pas de config → entryNoConfig', async () => {
        const sent = []
        loadEntry(makeDocument({href: 'https://x.com/vote', buttons: []}), sent)
        await waitMsg(sent, m => m.entryNoConfig, 'entryNoConfig jamais envoyé', 15000)
    })

    // ---------- Helpers background.js ----------
    await test('getEntryURL / isSameOriginPath (helpers background.js)', async () => {
        const src = fs.readFileSync(path.join(ROOT, 'background.js'), 'utf8')
        const start = src.indexOf('function getEntryURL')
        const end = src.indexOf('async function updateValue')
        const allProjects = {
            'topcraft.ru': {voteURL: p => 'https://topcraft.club/servers/' + p.id + '/vote/'}
        }
        const fn = new Function('allProjects', src.slice(start, end) + '\nreturn {getEntryURL, isSameOriginPath}')
        const {getEntryURL, isSameOriginPath} = fn(allProjects)

        assert.strictEqual(getEntryURL({rating: 'topcraft.ru', id: '123', entryUrl: '/vote'}), 'https://topcraft.club/vote')
        assert.strictEqual(getEntryURL({rating: 'topcraft.ru', id: '123', entryUrl: 'https://mysite.net/vote'}), 'https://mysite.net/vote')
        assert.strictEqual(getEntryURL({rating: 'topcraft.ru', id: '123', entryUrl: 'vote?x=1'}), 'https://topcraft.club/vote?x=1')
        assert.strictEqual(getEntryURL({rating: 'topcraft.ru', id: '123'}), null)

        assert.strictEqual(isSameOriginPath('https://a.com/vote?x=1', 'https://a.com/vote'), true)
        assert.strictEqual(isSameOriginPath('https://a.com/vote', 'https://a.com/vote#frag'), true)
        assert.strictEqual(isSameOriginPath('https://a.com/vote', 'https://a.com/other'), false)
        assert.strictEqual(isSameOriginPath('https://b.com/vote', 'https://a.com/vote'), false)
        assert.strictEqual(isSameOriginPath('not a url', 'https://a.com/vote'), false)
    })

    // ---------- Rapport ----------
    for (const [status, name] of results) {
        console.log(status, '-', name)
    }
    const failed = results.filter(r => r[0] === 'FAIL')
    console.log('')
    console.log(`${results.length - failed.length}/${results.length} tests OK`)
    realSetTimeout(() => process.exit(failed.length ? 1 : 0), 10)
})().catch(e => {
    console.error('ERREUR TEST:', e)
    realSetTimeout(() => process.exit(2), 10)
})
