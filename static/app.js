(() => {
    "use strict";

    const { me, csrf } = window.APP;
    const settings = window.APP.settings || {};
    const enterToSend = settings.enter_to_send !== false;
    const els = {
        messages: document.getElementById("messages"),
        input: document.getElementById("input"),
        composer: document.getElementById("composer"),
        send: document.getElementById("send"),
        userList: document.getElementById("user-list"),
        onlineCount: document.getElementById("online-count"),
        convTitle: document.getElementById("conv-title"),
        convSub: document.getElementById("conv-sub"),
        status: document.getElementById("conn-status"),
        sidebar: document.getElementById("sidebar"),
        openSidebar: document.getElementById("open-sidebar"),
        closeSidebar: document.getElementById("close-sidebar"),
        roomBtn: document.getElementById("room-btn"),
        meAvatar: document.getElementById("me-avatar"),
        meName: document.getElementById("me-name"),
        meHandle: document.getElementById("me-handle"),
    };

    const state = {
        conv: { kind: "room", username: null, displayName: null },
        lastId: 0,
        seen: new Set(),
        sending: false,
        lastAuthor: null,
        lastTime: 0,
        mounted: false,
    };

    // ---------------- Helpers ----------------

    function colorFor(name) {
        let h = 0;
        for (let i = 0; i < name.length; i += 1) {
            h = (h * 31 + name.charCodeAt(i)) % 360;
        }
        return `hsl(${h} 68% 45%)`;
    }

    function initialOf(name) {
        return (name || "?").trim().charAt(0).toUpperCase();
    }

    function timeLabel(iso) {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    }

    function setStatus(ok, text) {
        els.status.classList.toggle("off", !ok);
        els.status.textContent = "●";
        els.status.title = text || (ok ? "Đã kết nối" : "Mất kết nối");
    }

    async function api(path, options = {}) {
        const opts = {
            credentials: "same-origin",
            ...options,
            headers: { ...(options.headers || {}) },
        };

        if (options.method === "POST") {
            opts.headers["Content-Type"] = "application/json";
            opts.headers["X-CSRF-Token"] = csrf;
        }

        const res = await fetch(path, opts);

        if (res.status === 401) {
            window.location.href = "/login";
            throw new Error("Chưa đăng nhập");
        }

        if (!res.ok) {
            let message = `Lỗi ${res.status}`;
            try {
                const data = await res.json();
                if (data && data.error) message = data.error;
            } catch (_) {
                /* ignore */
            }
            throw new Error(message);
        }

        return res.json();
    }

    function nearBottom() {
        const el = els.messages;
        return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    }

    function scrollToBottom() {
        els.messages.scrollTop = els.messages.scrollHeight;
    }

    let audioCtx = null;

    function beep() {
        if (!settings.sound) return;
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            audioCtx = audioCtx || new Ctx();
            if (audioCtx.state === "suspended") audioCtx.resume();

            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            const now = audioCtx.currentTime;

            osc.type = "sine";
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.0001, now);
            gain.gain.exponentialRampToValueAtTime(0.1, now + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);

            osc.connect(gain).connect(audioCtx.destination);
            osc.start(now);
            osc.stop(now + 0.2);
        } catch (_) {
            /* trình duyệt chặn autoplay: bỏ qua */
        }
    }

    // ---------------- Render ----------------

    function emptyState() {
        const div = document.createElement("div");
        div.className = "empty";
        div.innerHTML = '<span class="big">💬</span><div>Chưa có tin nhắn nào. Hãy bắt đầu cuộc trò chuyện!</div>';
        return div;
    }

    function showEmptyIfNeeded() {
        if (els.messages.querySelector(".msg")) {
            const empty = els.messages.querySelector(".empty");
            if (empty) empty.remove();
            return;
        }
        if (!els.messages.querySelector(".empty")) {
            els.messages.appendChild(emptyState());
        }
    }

    function appendMessage(msg, options = {}) {
        const { forceScroll = false } = options;
        const stick = forceScroll || nearBottom();
        const empty = els.messages.querySelector(".empty");
        if (empty) empty.remove();

        const ts = new Date(msg.created_at).getTime();
        const sameAuthor =
            !msg.mine &&
            state.lastAuthor === msg.username &&
            Number.isFinite(ts) &&
            ts - state.lastTime < 5 * 60 * 1000;

        const row = document.createElement("div");
        row.className = "msg" + (msg.mine ? " mine" : "") + (sameAuthor ? " same-author" : "");
        row.dataset.id = String(msg.id);

        if (!msg.mine) {
            const avatar = document.createElement("div");
            avatar.className = "avatar";
            avatar.style.background = colorFor(msg.username);
            avatar.textContent = initialOf(msg.display_name);
            row.appendChild(avatar);
        }

        const wrap = document.createElement("div");
        wrap.className = "bubble-wrap";

        if (!msg.mine && !sameAuthor) {
            const author = document.createElement("div");
            author.className = "msg-author";
            author.textContent = msg.display_name;
            wrap.appendChild(author);
        }

        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = msg.body; // textContent => chống XSS
        wrap.appendChild(bubble);

        const time = document.createElement("div");
        time.className = "msg-time";
        time.textContent = timeLabel(msg.created_at);
        wrap.appendChild(time);

        row.appendChild(wrap);
        els.messages.appendChild(row);

        state.lastAuthor = msg.mine ? null : msg.username;
        state.lastTime = ts;

        if (stick) scrollToBottom();
    }

    function resetConversationView() {
        els.messages.innerHTML = "";
        state.lastId = 0;
        state.seen.clear();
        state.lastAuthor = null;
        state.lastTime = 0;
    }

    function setHeader(kind, displayName, username) {
        if (kind === "room") {
            els.convTitle.textContent = "# Phòng chung";
            els.convSub.textContent = "Mọi người trong server";
        } else {
            els.convTitle.textContent = displayName;
            els.convSub.textContent = "@" + username;
        }
    }

    function markActiveUser() {
        document.querySelectorAll(".user-row").forEach((row) => {
            row.classList.toggle(
                "active",
                state.conv.kind === "dm" && row.dataset.username === state.conv.username
            );
        });
        els.roomBtn.classList.toggle("active", state.conv.kind === "room");
    }

    // ---------------- Data ----------------

    async function loadMessages(initial = false) {
        const { kind, username } = state.conv;
        const path =
            kind === "room"
                ? `/api/chat?since=${state.lastId}`
                : `/api/dm/${encodeURIComponent(username)}?since=${state.lastId}`;

        const data = await api(path);

        if (initial && data.peer) {
            state.conv.displayName = data.peer.display_name;
            state.conv.username = data.peer.username;
            setHeader("dm", data.peer.display_name, data.peer.username);
        }

        let incoming = false;

        for (const msg of data.messages) {
            if (state.seen.has(msg.id)) continue;
            state.seen.add(msg.id);
            state.lastId = Math.max(state.lastId, msg.id);
            if (!msg.mine) incoming = true;
            appendMessage(msg, { forceScroll: initial });
        }

        if (!initial && incoming) beep();

        if (initial) showEmptyIfNeeded();
        setStatus(true);
    }

    async function openRoom() {
        if (state.conv.kind === "room") return closeSidebar();
        state.conv = { kind: "room", username: null, displayName: null };
        setHeader("room");
        resetConversationView();
        markActiveUser();
        closeSidebar();
        try {
            await loadMessages(true);
        } catch (err) {
            setStatus(false, err.message);
        }
    }

    async function openDM(username, displayName) {
        state.conv = { kind: "dm", username, displayName };
        setHeader("dm", displayName, username);
        resetConversationView();
        markActiveUser();
        closeSidebar();
        try {
            await loadMessages(true);
        } catch (err) {
            setStatus(false, err.message);
        }
    }

    async function loadUsers() {
        try {
            const data = await api("/api/users");
            renderUsers(data.users);
        } catch (err) {
            setStatus(false, err.message);
        }
    }

    function renderUsers(users) {
        els.onlineCount.textContent = String(users.filter((u) => u.online).length);
        els.userList.innerHTML = "";

        users
            .filter((u) => !u.is_me)
            .forEach((user) => {
                const row = document.createElement("button");
                row.type = "button";
                row.className = "user-row";
                row.dataset.username = user.username;
                row.title = `Nhắn tin cho ${user.display_name}`;

                const avatar = document.createElement("div");
                avatar.className = "avatar";
                avatar.style.background = colorFor(user.username);
                avatar.textContent = initialOf(user.display_name);
                if (user.online) {
                    const dot = document.createElement("span");
                    dot.className = "online-dot";
                    avatar.appendChild(dot);
                }

                const meta = document.createElement("div");
                meta.className = "user-meta";
                const name = document.createElement("strong");
                name.textContent = user.display_name;
                const handle = document.createElement("span");
                handle.textContent = "@" + user.username + (user.online ? " · đang online" : "");
                meta.append(name, handle);

                row.append(avatar, meta);
                row.addEventListener("click", () => openDM(user.username, user.display_name));
                els.userList.appendChild(row);
            });

        markActiveUser();
    }

    async function sendMessage(event) {
        event.preventDefault();
        if (state.sending) return;

        const body = els.input.value.trim();
        if (!body) return;

        state.sending = true;
        els.send.disabled = true;

        try {
            const path =
                state.conv.kind === "room"
                    ? "/api/chat"
                    : `/api/dm/${encodeURIComponent(state.conv.username)}`;

            const data = await api(path, {
                method: "POST",
                body: JSON.stringify({ body }),
            });

            if (!state.seen.has(data.message.id)) {
                state.seen.add(data.message.id);
                state.lastId = Math.max(state.lastId, data.message.id);
                appendMessage(data.message, { forceScroll: true });
            }

            els.input.value = "";
            autosize();
            setStatus(true);
        } catch (err) {
            setStatus(false, err.message);
        } finally {
            state.sending = false;
            els.send.disabled = false;
            els.input.focus();
        }
    }

    // ---------------- UI events ----------------

    function autosize() {
        els.input.style.height = "auto";
        els.input.style.height = Math.min(els.input.scrollHeight, 160) + "px";
    }

    function openSidebar() {
        els.sidebar.classList.add("open");
    }

    function closeSidebar() {
        els.sidebar.classList.remove("open");
    }

    function bind() {
        els.composer.addEventListener("submit", sendMessage);

        els.input.addEventListener("input", autosize);

        els.input.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;

            // Tắt "Enter để gửi" => Enter xuống dòng, Ctrl/Cmd+Enter mới gửi
            if (event.shiftKey && enterToSend) return;

            const withModifier = event.ctrlKey || event.metaKey;
            if (enterToSend || withModifier) {
                event.preventDefault();
                els.composer.requestSubmit();
            }
        });

        els.roomBtn.addEventListener("click", openRoom);
        els.openSidebar.addEventListener("click", openSidebar);
        els.closeSidebar.addEventListener("click", closeSidebar);

        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") poll();
        });
    }

    // ---------------- Init ----------------

    let polling = false;

    async function poll() {
        if (polling || document.visibilityState !== "visible") return;
        polling = true;
        try {
            await loadMessages(false);
        } catch (err) {
            setStatus(false, err.message);
        } finally {
            polling = false;
        }
    }

    function initMe() {
        els.meAvatar.style.background = colorFor(me.username);
        els.meAvatar.textContent = initialOf(me.display_name);
        els.meName.textContent = me.display_name;
        els.meHandle.textContent = "@" + me.username; // me.username is safe text
    }

    async function boot() {
        initMe();
        bind();
        autosize();

        els.input.placeholder = enterToSend
            ? "Nhập tin nhắn… (Enter để gửi, Shift+Enter xuống dòng)"
            : "Nhập tin nhắn… (Ctrl+Enter để gửi, Enter xuống dòng)";

        setHeader("room");
        markActiveUser();

        try {
            await loadMessages(true);
        } catch (err) {
            setStatus(false, err.message);
        }

        await loadUsers();
        setInterval(poll, 2000);
        setInterval(loadUsers, 10000);
        setInterval(() => {
            if (document.visibilityState === "visible") {
                api("/api/heartbeat", { method: "POST" }).catch(() => {});
            }
        }, 25000);
    }

    boot();
})();
