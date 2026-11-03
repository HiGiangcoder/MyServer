(() => {
    "use strict";

    const { me, csrf } = window.APP;
    const settings = window.APP.settings || {};
    const appConfig = window.APP.config || {};
    const maxFileMb = Number(appConfig.max_file_mb) || 100;
    const maxTotalBytes = 2 * 1024 * 1024 * 1024; // khớp MAX_CONTENT_LENGTH
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
        meBadge: document.getElementById("me-badge"),
        emojiBtn: document.getElementById("emoji-btn"),
        emojiPicker: document.getElementById("emoji-picker"),
        fileBtn: document.getElementById("file-btn"),
        folderBtn: document.getElementById("folder-btn"),
        fileInput: document.getElementById("file-input"),
        folderInput: document.getElementById("folder-input"),
        uploadHint: document.getElementById("upload-hint"),
        selectedFiles: document.getElementById("selected-files"),
        progress: document.getElementById("progress"),
        progressBar: document.getElementById("progress-bar"),
    };

    const state = {
        conv: { kind: "room", username: null, displayName: null },
        lastId: 0,
        seen: new Set(),
        sending: false,
        uploading: false,
        files: [],
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

    function formatSize(bytes) {
        const n = Number(bytes) || 0;
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
        return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    }

    function fileIcon(name) {
        const ext = String(name).split(".").pop().toLowerCase();
        if (["zip", "rar", "7z", "tar", "gz", "bz2"].includes(ext)) return "🗜️";
        if (["pdf"].includes(ext)) return "📕";
        if (["doc", "docx", "odt"].includes(ext)) return "📘";
        if (["xls", "xlsx", "csv", "ods"].includes(ext)) return "📗";
        if (["ppt", "pptx"].includes(ext)) return "📙";
        if (["mp4", "mkv", "mov", "avi", "webm"].includes(ext)) return "🎬";
        if (["mp3", "wav", "flac", "ogg", "m4a"].includes(ext)) return "🎵";
        if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext)) return "🖼️";
        return "📄";
    }

    function humanLeft(iso) {
        const ms = new Date(iso).getTime() - Date.now();
        if (!Number.isFinite(ms)) return "";
        if (ms <= 0) return "Đã hết hạn";

        const total = Math.floor(ms / 1000);
        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const mins = Math.floor((total % 3600) / 60);

        if (days > 0) return `Tự xóa sau ${days} ngày ${hours} giờ`;
        if (hours > 0) return `Tự xóa sau ${hours} giờ ${mins} phút`;
        if (mins > 0) return `Tự xóa sau ${mins} phút`;
        return `Tự xóa sau ${total} giây`;
    }

    function refreshExpiries(root = document) {
        root.querySelectorAll("[data-expires]").forEach((el) => {
            const text = humanLeft(el.dataset.expires);
            if (text) {
                el.textContent = text;
                el.classList.toggle("expired", text === "Đã hết hạn");
            }
        });
    }

    const EMOJIS = [
        "😀", "😂", "🤣", "😊", "😍", "😘", "😎", "🤩",
        "🥳", "😭", "😅", "🙃", "😴", "🤔", "🤯", "😱",
        "😡", "🥺", "😇", "🤗", "👍", "👎", "👏", "🙌",
        "🙏", "💪", "🤝", "✌️", "🤞", "👌", "❤️", "🧡",
        "💛", "💚", "💙", "💜", "🖤", "💔", "💯", "🔥",
        "✨", "🎉", "🎁", "☕", "🍕", "🍺", "⚡", "🚀",
    ];

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

    function buildAttachments(list) {
        const box = document.createElement("div");
        box.className = "attachments";

        list.forEach((att) => {
            if (att.is_image) {
                const wrapper = document.createElement("div");
                wrapper.className = "attachment-image";

                const link = document.createElement("a");
                link.href = att.url;
                link.target = "_blank";
                link.rel = "noopener";

                const img = document.createElement("img");
                img.src = att.url;
                img.alt = att.name;
                img.loading = "lazy";
                img.title = att.name;

                link.appendChild(img);
                wrapper.appendChild(link);

                const expiry = document.createElement("div");
                expiry.className = "attachment-expiry";
                expiry.dataset.expires = att.expires_at;
                expiry.textContent = humanLeft(att.expires_at);
                wrapper.appendChild(expiry);

                box.appendChild(wrapper);
                return;
            }

            const link = document.createElement("a");
            link.className = "attachment-file";
            link.href = att.url;
            link.target = "_blank";
            link.rel = "noopener";
            link.download = att.name.split("/").pop();

            const icon = document.createElement("span");
            icon.className = "attachment-icon";
            icon.textContent = fileIcon(att.name);

            const meta = document.createElement("div");
            meta.className = "attachment-meta";

            const name = document.createElement("span");
            name.className = "attachment-name";
            name.textContent = att.name;
            name.title = att.name;

            const sub = document.createElement("span");
            sub.className = "attachment-sub";
            sub.textContent = formatSize(att.size);

            meta.append(name, sub);
            link.append(icon, meta);

            const expiry = document.createElement("div");
            expiry.className = "attachment-expiry";
            expiry.dataset.expires = att.expires_at;
            expiry.textContent = humanLeft(att.expires_at);

            box.append(link, expiry);
        });

        return box;
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

        if (msg.body) {
            const bubble = document.createElement("div");
            bubble.className = "bubble";
            bubble.textContent = msg.body; // textContent => chống XSS
            wrap.appendChild(bubble);
        }

        if (Array.isArray(msg.attachments) && msg.attachments.length) {
            wrap.appendChild(buildAttachments(msg.attachments));
        }

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

    function clearFiles() {
        state.files = [];
        els.fileInput.value = "";
        els.folderInput.value = "";
        renderFiles();
        els.uploadHint.textContent = "";
    }

    function renderFiles() {
        els.selectedFiles.innerHTML = "";

        if (state.files.length === 0) {
            els.selectedFiles.hidden = true;
            return;
        }

        els.selectedFiles.hidden = false;
        let total = 0;

        state.files.forEach((file, index) => {
            total += file.size;

            const chip = document.createElement("div");
            chip.className = "file-chip";

            const icon = document.createElement("span");
            icon.textContent = fileIcon(file.name);

            const name = document.createElement("span");
            name.className = "chip-name";
            name.textContent = file.webkitRelativePath || file.name;
            name.title = name.textContent;

            const size = document.createElement("span");
            size.className = "chip-size";
            size.textContent = formatSize(file.size);

            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "✕";
            remove.title = "Bỏ chọn";
            remove.addEventListener("click", () => {
                state.files.splice(index, 1);
                renderFiles();
                updateUploadHint();
            });

            chip.append(icon, name, size, remove);
            els.selectedFiles.appendChild(chip);
        });

        updateUploadHint();
    }

    function updateUploadHint() {
        if (!state.files.length) {
            els.uploadHint.textContent = "";
            return;
        }
        const total = state.files.reduce((sum, file) => sum + file.size, 0);
        els.uploadHint.textContent = `${state.files.length} tệp • ${formatSize(total)}`;
    }

    function addFiles(fileList) {
        const incoming = Array.from(fileList || []);
        const tooBig = [];
        let overTotal = false;
        let totalBytes = state.files.reduce((sum, file) => sum + file.size, 0);

        for (const file of incoming) {
            if (file.size > maxFileMb * 1024 * 1024) {
                tooBig.push(file.name);
                continue;
            }

            const key = `${file.webkitRelativePath || file.name}|${file.size}`;
            const exists = state.files.some(
                (f) => `${f.webkitRelativePath || f.name}|${f.size}` === key
            );
            if (exists) continue;

            if (totalBytes + file.size > maxTotalBytes) {
                overTotal = true;
                break;
            }

            if (state.files.length >= 50) break;
            state.files.push(file);
            totalBytes += file.size;
        }

        renderFiles();

        if (tooBig.length) {
            els.uploadHint.textContent =
                `Bỏ qua ${tooBig.length} tệp vượt ${maxFileMb} MB`;
        } else if (overTotal) {
            els.uploadHint.textContent =
                "Tổng dung lượng vượt 2 GB, đã bỏ qua phần còn lại";
        }
    }

    function uploadFiles(body) {
        if (state.uploading) return;

        state.uploading = true;
        state.sending = true;
        els.send.disabled = true;
        els.progress.hidden = false;
        els.progressBar.style.width = "0%";

        const form = new FormData();
        form.append("target", state.conv.kind === "room" ? "room" : "dm");
        if (state.conv.kind === "dm") {
            form.append("username", state.conv.username);
        }
        if (body) form.append("body", body);
        form.append("csrf_token", csrf);

        state.files.forEach((file) => {
            form.append("files", file, file.webkitRelativePath || file.name);
        });

        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/upload");
        xhr.setRequestHeader("X-CSRF-Token", csrf);
        xhr.responseType = "json";

        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            els.progressBar.style.width = percent + "%";
            els.uploadHint.textContent = `Đang gửi ${percent}%`;
        };

        xhr.onload = () => {
            if (xhr.status === 401) {
                window.location.href = "/login";
                return;
            }

            const data = xhr.response || {};
            if (xhr.status >= 200 && xhr.status < 300 && data.message) {
                const msg = data.message;
                if (!state.seen.has(msg.id)) {
                    state.seen.add(msg.id);
                    state.lastId = Math.max(state.lastId, msg.id);
                    appendMessage(msg, { forceScroll: true });
                }
                els.input.value = "";
                autosize();
                clearFiles();
                setStatus(true);
                refreshExpiries();
            } else {
                const message =
                    (data && data.error) || `Gửi tệp thất bại (${xhr.status})`;
                setStatus(false, message);
                els.uploadHint.textContent = message;
            }
        };

        xhr.onerror = () => {
            setStatus(false, "Lỗi mạng khi gửi tệp");
            els.uploadHint.textContent = "Lỗi mạng khi gửi tệp";
        };

        xhr.onloadend = () => {
            state.uploading = false;
            state.sending = false;
            els.send.disabled = false;
            els.progress.hidden = true;
            els.progressBar.style.width = "0%";
            els.input.focus();
        };

        xhr.send(form);
    }

    async function sendMessage(event) {
        event.preventDefault();
        if (state.sending || state.uploading) return;

        const body = els.input.value.trim();
        if (!body && state.files.length === 0) return;

        if (state.files.length > 0) {
            uploadFiles(body);
            return;
        }

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

    function buildEmojiPicker() {
        if (els.emojiPicker.childElementCount > 0) return;
        EMOJIS.forEach((emoji) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = emoji;
            els.emojiPicker.appendChild(button);
        });
    }

    function insertEmoji(emoji) {
        const start = els.input.selectionStart ?? els.input.value.length;
        const end = els.input.selectionEnd ?? els.input.value.length;
        els.input.value =
            els.input.value.slice(0, start) + emoji + els.input.value.slice(end);
        const position = start + emoji.length;
        els.input.focus();
        els.input.setSelectionRange(position, position);
        autosize();
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

        els.fileBtn.addEventListener("click", () => els.fileInput.click());
        els.folderBtn.addEventListener("click", () => els.folderInput.click());

        els.fileInput.addEventListener("change", (event) => {
            addFiles(event.target.files);
            event.target.value = "";
        });

        els.folderInput.addEventListener("change", (event) => {
            addFiles(event.target.files);
            event.target.value = "";
        });

        els.emojiBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            buildEmojiPicker();
            els.emojiPicker.hidden = !els.emojiPicker.hidden;
        });

        els.emojiPicker.addEventListener("click", (event) => {
            const button = event.target.closest("button");
            if (button) insertEmoji(button.textContent);
        });

        document.addEventListener("click", (event) => {
            if (els.emojiPicker.hidden) return;
            if (
                els.emojiPicker.contains(event.target) ||
                els.emojiBtn.contains(event.target)
            ) {
                return;
            }
            els.emojiPicker.hidden = true;
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
        if (me.is_admin) els.meBadge.hidden = false;
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
        refreshExpiries();
        setInterval(poll, 2000);
        setInterval(loadUsers, 10000);
        setInterval(() => refreshExpiries(), 30000);
        setInterval(() => {
            if (document.visibilityState === "visible") {
                api("/api/heartbeat", { method: "POST" }).catch(() => {});
            }
        }, 25000);
    }

    boot();
})();
