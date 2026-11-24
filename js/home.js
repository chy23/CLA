/**
 * 首頁課程評量總覽：自動讀取 data/manifest.json 呈現三科入口與跨科週次矩陣
 */
(function () {
  const fmt = window.CLAFormat;

  const SUBJECT_META = [
    {
      key: "chinese",
      label: "國語",
      page: "chinese.html",
      desc: "翰林版第十一冊 · 情境閱讀、字詞句型辨析、現場圖表解讀與生活思辨",
    },
    {
      key: "math",
      label: "數學",
      page: "math.html",
      desc: "康軒版六上 · 圓周率、圓周長、扇形與圓面積時事素養計算與分步推理",
    },
    {
      key: "social",
      label: "社會",
      page: "social.html",
      desc: "南一版六上 · 戰後臺灣經濟發展、十大建設、半導體產業鏈與城鄉地方創生",
    },
  ];

  async function boot() {
    try {
      const resp = await fetch("data/manifest.json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const manifest = await resp.json();
      const subjects = manifest.subjects || {};

      // 1. 頂部統計指標
      const statsEl = document.getElementById("heroStats");
      if (statsEl) {
        const totalQuestions = SUBJECT_META.reduce(
          (sum, m) => sum + ((subjects[m.key] && subjects[m.key].totalQuestions) || 0),
          0
        );
        const allWeekNums = new Set();
        SUBJECT_META.forEach((m) => {
          const wks = (subjects[m.key] && subjects[m.key].weeks) || [];
          wks.forEach((w) => allWeekNums.add(w.weekNumber));
        });
        statsEl.innerHTML =
          '<div class="hero-stat"><span class="hero-stat-label">收錄科目</span><span class="hero-stat-val">3 科</span></div>' +
          '<div class="hero-stat"><span class="hero-stat-label">涵蓋週次</span><span class="hero-stat-val">' + allWeekNums.size + " 週</span></div>" +
          '<div class="hero-stat"><span class="hero-stat-label">累積素養題</span><span class="hero-stat-val">' + totalQuestions + " 題</span></div>";
      }

      // 2. 三科講台入口卡
      const gridEl = document.getElementById("subjectGrid");
      if (gridEl) {
        gridEl.innerHTML = SUBJECT_META.map((m) => {
          const info = subjects[m.key] || { totalWeeks: 0, totalQuestions: 0, weeks: [] };
          const pills = (info.weeks || [])
            .map(
              (w) =>
                '<a class="week-pill" href="' + m.page + "?week=" + w.weekNumber + '">' +
                fmt.escapeHtml(w.weekLabel) +
                '<span class="week-pill-cnt">' + w.questionCount + "題</span></a>"
            )
            .join("");

          const pillsArea = pills
            ? '<div class="week-pills">' + pills + "</div>"
            : '<div class="matrix-empty">尚待匯入試題檔（放進 sources/' + m.key + "/ 後更新）</div>";

          return (
            '<article class="subject-card subject-card--' + m.key + '">' +
            '  <div class="subject-card-top">' +
            '    <span class="subject-badge">' + fmt.escapeHtml(m.label) + "科講台</span>" +
            '    <span class="subject-count">' + info.totalWeeks + " 週 · " + info.totalQuestions + " 題</span>" +
            "  </div>" +
            "  <div>" +
            '    <h2 class="subject-card-title">' + fmt.escapeHtml(info.title || m.label + "形成性評量") + "</h2>" +
            '    <p class="subject-card-meta">' + fmt.escapeHtml(m.desc) + "</p>" +
            "  </div>" +
            pillsArea +
            '  <div class="subject-card-cta">' +
            '    <a class="enter-btn" href="' + m.page + '">進入' + fmt.escapeHtml(m.label) + "講台 →</a>" +
            '    <a class="overview-link" href="' + m.page + '?mode=overview">試卷總覽 / 列印</a>' +
            "  </div>" +
            "</article>"
          );
        }).join("");
      }

      // 3. 跨科週次課程矩陣
      const matrixBody = document.getElementById("matrixBody");
      if (matrixBody) {
        const weekSet = new Set();
        SUBJECT_META.forEach((m) => {
          ((subjects[m.key] && subjects[m.key].weeks) || []).forEach((w) => weekSet.add(w.weekNumber));
        });
        const sortedWeeks = Array.from(weekSet).sort((a, b) => a - b);

        matrixBody.innerHTML = sortedWeeks
          .map((wkNum) => {
            const cells = SUBJECT_META.map((m) => {
              const wk = ((subjects[m.key] && subjects[m.key].weeks) || []).find((w) => w.weekNumber === wkNum);
              if (!wk) return '<td><span class="matrix-empty">尚未排定</span></td>';
              return (
                "<td>" +
                '  <a class="matrix-link" href="' + m.page + "?week=" + wkNum + '">' +
                '    <span class="matrix-link-title">' + fmt.escapeHtml(wk.title) + "</span>" +
                '    <span class="matrix-link-meta">' + wk.questionCount + " 題" + (wk.schedule ? " · " + fmt.escapeHtml(wk.schedule.slice(0, 26)) + (wk.schedule.length > 26 ? "…" : "") : "") + "</span>" +
                "  </a>" +
                "</td>"
              );
            }).join("");
            return '<tr><td class="matrix-week">第 ' + wkNum + " 週</td>" + cells + "</tr>";
          })
          .join("");
      }

      const footerGen = document.getElementById("footerGeneratedAt");
      if (footerGen && manifest.generatedAt) {
        footerGen.textContent = "最近同步：" + manifest.generatedAt.replace("T", " ").slice(0, 16);
      }
    } catch (err) {
      const gridEl = document.getElementById("subjectGrid");
      if (gridEl) {
        gridEl.innerHTML =
          '<div class="empty-state-card" style="grid-column:1/-1;">' +
          "  <h2>無法載入課程總覽資料</h2>" +
          "  <p>請透過本機 HTTP 伺服器（例如 <code>python3 -m http.server</code>）或 GitHub Pages 瀏覽。</p>" +
          "</div>";
      }
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
