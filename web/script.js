const SUPPORT_TELEGRAM = "https://t.me/BOT_USERNAME";

const SUBSCRIPTION_HOST = window.location.origin;
const BOT_URL = window.location.origin;
const $ = (s) => document.querySelector(s);
const icons = { ios: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16.7 13.4c0-2.5 2-3.7 2.1-3.8-1.2-1.7-3-1.9-3.6-1.9-1.5-.2-3 .9-3.8.9s-2-.9-3.3-.9C5.4 7.7 3 9.3 3 13c0 1.1.2 2.3.7 3.6.7 1.7 1.7 3.6 3.2 3.5.8 0 1.3-.6 2.4-.6 1.1 0 1.6.6 2.5.6 1.5 0 2.4-1.8 3.1-3.5.5-1.2.7-2.4.7-2.4-.1 0-2.9-.8-2.9-3.8zM14.3 6.2c1.2-1.4 1.1-2.7 1.1-3.2-1.1.1-2.3.8-3 1.7-.8.9-1.2 2-1.1 3.1 1.2.1 2.3-.6 3-1.6z"/></svg>', android: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7.2 9.1 5.8 6.7a.6.6 0 1 1 1-.6l1.5 2.5a9 9 0 0 1 7.4 0l1.5-2.5a.6.6 0 1 1 1 .6l-1.4 2.4A5.3 5.3 0 0 1 20 13.8H4a5.3 5.3 0 0 1 3.2-4.7ZM8.5 12a.7.7 0 1 0 0-1.4.7.7 0 0 0 0 1.4Zm7 0a.7.7 0 1 0 0-1.4.7.7 0 0 0 0 1.4ZM4 15h16v3.1c0 .8-.6 1.4-1.4 1.4H5.4c-.8 0-1.4-.6-1.4-1.4V15Z"/></svg>', windows: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 4.4 10 3v8H3V4.4Zm8 6.6V2.8L21 1v10H11ZM3 12h7v8.9l-7-1.2V12Zm8 0h10v11l-10-1.8V12Z"/></svg>', mac: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.4 12.7c0-2 1.6-3 1.7-3.1-1-1.4-2.5-1.6-3-1.6-1.3-.1-2.5.8-3.1.8-.7 0-1.6-.8-2.7-.8-1.4 0-2.7.8-3.4 2.1-1.5 2.5-.4 6.2 1 8.1.7.9 1.5 1.9 2.6 1.8 1 0 1.4-.7 2.7-.7 1.2 0 1.6.7 2.7.7 1.1 0 1.9-1 2.5-1.9.8-1.1 1.1-2.2 1.1-2.3-.1 0-2.1-.8-2.1-3.1ZM15.3 6.7c.5-.6.9-1.5.8-2.4-.8 0-1.8.5-2.3 1.1-.5.6-.9 1.5-.8 2.3.9.1 1.8-.4 2.3-1Z"/></svg>' };
const platforms = {
    ios: {
        name: "iPhone / iPad",
        short: "iOS",
        icon: icons.ios,
        download: "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
        steps: [
            ["Установите Happ", "Откройте App Store и установите приложение Happ."],
            ["Импортируйте подписку", "Нажмите кнопку ниже — Happ откроется с уже подготовленной конфигурацией.", "import"],
            ["Подтвердите VPN-доступ", "Разрешите добавление VPN-конфигурации в системном диалоге."],
            ["Подключитесь", "Выберите профиль Via и нажмите кнопку подключения."],
        ],
    },
    android: {
        name: "Android",
        short: "Android",
        icon: icons.android,
        download: "https://github.com/Happ-proxy/happ-android/releases/latest",
        steps: [
            ["Установите Happ", "Скачайте и установите Happ для Android."],
            ["Импортируйте подписку", "Откройте ссылку импорта — приложение добавит конфигурацию.", "import"],
            ["Разрешите VPN", "Подтвердите стандартный системный запрос Android."],
            ["Подключитесь", "В Happ выберите Via и включите подключение."],
        ],
    },
    windows: {
        name: "Windows",
        short: "Windows",
        icon: icons.windows,
        download: "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
        steps: [
            ["Скачайте Happ", "Загрузите официальный установщик для Windows.", "download"],
            ["Установите приложение", "Запустите скачанный файл и завершите установку."],
            ["Скопируйте подписку", "Скопируйте ссылку сверху и импортируйте её в Happ.", "copy"],
            ["Подключитесь", "Выберите профиль Via и активируйте соединение."],
        ],
    },
    mac: {
        name: "macOS",
        short: "macOS",
        icon: icons.mac,
        download: "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
        steps: [
            ["Установите Happ", "Откройте App Store и установите Happ для macOS.", "download"],
            ["Импортируйте подписку", "Нажмите кнопку импорта и подтвердите открытие Happ.", "import"],
            ["Разрешите VPN", "Подтвердите добавление конфигурации в настройках macOS."],
            ["Подключитесь", "Активируйте профиль Via в приложении."],
        ],
    },
};
const supportBtn = document.getElementById("support-btn");
if (supportBtn) {
    supportBtn.href = SUPPORT_TELEGRAM;
}
function art() {
    return '<div class="art-wrap" aria-hidden="true"><div class="signal-art"><i class="ray"></i><i class="ring r1"></i><i class="ring r2"></i><i class="ring r3"></i><i class="planet"></i><i class="star s1"></i><i class="star s2"></i><i class="star s3"></i></div></div>';
}
function home() {
    $("#app").innerHTML = `<section class="home"><div class="home-copy"><h1>Интернет<br>без <em>шума.</em></h1><p>Приватное подключение для тех, кому нужен быстрый и спокойный доступ к сети. Одна подписка — все ваши устройства.</p><a class="primary" href="${BOT_URL}">Получить доступ <span>→</span></a></div>${art()}</section>`;
}
function subscription(id) {
    const url = SUBSCRIPTION_HOST + "/subscribe/" + encodeURIComponent(id);
    $("#app").innerHTML =
        `<section class="subscription"><div class="sub-intro"><div><h1>Подключение<br>готово.</h1></div><p>Добавьте подписку в приложение и пользуйтесь интернетом на любом устройстве.</p></div><div class="dashboard"><div><article class="card connection"><div class="card-eyebrow">Ваша подписка</div><div class="plan-row"><h2>${escapeHtml(id)}</h2><span class="status"><i></i>активна</span></div><div class="readout"><div class="metric"><span>Трафик</span><b>Безлимитный</b></div><div class="metric"><span>Устройства</span><b>Без ограничений</b></div></div><div class="link-area"><div class="link-head"><span class="card-eyebrow">Ссылка подписки</span><button class="copy-button" id="copy-main">Копировать</button></div><div class="subscription-link"><code id="sub-url">${url}</code><button class="copy-square" id="copy-icon" aria-label="Копировать"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></div></div><div class="connect-actions"><button class="primary" id="choose-device">Настроить устройство</button><button class="secondary" id="copy-secondary">Скопировать ссылку</button></div></article><article class="card setup" id="setup"><div class="setup-head"><span class="card-eyebrow">Выберите устройство</span><span class="mono" id="selected-label" style="font-size:11px;color:var(--muted)">01 / 04</span></div><div class="platforms" id="platforms"></div><div class="steps" id="steps"></div><button class="back" id="back">← Выбрать другое устройство</button></article></div><aside class="sidebar"><article class="card side-card">${art()}<h3>Сигнал<br>стабилен.</h3><p>Подписка готова к импорту. Выберите своё устройство, чтобы увидеть короткую инструкцию.</p></article><article class="card side-card"><div class="card-eyebrow">Нужна помощь?</div><ul class="help-list"><li><span>Проблема с импортом</span><b>01</b></li><li><span>Не подключается</span><b>02</b></li><li><span>Новая ссылка</span><b>03</b></li></ul><a class="primary support-button" href="${BOT_URL}">Написать в поддержку</a></article></aside></div></section>`;
    const copy = () => copyText(url);
    ["copy-main", "copy-icon", "copy-secondary"].forEach((x) => ($("#" + x).onclick = copy));
    $("#choose-device").onclick = () => $("#setup").scrollIntoView({ behavior: "smooth", block: "center" });
    renderPlatforms(url);
}
function renderPlatforms(url) {
    const host = $("#platforms");
    host.innerHTML = Object.entries(platforms)
        .map(([key, p]) => `<button class="platform" data-platform="${key}">${p.icon}<b>${p.short}</b><small>${p.name}</small></button>`)
        .join("");
    host.querySelectorAll(".platform").forEach((button) => (button.onclick = () => showSteps(button.dataset.platform, url)));
}
function showSteps(key, url) {
    const p = platforms[key];
    document.querySelectorAll(".platform").forEach((x) => x.classList.toggle("active", x.dataset.platform === key));
    $("#selected-label").textContent = p.short.toUpperCase();
    const action = (type) => (type === "import" ? `<a class="primary" href="happ://add/${url}">Импортировать в Happ</a>` : type === "copy" ? `<button class="secondary copy-inline">Скопировать ссылку</button>` : type === "download" ? `<a class="primary" href="${p.download}" target="_blank" rel="noopener">Скачать Happ</a>` : "");
    $("#steps").innerHTML = p.steps.map((s, i) => `<div class="step"><span class="step-number">0${i + 1}</span><div><h3>${s[0]}</h3><p>${s[1]}</p>${action(s[2])}</div></div>`).join("");
    $("#steps").classList.add("open");
    $("#back").classList.add("show");
    document.querySelectorAll(".copy-inline").forEach((x) => (x.onclick = () => copyText(url)));
    $("#back").onclick = () => {
        $("#steps").classList.remove("open");
        $("#back").classList.remove("show");
        document.querySelectorAll(".platform").forEach((x) => x.classList.remove("active"));
        $("#selected-label").textContent = "01 / 04";
    };
}
function copyText(text) {
    if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    else fallbackCopy(text);
    const t = $("#toast");
    t.classList.add("visible");
    setTimeout(() => t.classList.remove("visible"), 1900);
}
function fallbackCopy(text) {
    const el = document.createElement("textarea");
    el.value = text;
    document.body.append(el);
    el.select();
    document.execCommand("copy");
    el.remove();
}
function escapeHtml(v) {
    const el = document.createElement("div");
    el.textContent = v;
    return el.innerHTML;
}
const saved = localStorage.getItem("Via-theme");
if (saved) document.documentElement.dataset.theme = saved;
$("#theme").onclick = () => {
    const current = document.documentElement.dataset.theme || "dark";
    const next = current === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("Via-theme", next);
};
const token = new URLSearchParams(location.search).get("sub");
token ? subscription(token) : home();
