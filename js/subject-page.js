/**
 * 科目形成性評量核心互動引擎（講台逐題模式 + 試卷總覽模式）
 * 支援國語、數學、社會三科，自動讀取 data/{subject}.json
 */
(function () {
  const SUBJECT = document.body.dataset.subject || "chinese";
  const fmt = window.CLAFormat;
  const tools = window.CLATools;

  const TYPE_LABELS = {
    true_false: "是非題",
    choice: "選擇題",
    applied: "應用與探究題",
    calculation: "計算題",
    fill_blank: "填充題",
  };

  let subjectData = null;
  let currentWeekIdx = 0;
  let currentQIdx = 0;
  let currentMode = "podium"; // 'podium' | 'overview'

  // 紀錄每題課堂狀態 { [qId]: { selected: string|null, eliminated: Set<string>, revealStage: 0|1|2, revealedSteps: number } }
  const qStates = new Map();
  // 總覽模式個別展開紀錄
  const overviewOpenAnswers = new Set();

  function getQState(qId) {
    if (!qStates.has(qId)) {
      qStates.set(qId, {
        selected: null,
        eliminated: new Set(),
        revealStage: 0,
        revealedSteps: 1,
      });
    }
    return qStates.get(qId);
  }

  function currentWeek() {
    if (!subjectData || !subjectData.weeks || !subjectData.weeks.length) return null;
    return subjectData.weeks[currentWeekIdx] || subjectData.weeks[0];
  }

  function currentQuestion() {
    const wk = currentWeek();
    if (!wk || !wk.questions || !wk.questions.length) return null;
    return wk.questions[currentQIdx] || wk.questions[0];
  }

  function findContext(wk, contextId) {
    if (!wk || !contextId || !wk.contexts) return null;
    return wk.contexts.find((c) => c.id === contextId) || null;
  }

  function syncUrlState() {
    const wk = currentWeek();
    const q = currentQuestion();
    if (!wk) return;
    const params = new URLSearchParams();
    params.set("week", String(wk.weekNumber));
    if (q) params.set("q", String(q.displayNumber || q.number));
    if (currentMode === "overview") params.set("mode", "overview");
    const newUrl = window.location.pathname + "?" + params.toString();
    window.history.replaceState(null, "", newUrl);
  }

  /* --------------------------------------------------------------------------
     圖片／檔案圖表板 HTML 建構
     -------------------------------------------------------------------------- */
  function renderFigureHtml(fig) {
    const caption = fmt.escapeHtml(fig.caption || "試題配圖");
    const noteHtml = fig.sourceNote
      ? '<div class="figure-caption-bar">' + fmt.escapeHtml(fig.sourceNote) + "</div>"
      : "";

    if (fig.src) {
      const safeSrc = fmt.escapeHtml(fig.src);
      return (
        '<div class="figure-block">' +
        '  <div class="figure-frame">' +
        '    <img class="figure-img" src="' + safeSrc + '" alt="' + caption + '" loading="lazy" data-zoom-src="' + safeSrc + '" data-zoom-caption="' + caption + '">' +
        '    <button type="button" class="figure-zoom-btn" data-zoom-src="' + safeSrc + '" data-zoom-caption="' + caption + '">🔍 放大檢視</button>' +
        "  </div>" +
        (fig.caption ? '<div class="figure-caption-bar"><strong>' + caption + "</strong></div>" : "") +
        noteHtml +
        "</div>"
      );
    }

    const driveBtn = fig.driveUrl
      ? '<a class="figure-drive-btn" href="' + fmt.escapeHtml(fig.driveUrl) + '" target="_blank" rel="noopener noreferrer">開啟雲端原圖 ↗</a>'
      : "";

    return (
      '<div class="figure-block">' +
      '  <div class="figure-plaque">' +
      '    <div class="figure-plaque-top">' +
      '      <div class="figure-plaque-title">🖼️ <span>' + caption + "</span></div>" +
      driveBtn +
      "    </div>" +
      (fig.sourceNote ? '<div class="figure-plaque-note">' + fmt.escapeHtml(fig.sourceNote) + "</div>" : "") +
      '    <div class="figure-plaque-hint">提示：雲端原圖需以有權限之帳號開啟；若將圖檔放入 <code>sources/' +
      fmt.escapeHtml(SUBJECT) +
      '/images/</code> 並執行更新，即可直接於講台畫面顯示。</div>' +
      "  </div>" +
      "</div>"
    );
  }

  /* --------------------------------------------------------------------------
     週次導覽列與 Metadata
     -------------------------------------------------------------------------- */
  function renderWeekHeader(wk) {
    const weekTabsEl = document.getElementById("weekTabs");
    if (weekTabsEl && subjectData.weeks) {
      weekTabsEl.innerHTML = subjectData.weeks
        .map((w, idx) => {
          const active = idx === currentWeekIdx ? " is-active" : "";
          return (
            '<button type="button" class="week-tab-btn' + active + '" data-week-idx="' + idx + '">' +
            fmt.escapeHtml(w.weekLabel) +
            ' <span class="week-tab-count">' + w.questions.length + "題</span></button>"
          );
        })
        .join("");

      weekTabsEl.querySelectorAll("[data-week-idx]").forEach((btn) => {
        btn.addEventListener("click", () => {
          currentWeekIdx = Number(btn.getAttribute("data-week-idx"));
          currentQIdx = 0;
          tools.clearBoardCanvas();
          renderAll();
        });
      });
    }

    const metaEl = document.getElementById("weekMetaStrip");
    if (metaEl && wk) {
      const sched = wk.schedule ? "<div><strong>教學進度：</strong>" + fmt.escapeHtml(wk.schedule) + "</div>" : "";
      const goals = wk.goals ? "<div><strong>學習重點：</strong>" + fmt.escapeHtml(wk.goals) + "</div>" : "";
      metaEl.innerHTML =
        "<div>" +
        '  <h1 class="week-title-main">' + fmt.escapeHtml(wk.weekLabel + "：" + wk.title) + "</h1>" +
        '  <div class="week-submeta">' + sched + goals + "</div>" +
        "</div>" +
        '<div class="week-actions-right no-print">' +
        '  <button type="button" class="tool-btn" id="btnResetWeekState" title="清除本週課堂標記與已揭曉答案">↺ 重置課堂標記</button>' +
        "</div>";

      const resetBtn = document.getElementById("btnResetWeekState");
      if (resetBtn) {
        resetBtn.addEventListener("click", () => {
          wk.questions.forEach((q) => qStates.delete(q.id));
          overviewOpenAnswers.clear();
          tools.clearBoardCanvas();
          renderAll();
        });
      }
    }
  }

  /* --------------------------------------------------------------------------
     講台逐題模式 (Focus Podium)
     -------------------------------------------------------------------------- */
  function renderQuestionRail(wk) {
    const railEl = document.getElementById("questionRail");
    if (!railEl || !wk) return;
    if (currentMode !== "podium") {
      railEl.hidden = true;
      return;
    }
    railEl.hidden = false;

    const groupsHtml = (wk.sections || [])
      .map((sec) => {
        const secQs = wk.questions
          .map((q, idx) => ({ q, idx }))
          .filter((item) => item.q.sectionId === sec.id);
        if (!secQs.length) return "";
        const btns = secQs
          .map(({ q, idx }) => {
            const st = getQState(q.id);
            const cls = [
              "rail-num-btn",
              idx === currentQIdx ? "is-current" : "",
              st.revealStage > 0 ? "is-revealed" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              '<button type="button" class="' + cls + '" data-q-idx="' + idx + '" title="跳至第 ' + fmt.escapeHtml(q.displayNumber || q.number) + ' 題">' +
              fmt.escapeHtml(q.displayNumber || q.number) +
              "</button>"
            );
          })
          .join("");
        return (
          '<div class="rail-section-group">' +
          '  <span class="rail-section-label">' + fmt.escapeHtml(sec.label + "、" + sec.title) + "</span>" +
          btns +
          "</div>"
        );
      })
      .join("");

    railEl.innerHTML = groupsHtml;
    railEl.querySelectorAll("[data-q-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentQIdx = Number(btn.getAttribute("data-q-idx"));
        tools.clearBoardCanvas();
        renderStage();
        syncUrlState();
      });
    });
  }

  function renderPodiumStage(wk, q) {
    const container = document.getElementById("stageContainer");
    if (!container || !q) return;

    const ctxObj = findContext(wk, q.contextId);
    const qFigs = q.figures || [];
    const ctxFigs = ctxObj ? ctxObj.figures || [] : [];
    const hasLeftPanel = Boolean(ctxObj || qFigs.length > 0);
    const st = getQState(q.id);

    // 左欄：情境文章 / 配圖板
    let leftHtml = "";
    if (hasLeftPanel) {
      let inner = "";
      if (ctxObj) {
        inner += '<div class="context-badge">' + fmt.escapeHtml(ctxObj.title.replace(/^[【]|[】]$/g, "")) + "</div>";
        if (ctxObj.intro) {
          inner += '<div class="context-intro">' + fmt.renderRichText(ctxObj.intro) + "</div>";
        }
        if (ctxObj.excerptTitle || ctxObj.excerpt) {
          inner +=
            '<div class="excerpt-box">' +
            (ctxObj.excerptTitle ? '<div class="excerpt-title">' + fmt.escapeHtml(ctxObj.excerptTitle) + "</div>" : "") +
            (ctxObj.excerpt ? '<div class="excerpt-body">' + fmt.renderRichText(ctxObj.excerpt) + "</div>" : "") +
            "</div>";
        }
        ctxFigs.forEach((f) => {
          inner += renderFigureHtml(f);
        });
      }
      if (qFigs.length > 0) {
        if (!ctxObj) {
          inner += '<div class="context-badge">附圖與資料判讀</div>';
        }
        qFigs.forEach((f) => {
          inner += renderFigureHtml(f);
        });
      }
      leftHtml = '<aside class="context-panel" aria-label="題組情境與配圖">' + inner + "</aside>";
    }

    // 右欄（或置中單欄）：題幹、互動選項、答案簾
    const typeLabel = TYPE_LABELS[q.type] || "試題";
    const dispNum = q.displayNumber || String(q.number);
    const numLabel = "第 " + dispNum + " 題";
    const ptsHtml = q.points ? '<span class="q-points">' + q.points + " 分</span>" : "";
    const tagHtml = q.tag ? '<span class="q-tag-pill">' + fmt.escapeHtml(q.tag) + "</span>" : "";

    let bodyHtml =
      '<div class="q-header">' +
      '  <div class="q-header-left">' +
      '    <span class="q-num-badge">' + fmt.escapeHtml(numLabel) + "</span>" +
      '    <span class="q-type-pill">' + fmt.escapeHtml(typeLabel) + "</span>" +
      tagHtml +
      "  </div>" +
      ptsHtml +
      "</div>";

    if (q.material) {
      bodyHtml += '<div class="q-material">' + fmt.renderRichText(q.material) + "</div>";
    }

    bodyHtml += '<div class="q-prompt">' + fmt.renderRichText(q.prompt) + "</div>";

    if (q.subQuestions && q.subQuestions.length) {
      bodyHtml += '<div class="q-subquestions">';
      q.subQuestions.forEach((sq) => {
        bodyHtml +=
          '<div class="q-subq-item">' +
          '  <span class="q-subq-label">' + fmt.escapeHtml(sq.label) + "</span>" +
          "  <div>" + fmt.renderRichText(sq.text) + "</div>" +
          "</div>";
      });
      bodyHtml += "</div>";
    }

    if (q.bulletPrompts && q.bulletPrompts.length) {
      bodyHtml += '<ul class="q-bullet-prompts">';
      q.bulletPrompts.forEach((bp) => {
        bodyHtml += "<li>" + fmt.renderRichText(bp) + "</li>";
      });
      bodyHtml += "</ul>";
    }

    if (q.stances && q.stances.length) {
      bodyHtml += '<div class="stances-grid">';
      q.stances.forEach((sc) => {
        bodyHtml +=
          '<div class="stance-card">' +
          '  <div class="stance-label">' + fmt.escapeHtml(sc.label) + "</div>" +
          '  <div class="stance-text">' + fmt.renderRichText(sc.text) + "</div>" +
          "</div>";
      });
      bodyHtml += "</div>";
      if (q.stancePrompt) {
        bodyHtml += '<div class="stance-prompt">' + fmt.renderRichText(q.stancePrompt) + "</div>";
      }
    }

    // 互動答題元件：單選 / 是非
    if (q.type === "choice" && q.options && q.options.length) {
      bodyHtml += '<div class="options-list" role="group" aria-label="選項">';
      q.options.forEach((opt) => {
        const isSelected = st.selected === opt.key;
        const isElim = st.eliminated.has(opt.key);
        const isCorrect = st.revealStage >= 1 && q.answer === opt.key;
        const rowCls = [
          "option-row",
          isSelected ? "is-selected" : "",
          isElim ? "is-eliminated" : "",
          isCorrect ? "is-correct" : "",
        ]
          .filter(Boolean)
          .join(" ");

        bodyHtml +=
          '<div class="' + rowCls + '">' +
          '  <button type="button" class="option-main-btn" data-select-opt="' + fmt.escapeHtml(opt.key) + '">' +
          '    <span class="option-key">' + fmt.escapeHtml(opt.key) + "</span>" +
          '    <span class="option-text">' + fmt.renderRichText(opt.text) + "</span>" +
          "  </button>" +
          '  <button type="button" class="option-elim-btn" data-elim-opt="' + fmt.escapeHtml(opt.key) + '" aria-pressed="' + (isElim ? "true" : "false") + '" title="刪去法劃除選項">✕ 刪去</button>' +
          "</div>";
      });
      bodyHtml += "</div>";
    } else if (q.type === "true_false") {
      const items = [
        { val: "○", label: "○ 正確" },
        { val: "✕", label: "✕ 錯誤" },
      ];
      bodyHtml += '<div class="tf-grid" role="group" aria-label="是非題選項">';
      items.forEach((it) => {
        const isSelected = st.selected === it.val;
        const isCorrect = st.revealStage >= 1 && q.answer === it.val;
        const isWrongPicked = st.revealStage >= 1 && isSelected && q.answer !== it.val;
        const cls = [
          "tf-btn",
          isSelected ? "is-selected" : "",
          isCorrect ? "is-correct" : "",
          isWrongPicked ? "is-wrong-picked" : "",
        ]
          .filter(Boolean)
          .join(" ");
        bodyHtml +=
          '<button type="button" class="' + cls + '" data-select-tf="' + it.val + '">' +
          fmt.escapeHtml(it.label) +
          "</button>";
      });
      bodyHtml += "</div>";
    }

    // 分層揭曉答案控制列
    const stage1Label = st.revealStage >= 1 ? "收合答案" : "揭曉答案";
    const stage2Label = st.revealStage >= 2 ? "隱藏解析與規準" : "顯示解析與評分規準";

    bodyHtml +=
      '<div class="reveal-controls">' +
      '  <div class="reveal-btn-group">' +
      '    <button type="button" class="reveal-btn reveal-btn--primary" id="btnRevealStage1">' +
      stage1Label +
      " <kbd>Space</kbd></button>" +
      '    <button type="button" class="reveal-btn reveal-btn--secondary" id="btnRevealStage2">' +
      stage2Label +
      " <kbd>E</kbd></button>" +
      "  </div>" +
      "</div>";

    // 答案簾內容
    if (st.revealStage >= 1) {
      let veilInner = "";
      if (q.type === "choice") {
        veilInner +=
          '<div class="answer-headline">' +
          '  <span class="answer-chip">正確答案</span>' +
          '  <span class="answer-short-text">選項 (' + fmt.escapeHtml(q.answer) + ")</span>" +
          "</div>";
      } else if (q.type === "true_false") {
        const tfWord = q.answer === "○" ? "○（正確）" : "✕（錯誤）";
        veilInner +=
          '<div class="answer-headline">' +
          '  <span class="answer-chip">正確答案</span>' +
          '  <span class="answer-short-text">' + fmt.escapeHtml(tfWord) + "</span>" +
          "</div>";
      } else {
        veilInner +=
          '<div class="answer-headline">' +
          '  <span class="answer-chip">參考解答</span>' +
          "</div>" +
          '<div class="answer-short-text">' + fmt.renderRichText(q.answerText) + "</div>";
      }

      // 步驟算式（應用題階段一可逐步演示，階段二自動全部顯示）
      const steps = q.steps || [];
      const showStepsNow = steps.length > 0 && (q.type === "applied" || q.type === "calculation" || st.revealStage >= 2);
      if (showStepsNow) {
        const visibleCnt = st.revealStage >= 2 ? steps.length : Math.min(steps.length, st.revealedSteps);
        const stepItemsHtml = steps
          .slice(0, visibleCnt)
          .map((line) => "<li>" + fmt.renderRichText(line) + "</li>")
          .join("");

        let stepControls = "";
        if (st.revealStage === 1 && visibleCnt < steps.length) {
          stepControls =
            '<div style="display:flex;gap:6px;">' +
            '  <button type="button" class="preset-btn" style="padding:0 10px;" id="btnNextStep">+ 下一步 (' + visibleCnt + "/" + steps.length + ")</button>" +
            '  <button type="button" class="preset-btn" style="padding:0 10px;" id="btnAllSteps">全部步驟</button>' +
            "</div>";
        }

        veilInner +=
          '<div class="steps-box">' +
          '  <div class="steps-header"><span>解題算式與推理步驟</span>' + stepControls + "</div>" +
          '  <ol class="steps-list">' + stepItemsHtml + "</ol>" +
          "</div>";
      }

      // 階段二：詳細解析與評分規準
      if (st.revealStage >= 2) {
        const stepsJoined = steps.join("\n");
        if (q.explanation && q.explanation.trim() !== stepsJoined.trim()) {
          veilInner +=
            '<div class="explanation-box">' +
            '  <div class="explanation-label">詳細解析</div>' +
            '  <div class="explanation-body">' + fmt.renderRichText(q.explanation) + "</div>" +
            "</div>";
        } else if (!q.explanation && !steps.length && !(q.rubric && q.rubric.length)) {
          veilInner +=
            '<div class="explanation-box">' +
            '  <div class="explanation-body">本題重點已包含於上方參考解答。</div>' +
            "</div>";
        }

        if (q.rubric && q.rubric.length) {
          veilInner +=
            '<div class="rubric-box">' +
            '  <div class="explanation-label">評分規準</div>' +
            '  <div class="rubric-list">';
          q.rubric.forEach((r) => {
            const scoreBadge = r.score ? '<span class="rubric-score-pill">' + fmt.escapeHtml(r.score) + "</span>" : "";
            veilInner += '<div class="rubric-item">' + scoreBadge + "<span>" + fmt.renderRichText(r.text) + "</span></div>";
          });
          veilInner += "</div></div>";
        }
      }

      bodyHtml += '<div class="answer-veil">' + veilInner + "</div>";
    }

    const totalQ = wk.questions.length;
    const progressLabel =
      dispNum !== String(q.number)
        ? `第 ${currentQIdx + 1} / ${totalQ} 題（原試題第 ${dispNum} 題）`
        : `第 ${currentQIdx + 1} / ${totalQ} 題`;

    container.innerHTML =
      '<div class="podium-grid ' + (hasLeftPanel ? "is-split" : "is-single") + '">' +
      leftHtml +
      '  <section class="paper-card" aria-label="目前試題">' + bodyHtml + "</section>" +
      "</div>" +
      '<div class="podium-nav-bar no-print">' +
      '  <button type="button" class="podium-step-btn" id="btnPrevQ" ' + (currentQIdx <= 0 ? "disabled" : "") + ">← 上一題</button>" +
      '  <div class="podium-progress">' + progressLabel + "</div>" +
      '  <button type="button" class="podium-step-btn" id="btnNextQ" ' + (currentQIdx >= totalQ - 1 ? "disabled" : "") + ">下一題 →</button>" +
      "</div>";

    // 綁定互動事件
    container.querySelectorAll("[data-select-opt]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-select-opt");
        st.selected = st.selected === key ? null : key;
        st.eliminated.delete(key);
        renderStage();
      });
    });

    container.querySelectorAll("[data-elim-opt]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-elim-opt");
        if (st.eliminated.has(key)) {
          st.eliminated.delete(key);
        } else {
          st.eliminated.add(key);
          if (st.selected === key) st.selected = null;
        }
        renderStage();
      });
    });

    container.querySelectorAll("[data-select-tf]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const val = btn.getAttribute("data-select-tf");
        st.selected = st.selected === val ? null : val;
        renderStage();
      });
    });

    const b1 = document.getElementById("btnRevealStage1");
    const b2 = document.getElementById("btnRevealStage2");
    if (b1) {
      b1.addEventListener("click", () => {
        st.revealStage = st.revealStage >= 1 ? 0 : 1;
        renderQuestionRail(wk);
        renderStage();
      });
    }
    if (b2) {
      b2.addEventListener("click", () => {
        st.revealStage = st.revealStage === 2 ? 1 : 2;
        if (st.revealStage === 2 && q.steps) st.revealedSteps = q.steps.length;
        renderQuestionRail(wk);
        renderStage();
      });
    }

    const nextStepBtn = document.getElementById("btnNextStep");
    const allStepBtn = document.getElementById("btnAllSteps");
    if (nextStepBtn) {
      nextStepBtn.addEventListener("click", () => {
        st.revealedSteps = Math.min((q.steps || []).length, st.revealedSteps + 1);
        renderStage();
      });
    }
    if (allStepBtn) {
      allStepBtn.addEventListener("click", () => {
        st.revealedSteps = (q.steps || []).length;
        renderStage();
      });
    }

    const prevBtn = document.getElementById("btnPrevQ");
    const nextBtn = document.getElementById("btnNextQ");
    if (prevBtn) prevBtn.addEventListener("click", () => stepQuestion(-1));
    if (nextBtn) nextBtn.addEventListener("click", () => stepQuestion(1));

    bindZoomButtons(container);
  }

  /* --------------------------------------------------------------------------
     試卷總覽模式 (Overview Sheet)
     -------------------------------------------------------------------------- */
  function renderOverviewSheet(wk) {
    const container = document.getElementById("stageContainer");
    if (!container || !wk) return;

    let html =
      '<div class="overview-sheet">' +
      '  <div class="print-header-only">' +
      '    <h2 style="margin:0;font-size:16pt;">' + fmt.escapeHtml(subjectData.title + " — " + wk.weekLabel + "：" + wk.title) + "</h2>" +
      '    <div style="margin-top:6pt;font-size:10.5pt;">班級：六年____班　　座號：______　　姓名：______________</div>' +
      "  </div>" +
      '  <div class="overview-toolbar no-print">' +
      '    <div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '      <button type="button" class="tool-btn" id="btnOvExpandAll">展開本週全部解答</button>' +
      '      <button type="button" class="tool-btn" id="btnOvCollapseAll">收合全部解答</button>' +
      "    </div>" +
      '    <div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '      <button type="button" class="tool-btn" id="btnPrintBlank">🖨️ 列印空白學習單</button>' +
      '      <button type="button" class="tool-btn tool-btn--primary" id="btnPrintTeacher">🖨️ 列印教師解答卷</button>' +
      "    </div>" +
      "  </div>";

    const printedContexts = new Set();

    (wk.sections || []).forEach((sec) => {
      const secQs = wk.questions
        .map((q, idx) => ({ q, idx }))
        .filter((x) => x.q.sectionId === sec.id);
      if (!secQs.length) return;

      html +=
        '<div class="overview-section-header">' +
        "  <h3>" + fmt.escapeHtml(sec.label + "、" + sec.title) + "</h3>" +
        (sec.scoreInfo ? "<span>" + fmt.escapeHtml(sec.scoreInfo) + "</span>" : "") +
        "</div>";

      secQs.forEach(({ q, idx }) => {
        if (q.contextId && !printedContexts.has(q.contextId)) {
          printedContexts.add(q.contextId);
          const ctx = findContext(wk, q.contextId);
          if (ctx) {
            html += '<div class="paper-card context-box">';
            html += '<div class="context-badge">' + fmt.escapeHtml(ctx.title.replace(/^[【]|[】]$/g, "")) + "</div>";
            if (ctx.intro) html += '<div class="context-intro">' + fmt.renderRichText(ctx.intro) + "</div>";
            if (ctx.excerptTitle || ctx.excerpt) {
              html +=
                '<div class="excerpt-box">' +
                (ctx.excerptTitle ? '<div class="excerpt-title">' + fmt.escapeHtml(ctx.excerptTitle) + "</div>" : "") +
                (ctx.excerpt ? '<div class="excerpt-body">' + fmt.renderRichText(ctx.excerpt) + "</div>" : "") +
                "</div>";
            }
            (ctx.figures || []).forEach((f) => {
              html += renderFigureHtml(f);
            });
            html += "</div>";
          }
        }

        const isOpen = overviewOpenAnswers.has(q.id);
        const dispNum = q.displayNumber || String(q.number);
        html += '<article class="overview-q-card">';
        html +=
          '<div class="q-header">' +
          '  <div class="q-header-left">' +
          '    <span class="q-num-badge">第 ' + fmt.escapeHtml(dispNum) + " 題</span>" +
          (q.tag ? '<span class="q-tag-pill">' + fmt.escapeHtml(q.tag) + "</span>" : "") +
          (q.points ? '<span class="q-points">' + q.points + " 分</span>" : "") +
          "  </div>" +
          '  <div class="overview-q-actions no-print">' +
          '    <button type="button" class="tool-btn" data-ov-toggle="' + fmt.escapeHtml(q.id) + '">' + (isOpen ? "收合解答" : "顯示解答") + "</button>" +
          '    <button type="button" class="tool-btn" data-ov-focus="' + idx + '">講台投影</button>' +
          "  </div>" +
          "</div>";

        if (q.material) html += '<div class="q-material">' + fmt.renderRichText(q.material) + "</div>";
        html += '<div class="q-prompt" style="font-size:16.5px;">' + fmt.renderRichText(q.prompt) + "</div>";

        (q.figures || []).forEach((f) => {
          html += renderFigureHtml(f);
        });

        if (q.subQuestions && q.subQuestions.length) {
          html += '<div class="q-subquestions">';
          q.subQuestions.forEach((sq) => {
            html += '<div class="q-subq-item" style="font-size:15px;"><span class="q-subq-label">' + fmt.escapeHtml(sq.label) + "</span><div>" + fmt.renderRichText(sq.text) + "</div></div>";
          });
          html += "</div>";
        }

        if (q.bulletPrompts && q.bulletPrompts.length) {
          html += '<ul class="q-bullet-prompts">';
          q.bulletPrompts.forEach((bp) => {
            html += '<li style="font-size:15px;">' + fmt.renderRichText(bp) + "</li>";
          });
          html += "</ul>";
        }

        if (q.stances && q.stances.length) {
          html += '<div class="stances-grid">';
          q.stances.forEach((sc) => {
            html += '<div class="stance-card"><div class="stance-label">' + fmt.escapeHtml(sc.label) + '</div><div class="stance-text" style="font-size:14.5px;">' + fmt.renderRichText(sc.text) + "</div></div>";
          });
          html += "</div>";
          if (q.stancePrompt) html += '<div class="stance-prompt" style="font-size:15.5px;">' + fmt.renderRichText(q.stancePrompt) + "</div>";
        }

        if (q.options && q.options.length) {
          html += '<div class="overview-options">';
          q.options.forEach((opt) => {
            const cls = isOpen && q.answer === opt.key ? "overview-opt is-correct" : "overview-opt";
            html += '<div class="' + cls + '"><strong>(' + fmt.escapeHtml(opt.key) + ")</strong> " + fmt.renderRichText(opt.text) + "</div>";
          });
          html += "</div>";
        }

        let ansSummary = "";
        if (q.type === "choice") {
          ansSummary = "<strong>答案：(" + fmt.escapeHtml(q.answer) + ")</strong>";
        } else if (q.type === "true_false") {
          ansSummary = "<strong>答案：" + fmt.escapeHtml(q.answer) + "</strong>";
        } else {
          ansSummary = "<strong>參考解答：</strong><br>" + fmt.renderRichText(q.answerText);
        }

        let ovExtra = "";
        if (q.steps && q.steps.length) {
          ovExtra += '<ol class="steps-list" style="margin-top:8px;">' + q.steps.map((s) => '<li style="font-size:14.5px;">' + fmt.renderRichText(s) + "</li>").join("") + "</ol>";
        }
        const stepsJoined = (q.steps || []).join("\n");
        if (q.explanation && q.explanation.trim() !== stepsJoined.trim()) {
          ovExtra += '<div style="margin-top:6px;color:var(--ink-secondary);"><strong>解析：</strong>' + fmt.renderRichText(q.explanation) + "</div>";
        }
        if (q.rubric && q.rubric.length) {
          ovExtra +=
            '<div class="rubric-list" style="margin-top:8px;">' +
            q.rubric
              .map(
                (r) =>
                  '<div class="rubric-item" style="font-size:13.5px;">' +
                  (r.score ? '<span class="rubric-score-pill">' + fmt.escapeHtml(r.score) + "</span>" : "") +
                  "<span>" + fmt.renderRichText(r.text) + "</span></div>"
              )
              .join("") +
            "</div>";
        }

        html +=
          '<div class="overview-answer-block"' + (isOpen ? "" : " hidden") + ">" +
          ansSummary +
          ovExtra +
          "</div>";

        html += "</article>";
      });
    });

    html += "</div>";
    container.innerHTML = html;

    const btnExp = document.getElementById("btnOvExpandAll");
    const btnCol = document.getElementById("btnOvCollapseAll");
    const btnPrintBlank = document.getElementById("btnPrintBlank");
    const btnPrintTeacher = document.getElementById("btnPrintTeacher");

    if (btnExp) {
      btnExp.addEventListener("click", () => {
        wk.questions.forEach((q) => overviewOpenAnswers.add(q.id));
        renderStage();
      });
    }
    if (btnCol) {
      btnCol.addEventListener("click", () => {
        overviewOpenAnswers.clear();
        renderStage();
      });
    }
    if (btnPrintBlank) {
      btnPrintBlank.addEventListener("click", () => {
        document.body.classList.add("print-hide-answers");
        window.print();
        setTimeout(() => document.body.classList.remove("print-hide-answers"), 500);
      });
    }
    if (btnPrintTeacher) {
      btnPrintTeacher.addEventListener("click", () => {
        document.body.classList.remove("print-hide-answers");
        wk.questions.forEach((q) => overviewOpenAnswers.add(q.id));
        renderStage();
        setTimeout(() => window.print(), 100);
      });
    }

    container.querySelectorAll("[data-ov-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-ov-toggle");
        if (overviewOpenAnswers.has(id)) overviewOpenAnswers.delete(id);
        else overviewOpenAnswers.add(id);
        renderStage();
      });
    });

    container.querySelectorAll("[data-ov-focus]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentQIdx = Number(btn.getAttribute("data-ov-focus"));
        setMode("podium");
      });
    });

    bindZoomButtons(container);
  }

  function bindZoomButtons(rootEl) {
    rootEl.querySelectorAll("[data-zoom-src]").forEach((el) => {
      el.addEventListener("click", () => {
        tools.openLightbox(el.getAttribute("data-zoom-src"), el.getAttribute("data-zoom-caption"));
      });
    });
  }

  function renderMetaNotes() {
    const el = document.getElementById("metaNotesContainer");
    if (!el) return;
    const notes = (subjectData && subjectData.metaNotes) || [];
    if (!notes.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = notes
      .map(
        (n) =>
          '<details class="meta-notes-details">' +
          "  <summary>📜 " + fmt.escapeHtml(n.title) + "</summary>" +
          "  <ul>" + n.items.map((it) => "<li>" + fmt.escapeHtml(it) + "</li>").join("") + "</ul>" +
          "</details>"
      )
      .join("");
  }

  function renderEmptyState() {
    const railEl = document.getElementById("questionRail");
    const metaEl = document.getElementById("weekMetaStrip");
    const container = document.getElementById("stageContainer");
    if (railEl) railEl.hidden = true;
    if (metaEl) metaEl.hidden = true;
    if (container) {
      container.innerHTML =
        '<div class="empty-state-card">' +
        "  <h2>" + fmt.escapeHtml(subjectData.subjectLabel) + "科目前尚無匯入試題</h2>" +
        "  <p>請將包含題目的 <code>.docx</code>、<code>.pdf</code> 或 <code>.md</code> 檔案放入 <code>sources/" +
        fmt.escapeHtml(SUBJECT) +
        "/</code>，再執行 <code>./update.command</code> 或 <code>python3 scripts/build_data.py</code>。</p>" +
        '  <a class="tool-btn tool-btn--primary" href="index.html">返回課程總覽</a>' +
        "</div>";
    }
  }

  function setMode(mode) {
    currentMode = mode === "overview" ? "overview" : "podium";
    document.querySelectorAll("[data-mode-btn]").forEach((b) => {
      b.classList.toggle("is-active", b.getAttribute("data-mode-btn") === currentMode);
    });
    renderStage();
    syncUrlState();
  }

  function stepQuestion(delta) {
    const wk = currentWeek();
    if (!wk || !wk.questions.length) return;
    const next = currentQIdx + delta;
    if (next < 0 || next >= wk.questions.length) return;
    currentQIdx = next;
    tools.clearBoardCanvas();
    renderStage();
    syncUrlState();
  }

  function renderStage() {
    const wk = currentWeek();
    if (!wk || !wk.questions || !wk.questions.length) {
      renderEmptyState();
      return;
    }
    const metaEl = document.getElementById("weekMetaStrip");
    if (metaEl) metaEl.hidden = false;
    renderQuestionRail(wk);
    if (currentMode === "overview") {
      const railEl = document.getElementById("questionRail");
      if (railEl) railEl.hidden = true;
      renderOverviewSheet(wk);
    } else {
      renderPodiumStage(wk, currentQuestion());
    }
  }

  function renderAll() {
    const wk = currentWeek();
    if (!wk) {
      renderEmptyState();
      return;
    }
    renderWeekHeader(wk);
    renderStage();
    renderMetaNotes();
    syncUrlState();
  }

  /* --------------------------------------------------------------------------
     鍵盤快捷鍵
     -------------------------------------------------------------------------- */
  function initKeyboard() {
    document.addEventListener("keydown", (e) => {
      if (e.target && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;

      const lb = document.getElementById("lightboxModal");
      const sc = document.getElementById("shortcutModal");

      if (e.key === "Escape") {
        if (lb && !lb.hidden) { tools.closeLightbox(); return; }
        if (sc && !sc.hidden) { tools.toggleShortcutModal(false); return; }
        tools.closePopovers();
        if (tools.isDrawEnabled()) tools.setDrawEnabled(false);
        return;
      }

      if ((lb && !lb.hidden) || (sc && !sc.hidden)) return;

      if (e.key === "?" || (e.shiftKey && e.key === "/")) {
        e.preventDefault();
        tools.toggleShortcutModal();
        return;
      }
      if (e.key === "t" || e.key === "T") {
        e.preventDefault();
        tools.togglePopover("timer");
        return;
      }
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        tools.togglePopover("picker");
        return;
      }
      if (e.key === "b" || e.key === "B") {
        e.preventDefault();
        tools.setDrawEnabled(!tools.isDrawEnabled());
        return;
      }
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        tools.toggleFullscreen();
        return;
      }

      if (currentMode !== "podium") return;
      const q = currentQuestion();
      if (!q) return;
      const st = getQState(q.id);

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        stepQuestion(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        stepQuestion(1);
      } else if (e.code === "Space") {
        e.preventDefault();
        if (st.revealStage === 0) {
          st.revealStage = 1;
        } else if (st.revealStage === 1) {
          const steps = q.steps || [];
          if ((q.type === "applied" || q.type === "calculation") && st.revealedSteps < steps.length) {
            st.revealedSteps += 1;
          } else {
            st.revealStage = 2;
            if (steps.length) st.revealedSteps = steps.length;
          }
        } else {
          st.revealStage = 0;
        }
        renderStage();
      } else if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        st.revealStage = st.revealStage === 2 ? 0 : 2;
        if (st.revealStage === 2 && q.steps) st.revealedSteps = q.steps.length;
        renderStage();
      } else if (["1", "2", "3", "4"].includes(e.key)) {
        const idx = Number(e.key) - 1;
        if (q.type === "choice" && q.options && q.options[idx]) {
          const k = q.options[idx].key;
          st.selected = st.selected === k ? null : k;
          st.eliminated.delete(k);
          renderStage();
        } else if (q.type === "true_false" && (idx === 0 || idx === 1)) {
          const tf = idx === 0 ? "○" : "✕";
          st.selected = st.selected === tf ? null : tf;
          renderStage();
        }
      }
    });
  }

  async function boot() {
    tools.init();
    initKeyboard();

    document.querySelectorAll("[data-mode-btn]").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.getAttribute("data-mode-btn")));
    });

    const container = document.getElementById("stageContainer");
    try {
      const resp = await fetch("data/" + SUBJECT + ".json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      subjectData = await resp.json();

      // 還原 URL query (?week=12&q=7&mode=overview)
      const params = new URLSearchParams(window.location.search);
      const reqWeek = Number(params.get("week"));
      const reqQ = params.get("q");
      const reqMode = params.get("mode");

      if (reqWeek && subjectData.weeks) {
        const wIdx = subjectData.weeks.findIndex((w) => w.weekNumber === reqWeek);
        if (wIdx >= 0) currentWeekIdx = wIdx;
      }

      const wk = currentWeek();
      if (wk && reqQ) {
        const qIdx = wk.questions.findIndex(
          (q) => String(q.displayNumber || q.number) === reqQ || String(q.number) === reqQ
        );
        if (qIdx >= 0) currentQIdx = qIdx;
      }

      if (reqMode === "overview") currentMode = "overview";
      document.querySelectorAll("[data-mode-btn]").forEach((b) => {
        b.classList.toggle("is-active", b.getAttribute("data-mode-btn") === currentMode);
      });

      const footerUpdate = document.getElementById("footerUpdatedAt");
      if (footerUpdate && subjectData.updatedAt) {
        footerUpdate.textContent = "資料更新：" + subjectData.updatedAt.replace("T", " ").slice(0, 16);
      }

      renderAll();
    } catch (err) {
      if (container) {
        container.innerHTML =
          '<div class="empty-state-card">' +
          "  <h2>無法讀取試題資料</h2>" +
          "  <p>請確認已透過本機伺服器或 GitHub Pages 開啟本頁，並先執行 <code>python3 scripts/build_data.py</code> 產生資料檔。</p>" +
          '  <p style="font-size:13px;color:var(--ink-tertiary);">' + fmt.escapeHtml(err.message) + "</p>" +
          "</div>";
      }
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
