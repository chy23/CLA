/**
 * 文字安全跳脫、Markdown 強調與 KaTeX 數學公式排版工具
 */
window.CLAFormat = (function () {
  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fallbackMathToText(tex) {
    return String(tex)
      .replace(/\\rightarrow|\\to/g, "→")
      .replace(/\\leftarrow/g, "←")
      .replace(/\\times/g, "×")
      .replace(/\\div/g, "÷")
      .replace(/\\approx/g, "≈")
      .replace(/\\neq/g, "≠")
      .replace(/\\leq/g, "≤")
      .replace(/\\geq/g, "≥")
      .replace(/\\cdot/g, "·")
      .replace(/\\pi/g, "π")
      .replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, "$1/$2")
      .replace(/\\left|\\right/g, "")
      .trim();
  }

  function renderTex(tex, displayMode) {
    if (window.katex && typeof window.katex.renderToString === "function") {
      try {
        return window.katex.renderToString(tex.trim(), {
          displayMode: Boolean(displayMode),
          throwOnError: false,
          strict: false,
        });
      } catch (_) {
        return '<span class="math-fallback">' + escapeHtml(fallbackMathToText(tex)) + "</span>";
      }
    }
    return '<span class="math-fallback">' + escapeHtml(fallbackMathToText(tex)) + "</span>";
  }

  function renderRichText(raw) {
    if (!raw) return "";
    const placeholders = [];
    let text = String(raw);

    // 1. 先抽離區塊公式 $$...$$
    text = text.replace(/\$\$([\s\S]+?)\$\$/g, function (_, inner) {
      const idx = placeholders.length;
      placeholders.push('<div class="math-display">' + renderTex(inner, true) + "</div>");
      return "\u0000MATH" + idx + "\u0000";
    });

    // 2. 抽離行內公式 $...$
    text = text.replace(/\$([^\n$]+?)\$/g, function (full, inner) {
      // 避免把純金額誤判，若內容全空白則保留
      if (!inner.trim()) return full;
      const idx = placeholders.length;
      placeholders.push('<span class="math-inline">' + renderTex(inner, false) + "</span>");
      return "\u0000MATH" + idx + "\u0000";
    });

    // 3. 安全跳脫 HTML，再轉 **粗體** 與換行
    let safe = escapeHtml(text);
    safe = safe.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/\n/g, "<br>");

    // 4. 還原公式節點
    safe = safe.replace(/\u0000MATH(\d+)\u0000/g, function (_, idxStr) {
      return placeholders[Number(idxStr)] || "";
    });

    return safe;
  }

  return {
    escapeHtml: escapeHtml,
    renderRichText: renderRichText,
  };
})();
