/**
 * 課堂形成性評量隨手教具：
 * - 思考計時器 (T)
 * - 班級隨機抽號器 (R)
 * - 板書畫筆標記 (B)
 * - 圖片放大燈箱
 * - 投影字級縮放與全螢幕 (F)
 * - 快捷鍵一覽 (?)
 */
window.CLATools = (function () {
  const STORAGE_SCALE_KEY = "cla_stage_scale";
  const STORAGE_ROSTER_KEY = "cla_roster_size";

  let activePopover = null; // 'timer' | 'picker' | null
  let timerSeconds = 60;
  let timerRemaining = 60;
  let timerRunning = false;
  let timerInterval = null;
  let timerMuted = false;

  let rosterSize = Number(localStorage.getItem(STORAGE_ROSTER_KEY)) || 28;
  let drawnNumbers = [];
  let lastDrawn = null;

  // 畫筆狀態
  let drawEnabled = false;
  let drawColor = "#B33A2E";
  let drawLineWidth = 3.5;
  let isDrawingStroke = false;
  let canvasEl = null;
  let ctx = null;
  let drawDockEl = null;

  function playGentleChime() {
    if (timerMuted) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ac = new AudioCtx();
      const now = ac.currentTime;
      [523.25, 659.25, 783.99].forEach((freq, idx) => {
        const osc = ac.createOscillator();
        const gain = ac.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.001, now + idx * 0.14);
        gain.gain.exponentialRampToValueAtTime(0.14, now + idx * 0.14 + 0.04);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.14 + 0.55);
        osc.connect(gain);
        gain.connect(ac.destination);
        osc.start(now + idx * 0.14);
        osc.stop(now + idx * 0.14 + 0.6);
      });
    } catch (_) {
      // 忽略自動播放權限受限
    }
  }

  function formatMMSS(sec) {
    const s = Math.max(0, Math.floor(sec));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
  }

  /* --------------------------------------------------------------------------
     字級縮放 (標準 / 大 / 特大)
     -------------------------------------------------------------------------- */
  function applyScale(scale) {
    const valid = ["md", "lg", "xl"].includes(scale) ? scale : "md";
    if (valid === "md") {
      document.body.removeAttribute("data-scale");
    } else {
      document.body.setAttribute("data-scale", valid);
    }
    localStorage.setItem(STORAGE_SCALE_KEY, valid);
    document.querySelectorAll("[data-scale-choice]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.getAttribute("data-scale-choice") === valid);
    });
  }

  function initScale() {
    const saved = localStorage.getItem(STORAGE_SCALE_KEY) || "md";
    applyScale(saved);
    document.querySelectorAll("[data-scale-choice]").forEach((btn) => {
      btn.addEventListener("click", () => {
        applyScale(btn.getAttribute("data-scale-choice"));
      });
    });
  }

  /* --------------------------------------------------------------------------
     計時器與抽號器 Popover UI
     -------------------------------------------------------------------------- */
  function updateTimerUI() {
    const badge = document.getElementById("headerTimerBadge");
    const display = document.getElementById("timerDisplay");
    const toggleBtn = document.getElementById("timerStartBtn");

    if (display) display.textContent = formatMMSS(timerRemaining);
    if (toggleBtn) toggleBtn.textContent = timerRunning ? "暫停" : "開始倒數";

    if (badge) {
      const showBadge = timerRunning || timerRemaining !== timerSeconds;
      badge.hidden = !showBadge;
      badge.textContent = "⏱ " + formatMMSS(timerRemaining);
      badge.classList.toggle("is-done", timerRemaining === 0 && !timerRunning);
    }
  }

  function stopTimerInterval() {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
    timerRunning = false;
  }

  function setTimerPreset(sec) {
    stopTimerInterval();
    timerSeconds = sec;
    timerRemaining = sec;
    updateTimerUI();
  }

  function toggleTimerRun() {
    if (timerRunning) {
      stopTimerInterval();
      updateTimerUI();
      return;
    }
    if (timerRemaining <= 0) {
      timerRemaining = timerSeconds;
    }
    timerRunning = true;
    updateTimerUI();
    timerInterval = setInterval(() => {
      timerRemaining -= 1;
      if (timerRemaining <= 0) {
        timerRemaining = 0;
        stopTimerInterval();
        playGentleChime();
      }
      updateTimerUI();
    }, 1000);
  }

  function updatePickerUI() {
    const numEl = document.getElementById("pickerNum");
    const subEl = document.getElementById("pickerSub");
    const histEl = document.getElementById("pickerHistory");
    const inputEl = document.getElementById("pickerRosterInput");

    if (inputEl && Number(inputEl.value) !== rosterSize) {
      inputEl.value = String(rosterSize);
    }
    if (numEl) {
      numEl.textContent = lastDrawn !== null ? String(lastDrawn) + " 號" : "--";
    }
    if (subEl) {
      const remain = Math.max(0, rosterSize - drawnNumbers.length);
      subEl.textContent = `已抽 ${drawnNumbers.length} 位・尚餘 ${remain} 位`;
    }
    if (histEl) {
      histEl.textContent = drawnNumbers.length
        ? "已抽座號：" + drawnNumbers.join("、")
        : "尚未抽出座號，點擊下方按鈕開始抽籤。";
    }
  }

  function drawNextStudent() {
    const available = [];
    for (let i = 1; i <= rosterSize; i++) {
      if (!drawnNumbers.includes(i)) available.push(i);
    }
    if (!available.length) {
      drawnNumbers = [];
      for (let i = 1; i <= rosterSize; i++) available.push(i);
    }
    const pick = available[Math.floor(Math.random() * available.length)];
    drawnNumbers.push(pick);
    lastDrawn = pick;
    updatePickerUI();
  }

  function closePopovers() {
    activePopover = null;
    const tPop = document.getElementById("timerPopover");
    const pPop = document.getElementById("pickerPopover");
    const tBtn = document.getElementById("btnTimerTool");
    const pBtn = document.getElementById("btnPickerTool");
    if (tPop) tPop.hidden = true;
    if (pPop) pPop.hidden = true;
    if (tBtn) tBtn.classList.remove("is-active");
    if (pBtn) pBtn.classList.remove("is-active");
  }

  function togglePopover(name) {
    if (activePopover === name) {
      closePopovers();
      return;
    }
    closePopovers();
    activePopover = name;
    const pop = document.getElementById(name === "timer" ? "timerPopover" : "pickerPopover");
    const btn = document.getElementById(name === "timer" ? "btnTimerTool" : "btnPickerTool");
    if (pop) pop.hidden = false;
    if (btn) btn.classList.add("is-active");
  }

  /* --------------------------------------------------------------------------
     板書畫筆圖層 (Pointer Events)
     -------------------------------------------------------------------------- */
  function resizeCanvas() {
    if (!canvasEl) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = window.innerWidth;
    const h = window.innerHeight;
    canvasEl.width = w * dpr;
    canvasEl.height = h * dpr;
    canvasEl.style.width = w + "px";
    canvasEl.style.height = h + "px";
    ctx = canvasEl.getContext("2d");
    if (ctx) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
    }
  }

  function clearBoardCanvas() {
    if (ctx && canvasEl) {
      ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
    }
  }

  function setDrawEnabled(enabled) {
    drawEnabled = Boolean(enabled);
    document.body.classList.toggle("is-drawing", drawEnabled);
    const btn = document.getElementById("btnDrawTool");
    if (btn) btn.classList.toggle("is-active", drawEnabled);
    if (drawDockEl) drawDockEl.hidden = !drawEnabled;
  }

  function initDrawBoard() {
    canvasEl = document.getElementById("drawCanvas");
    drawDockEl = document.getElementById("drawDock");
    if (!canvasEl) return;

    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

    canvasEl.addEventListener("pointerdown", (e) => {
      if (!drawEnabled || !ctx) return;
      isDrawingStroke = true;
      canvasEl.setPointerCapture(e.pointerId);
      ctx.beginPath();
      ctx.strokeStyle = drawColor;
      ctx.lineWidth = drawLineWidth;
      ctx.globalAlpha = drawColor === "rgba(250, 204, 21, 0.45)" ? 0.55 : 1;
      ctx.moveTo(e.clientX, e.clientY);
    });

    canvasEl.addEventListener("pointermove", (e) => {
      if (!isDrawingStroke || !drawEnabled || !ctx) return;
      ctx.lineTo(e.clientX, e.clientY);
      ctx.stroke();
    });

    const endStroke = () => {
      isDrawingStroke = false;
      if (ctx) ctx.closePath();
    };
    canvasEl.addEventListener("pointerup", endStroke);
    canvasEl.addEventListener("pointercancel", endStroke);

    document.querySelectorAll("[data-pen-color]").forEach((btn) => {
      btn.addEventListener("click", () => {
        drawColor = btn.getAttribute("data-pen-color");
        drawLineWidth = Number(btn.getAttribute("data-pen-width") || "3.5");
        document.querySelectorAll("[data-pen-color]").forEach((b) => {
          b.classList.toggle("is-active", b === btn);
        });
      });
    });

    const clearBtn = document.getElementById("btnClearBoard");
    if (clearBtn) clearBtn.addEventListener("click", clearBoardCanvas);

    const closeDrawBtn = document.getElementById("btnCloseDraw");
    if (closeDrawBtn) closeDrawBtn.addEventListener("click", () => setDrawEnabled(false));
  }

  /* --------------------------------------------------------------------------
     圖片放大燈箱 & 快捷鍵彈窗
     -------------------------------------------------------------------------- */
  let currentZoomPct = 100;

  function openLightbox(src, caption) {
    const modal = document.getElementById("lightboxModal");
    const img = document.getElementById("lightboxImg");
    const cap = document.getElementById("lightboxCaption");
    if (!modal || !img) return;
    currentZoomPct = 100;
    img.src = src;
    img.alt = caption || "試題配圖";
    img.style.width = "100%";
    if (cap) cap.textContent = caption || "試題配圖";
    modal.hidden = false;
    updateZoomButtons();
  }

  function closeLightbox() {
    const modal = document.getElementById("lightboxModal");
    if (modal) modal.hidden = true;
  }

  function updateZoomButtons() {
    const img = document.getElementById("lightboxImg");
    if (img) {
      img.style.width = currentZoomPct === 100 ? "100%" : currentZoomPct + "%";
      img.style.maxWidth = currentZoomPct === 100 ? "100%" : "none";
    }
    document.querySelectorAll("[data-lightbox-zoom]").forEach((b) => {
      b.classList.toggle("is-active", Number(b.getAttribute("data-lightbox-zoom")) === currentZoomPct);
    });
  }

  function toggleShortcutModal(force) {
    const modal = document.getElementById("shortcutModal");
    if (!modal) return;
    const next = typeof force === "boolean" ? force : modal.hidden;
    modal.hidden = !next;
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  function init() {
    initScale();
    initDrawBoard();

    const btnTimer = document.getElementById("btnTimerTool");
    const btnPicker = document.getElementById("btnPickerTool");
    const btnDraw = document.getElementById("btnDrawTool");
    const btnFs = document.getElementById("btnFullscreen");
    const btnHelp = document.getElementById("btnShortcutHelp");

    if (btnTimer) btnTimer.addEventListener("click", (e) => { e.stopPropagation(); togglePopover("timer"); });
    if (btnPicker) btnPicker.addEventListener("click", (e) => { e.stopPropagation(); togglePopover("picker"); });
    if (btnDraw) btnDraw.addEventListener("click", () => setDrawEnabled(!drawEnabled));
    if (btnFs) btnFs.addEventListener("click", toggleFullscreen);
    if (btnHelp) btnHelp.addEventListener("click", () => toggleShortcutModal(true));

    document.querySelectorAll("[data-close-popover]").forEach((b) => {
      b.addEventListener("click", closePopovers);
    });

    // 計時器按鈕
    document.querySelectorAll("[data-timer-preset]").forEach((b) => {
      b.addEventListener("click", () => setTimerPreset(Number(b.getAttribute("data-timer-preset"))));
    });
    const tStart = document.getElementById("timerStartBtn");
    const tReset = document.getElementById("timerResetBtn");
    const tMute = document.getElementById("timerMuteBtn");
    if (tStart) tStart.addEventListener("click", toggleTimerRun);
    if (tReset) tReset.addEventListener("click", () => setTimerPreset(timerSeconds));
    if (tMute) {
      tMute.addEventListener("click", () => {
        timerMuted = !timerMuted;
        tMute.textContent = timerMuted ? "靜音" : "鈴聲";
      });
    }

    // 抽號器按鈕
    const pDraw = document.getElementById("pickerDrawBtn");
    const pReset = document.getElementById("pickerResetBtn");
    const pInput = document.getElementById("pickerRosterInput");
    if (pDraw) pDraw.addEventListener("click", drawNextStudent);
    if (pReset) {
      pReset.addEventListener("click", () => {
        drawnNumbers = [];
        lastDrawn = null;
        updatePickerUI();
      });
    }
    if (pInput) {
      pInput.addEventListener("change", () => {
        const val = Math.min(60, Math.max(2, Number(pInput.value) || 28));
        rosterSize = val;
        localStorage.setItem(STORAGE_ROSTER_KEY, String(val));
        drawnNumbers = drawnNumbers.filter((n) => n <= rosterSize);
        if (lastDrawn && lastDrawn > rosterSize) lastDrawn = null;
        updatePickerUI();
      });
    }

    // 燈箱事件
    const lbModal = document.getElementById("lightboxModal");
    const lbClose = document.getElementById("lightboxCloseBtn");
    if (lbClose) lbClose.addEventListener("click", closeLightbox);
    if (lbModal) {
      lbModal.addEventListener("click", (e) => {
        if (e.target === lbModal) closeLightbox();
      });
    }
    document.querySelectorAll("[data-lightbox-zoom]").forEach((b) => {
      b.addEventListener("click", () => {
        currentZoomPct = Number(b.getAttribute("data-lightbox-zoom"));
        updateZoomButtons();
      });
    });

    // 快捷鍵說明彈窗
    const scModal = document.getElementById("shortcutModal");
    const scClose = document.getElementById("shortcutCloseBtn");
    if (scClose) scClose.addEventListener("click", () => toggleShortcutModal(false));
    if (scModal) {
      scModal.addEventListener("click", (e) => {
        if (e.target === scModal) toggleShortcutModal(false);
      });
    }

    // 點擊空白處關閉 Popover
    document.addEventListener("click", (e) => {
      if (!activePopover) return;
      const anchor = e.target.closest(".tool-anchor");
      if (!anchor) closePopovers();
    });

    updateTimerUI();
    updatePickerUI();
  }

  return {
    init: init,
    togglePopover: togglePopover,
    closePopovers: closePopovers,
    setDrawEnabled: setDrawEnabled,
    isDrawEnabled: function () { return drawEnabled; },
    clearBoardCanvas: clearBoardCanvas,
    openLightbox: openLightbox,
    closeLightbox: closeLightbox,
    toggleShortcutModal: toggleShortcutModal,
    toggleFullscreen: toggleFullscreen,
  };
})();
