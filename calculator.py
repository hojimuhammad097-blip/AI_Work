"""Графический калькулятор.

Математическое ядро (безопасное вычисление выражений через AST) сохранено
из консольной версии. Вместо консольного меню теперь запускается
графический интерфейс: современная тёмная веб-страница, удобная на телефоне.

Запуск:
    python calculator.py            # сервер на http://127.0.0.1:8000
    python calculator.py --port 8080
    python calculator.py --cli      # старый консольный REPL (для тестов/SSH)

Кнопки интерфейса: 0-9, +, -, ×, ÷, %, скобки ( ), точка, =, C, ⌫ (backspace).
История открывается маленькой иконкой часов слева в верхней строке:
LEFT DRAWER выезжает слева направо поверх кнопок калькулятора
(калькулятор не перестраивается, правая колонка операций остаётся
видимой; внизу drawer кнопка «Очистить журнал», клик по записи
возвращает её в ввод). Есть обработка ошибок
(деление на ноль, неправильные выражения).

Зависимости: только стандартная библиотека Python 3.8+.
"""

import ast
import argparse
import json
import operator
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Математическое ядро (сохранено из консольной версии)
# ---------------------------------------------------------------------------

# Разрешённые операции для безопасного вычисления
_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Сколько записей хранить в истории
HISTORY_LIMIT = 50


def add(a: float, b: float) -> float:
    """Сложение."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Вычитание."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Умножение."""
    return a * b


def divide(a: float, b: float) -> float:
    """Деление с защитой от деления на ноль."""
    if b == 0:
        raise ZeroDivisionError("Ошибка: деление на ноль!")
    return a / b


def _eval_node(node: ast.AST) -> float:
    """Рекурсивно вычисляет разрешённый AST-узел."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Некорректное значение: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise ValueError(
                f"Операция {op_type.__name__} не поддерживается. Разрешены: +, -, *, /."
            )
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type is ast.Div and right == 0:
            raise ZeroDivisionError("Ошибка: деление на ноль!")
        return _ALLOWED_BINOPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARYOPS:
            raise ValueError(f"Унарная операция {op_type.__name__} не поддерживается.")
        return _ALLOWED_UNARYOPS[op_type](_eval_node(node.operand))
    raise ValueError(
        f"Некорректное выражение: запрещён элемент {type(node).__name__}."
    )


def normalize_expression(expression: str) -> str:
    """Приводит выражение с кнопок GUI к виду, понятному calculate().

    Заменяет красивые символы кнопок на ASCII-операторы:
        '×' -> '*', '÷' -> '/', '−' (U+2212) -> '-',
        запятая -> точка (десятичный разделитель).
    Символ '%' сохраняется как есть — его раскрывает expand_percent().
    """
    if expression is None:
        return ""
    text = str(expression)
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-")
    text = text.replace(",", ".")
    return text.strip()


# Регулярки для процентов (работают уже по нормализованному ASCII-тексту)
_PERCENT_ADD_RE = re.compile(
    r"(?P<base>\d+(?:\.\d+)?)\s*(?P<op>[+\-])\s*(?P<pct>\d+(?:\.\d+)?)%"
)
_PERCENT_NUM_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)%")
_PERCENT_PAREN_RE = re.compile(r"\)\s*%")


def expand_percent(expression: str) -> str:
    """Раскрывает '%' в обычную арифметику.

    Семантика (как на обычных калькуляторах):
      - ``50%`` -> ``(50/100)`` = 0.5
      - ``200*10%`` -> ``200*(10/100)`` = 20
      - ``200/10%`` -> ``200/(10/100)`` = 2000
      - ``200+10%`` -> ``200+(200*10/100)`` = 220 (10% от 200)
      - ``200-10%`` -> ``200-(200*10/100)`` = 180
      - ``(20+30)%`` -> ``(20+30)/100`` = 0.5
      - ``10%+20%`` -> ``(10/100)+(20/100)`` = 0.3

    Для ``+``/``-`` особое правило применяется только когда слева
    непосредственно число (самый частый сценарий). В остальных случаях
    ``X%`` означает ``X/100``.

    Raises:
        ValueError: если '%' стоит без числа/скобки перед ним.
    """
    text = expression
    # 1) A+B% / A-B% -> A+(A*B/100). Повторяем для цепочек вида 50+10%+5%.
    for _ in range(50):
        m = _PERCENT_ADD_RE.search(text)
        if not m:
            break
        base = m.group("base")
        op = m.group("op")
        pct = m.group("pct")
        replacement = f"{base}{op}({base}*{pct}/100)"
        text = text[: m.start()] + replacement + text[m.end() :]
    # 2) Обычные N% -> (N/100)
    text = _PERCENT_NUM_RE.sub(r"(\g<num>/100)", text)
    # 3) )% -> )/100  (процент от выражения в скобках)
    text = _PERCENT_PAREN_RE.sub(")/100", text)
    # 4) Оставшийся '%' — ошибка использования
    if "%" in text:
        raise ValueError(
            "Некорректное использование '%'. Примеры: 50%, 200+10%, (20+30)%."
        )
    return text


def calculate(expression: str) -> float:
    """Вычисляет арифметическое выражение.

    Поддерживает: числа (целые и десятичные), +, -, *, /, скобки,
    унарный минус/плюс, проценты (%). Понимает также символы кнопок
    GUI: ×, ÷, −. Десятичный разделитель: точка или запятая.

    Проценты:
        50% = 0.5; 200*10% = 20; 200/10% = 2000;
        200+10% = 220; 200-10% = 180; (20+30)% = 0.5.

    Args:
        expression: строка вида "(2.5 + 3) * 4 / 2".

    Raises:
        ValueError: пустой ввод, недопустимые символы, синтаксическая ошибка.
        ZeroDivisionError: деление на ноль.
    """
    if expression is None:
        raise ValueError("Пустое выражение.")
    text = normalize_expression(expression)
    if not text:
        raise ValueError("Пустое выражение. Введите, например: (2 + 3) * 4.")
    # Раскрываем проценты до проверки символов (там '%' уже не должно остаться)
    text = expand_percent(text)
    if not text.strip():
        raise ValueError("Пустое выражение. Введите, например: (2 + 3) * 4.")
    # Быстрая проверка допустимых символов
    allowed_chars = set("0123456789+-*/(). \t")
    for ch in text:
        if ch not in allowed_chars:
            raise ValueError(
                f"Недопустимый символ: {ch!r}. Разрешены цифры, +, -, *, /, скобки, точка."
            )
    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Синтаксическая ошибка в выражении: {exc.msg}") from exc
    return _eval_node(parsed)


def format_result(value: float) -> str:
    """Красиво форматирует число: убирает лишний '.0'."""
    if float(value).is_integer():
        return str(int(value))
    # Округляем до 10 знаков, чтобы убрать артефакты float
    rounded = round(float(value), 10)
    if rounded.is_integer():
        return str(int(rounded))
    text = str(rounded)
    return text.rstrip("0").rstrip(".") if "." in text else text


def evaluate_expression(expression: str) -> dict:
    """Вычисляет выражение для GUI/API. Никогда не бросает исключения.

    Returns:
        dict вида {"ok": True, "result": float, "formatted": str,
                   "expression": str}
        или {"ok": False, "error": str, "expression": str}.
    """
    original = "" if expression is None else str(expression)
    try:
        value = calculate(original)
    except ZeroDivisionError as exc:
        return {"ok": False, "error": str(exc), "expression": original}
    except ValueError as exc:
        return {"ok": False, "error": f"Ошибка ввода: {exc}", "expression": original}
    except Exception as exc:  # на всякий случай
        return {"ok": False, "error": f"Неожиданная ошибка: {exc}", "expression": original}
    return {
        "ok": True,
        "result": float(value),
        "formatted": format_result(value),
        "expression": original,
    }


class CalculatorHistory:
    """История последних вычислений для GUI."""

    def __init__(self, limit: int = HISTORY_LIMIT):
        self.limit = max(1, int(limit))
        self._items: list = []

    def add(self, expression: str, result: str) -> dict:
        """Добавляет запись, возвращает её."""
        entry = {"expression": str(expression), "result": str(result)}
        self._items.insert(0, entry)
        del self._items[self.limit:]
        return entry

    def clear(self) -> None:
        self._items.clear()

    def get_all(self) -> list:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Веб-интерфейс (стандартная библиотека, без зависимостей)
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Калькулятор</title>
<style>
  :root {
    --bg: #0f172a;
    --card: #1e293b;
    --display: #0b1220;
    --btn: #334155;
    --btn-hover: #475569;
    --btn-op: #f59e0b;
    --btn-op-hover: #fbbf24;
    --btn-danger: #ef4444;
    --btn-danger-hover: #f87171;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #22d3ee;
    --radius: 14px;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    display: flex; justify-content: center; align-items: flex-start;
    padding: 12px;
  }
  .app { width: 100%; max-width: 430px; display: flex; flex-direction: column; gap: 12px; }
  .topbar { display: flex; align-items: center; gap: 10px; padding-top: 6px; }
  .brand { font-size: 1.25rem; font-weight: 700; }
  .brand small { display: block; color: var(--muted); font-size: 0.8rem; font-weight: 400; }
  #openHistory {
    width: 38px; height: 38px; min-width: 38px;
    display: inline-flex; align-items: center; justify-content: center;
    background: var(--card); border: 1px solid var(--btn-hover); color: var(--text);
    border-radius: 12px; font-size: 1.15rem; line-height: 1;
    cursor: pointer; touch-action: manipulation; padding: 0;
  }
  #openHistory:hover { border-color: var(--accent); }
  #openHistory:active { transform: scale(.94); }
  .card { background: var(--card); border-radius: var(--radius); padding: 14px;
          box-shadow: 0 10px 30px rgba(0,0,0,.35); }
  .display {
    background: var(--display); border-radius: var(--radius);
    padding: 16px 14px; min-height: 110px;
    display: flex; flex-direction: column; justify-content: center; gap: 6px;
    overflow: hidden;
  }
  #expr {
    width: 100%; background: transparent; border: none; outline: none;
    color: var(--muted); font-size: 1.15rem; text-align: right;
    min-height: 1.6em; word-break: break-all;
  }
  #result { font-size: 2.4rem; font-weight: 800; text-align: right;
            min-height: 1.3em; word-break: break-all; line-height: 1.1; }
  #result.ok { color: var(--text); }
  #result.err { color: var(--btn-danger-hover); font-size: 1.15rem; font-weight: 600; }
  .grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 12px;
  }
  button.key {
    border: none; border-radius: 12px; background: var(--btn); color: var(--text);
    font-size: 1.35rem; font-weight: 600; cursor: pointer;
    min-height: 62px; touch-action: manipulation; user-select: none;
    transition: transform .05s ease, background .15s ease;
  }
  button.key:hover { background: var(--btn-hover); }
  button.key:active { transform: scale(.96); }
  button.op { background: #3b3a2a; color: var(--btn-op-hover); font-size: 1.5rem; }
  button.op:hover { background: #4a492f; }
  button.eq { background: var(--btn-op); color: #1a1a1a; font-size: 1.7rem;
              grid-column: span 4; min-height: 66px; }
  button.eq:hover { background: var(--btn-op-hover); }
  button.danger { color: #fecaca; }
  .hint { color: var(--muted); font-size: 0.78rem; text-align: center; margin-top: 10px; }
  /* История — LEFT DRAWER: выезжает слева поверх кнопок калькулятора.
     Калькулятор (.app) при этом не перестраивается; правая колонка
     кнопок остаётся видимой, т.к. ширина drawer < 100%. */
  .scrim {
    position: fixed; inset: 0; background: rgba(2,6,23,.18);
    z-index: 40;
  }
  .scrim.hidden { display: none; }
  .drawer {
    position: fixed; top: 0; left: 0; bottom: 0;
    width: min(78vw, 320px);
    background: #1c2942; border-right: 1px solid rgba(148,163,184,.22);
    border-radius: 0 20px 20px 0;
    padding: 12px;
    box-shadow: 20px 0 60px rgba(0,0,0,.55), 2px 0 12px rgba(0,0,0,.4);
    z-index: 50;
    transform: translateX(-105%);
    transition: transform .25s ease;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .drawer.open { transform: translateX(0); }
  #closeHistory {
    align-self: flex-end;
    width: 32px; height: 32px; min-width: 32px;
    display: flex; align-items: center; justify-content: center;
    background: var(--display); border: 1px solid var(--btn-hover); color: var(--text);
    border-radius: 10px; font-size: 1rem; font-weight: 700; line-height: 1;
    cursor: pointer; touch-action: manipulation; padding: 0; margin-bottom: 8px;
  }
  #closeHistory:hover { border-color: var(--accent); }
  #closeHistory:active { transform: scale(.94); }
  .drawer-foot { display: flex; gap: 10px; margin-top: 12px; }
  #clearHistory {
    flex: 1 1 0; min-height: 48px;
    border-radius: 14px; font-size: 1rem; font-weight: 700; cursor: pointer;
    touch-action: manipulation;
    background: transparent; border: 1px solid var(--btn-hover); color: var(--text);
  }
  #clearHistory:hover { border-color: var(--muted); }
  #historyList { list-style: none; margin: 2px 0 0; padding: 2px;
                 display: flex; flex-direction: column; gap: 8px;
                 overflow-y: auto; overscroll-behavior: contain;
                 -webkit-overflow-scrolling: touch; touch-action: pan-y;
                 flex: 1 1 auto; min-height: 0; }
  #historyList li {
    background: var(--display); border: 1px solid rgba(148,163,184,.14);
    border-radius: 14px; padding: 10px 12px;
    font-size: 0.9rem; cursor: pointer; display: flex; flex-direction: column; gap: 3px;
  }
  #historyList li:hover { border-color: var(--btn-hover); }
  #historyList li:active { background: #16213a; transform: scale(.99); }
  #historyList .hexpr { color: var(--muted); font-size: 0.85rem; word-break: break-all; }
  #historyList .hres { color: var(--text); font-size: 1.2rem; font-weight: 800; word-break: break-all; text-align: right; }
  .empty { color: var(--muted); text-align: center; font-size: 0.85rem; padding: 12px 8px; }
  @media (max-width: 380px) {
    button.key { min-height: 58px; font-size: 1.25rem; }
    #result { font-size: 2rem; }
  }
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <button id="openHistory" aria-label="Открыть историю" aria-haspopup="dialog" title="История">🕘</button>
    <div class="brand">🧮 Калькулятор<small>Тёмная тема · для телефона</small></div>
  </div>
  <div class="card">
    <div class="display">
      <input id="expr" type="text" placeholder="0" autocomplete="off" spellcheck="false" aria-label="Выражение">
      <div id="result" class="ok">0</div>
    </div>
    <div class="grid" id="keys">
      <button class="key danger" data-act="clear">C</button>
      <button class="key op" data-ins="(">(</button>
      <button class="key op" data-ins=")">)</button>
      <button class="key op" data-ins="%">%</button>
      <button class="key" data-ins="7">7</button>
      <button class="key" data-ins="8">8</button>
      <button class="key" data-ins="9">9</button>
      <button class="key op" data-ins="÷">÷</button>
      <button class="key" data-ins="4">4</button>
      <button class="key" data-ins="5">5</button>
      <button class="key" data-ins="6">6</button>
      <button class="key op" data-ins="×">×</button>
      <button class="key" data-ins="1">1</button>
      <button class="key" data-ins="2">2</button>
      <button class="key" data-ins="3">3</button>
      <button class="key op" data-ins="−">−</button>
      <button class="key" data-ins="0">0</button>
      <button class="key" data-ins=".">.</button>
      <button class="key op" data-act="back">⌫</button>
      <button class="key op" data-ins="+">+</button>
      <button class="key eq" data-act="eq">=</button>
    </div>
    <div class="hint">Можно печатать с клавиатуры: цифры, + − * / % ( ) . Enter (=), Backspace, Esc (C)</div>
  </div>
</div>
<div id="historyOverlay" class="scrim hidden"></div>
<div id="historyDrawer" class="drawer" role="dialog" aria-modal="true" aria-label="История вычислений">
  <button id="closeHistory" aria-label="Закрыть историю">✕</button>
  <ul id="historyList"></ul>
  <div id="historyEmpty" class="empty">Журнал пуст</div>
  <div class="drawer-foot">
    <button id="clearHistory">Очистить журнал</button>
  </div>
</div>
<script>
const exprEl = document.getElementById('expr');
const resultEl = document.getElementById('result');
const historyList = document.getElementById('historyList');
const historyEmpty = document.getElementById('historyEmpty');
const overlay = document.getElementById('historyOverlay');
const drawer = document.getElementById('historyDrawer');
let history = [];
try { history = JSON.parse(localStorage.getItem('calc_history') || '[]'); } catch(e) { history = []; }

function openHistory() { renderHistory(); drawer.classList.add('open'); overlay.classList.remove('hidden'); }
function closeHistoryPanel() {
  drawer.classList.remove('open');
  overlay.classList.add('hidden');
  // Только возвращаем фокус к калькулятору, выражение не трогаем.
  try { exprEl.focus({preventScroll: true}); } catch(e) { exprEl.focus(); }
  try { exprEl.setSelectionRange(exprEl.value.length, exprEl.value.length); } catch(e) {}
}

function saveHistory() {
  try { localStorage.setItem('calc_history', JSON.stringify(history.slice(0, 50))); } catch(e) {}
}
function renderHistory() {
  historyList.innerHTML = '';
  historyEmpty.style.display = history.length ? 'none' : 'block';
  historyList.style.display = history.length ? 'flex' : 'none';
  history.forEach((h) => {
    const li = document.createElement('li');
    const s1 = document.createElement('span'); s1.className = 'hexpr'; s1.textContent = h.expression;
    const s2 = document.createElement('span'); s2.className = 'hres'; s2.textContent = '= ' + h.result;
    li.appendChild(s1); li.appendChild(s2);
    li.title = 'Нажмите, чтобы вернуть в ввод';
    li.onclick = () => {
      exprEl.value = h.expression;
      closeHistoryPanel();
    };
    historyList.appendChild(li);
  });
}
function showPreview(text, ok) {
  resultEl.textContent = text;
  resultEl.className = ok ? 'ok' : 'err';
}
function pushHistory(expression, result) {
  history.unshift({expression, result});
  history = history.slice(0, 50);
  saveHistory(); renderHistory();
}
async function doEquals() {
  const expression = exprEl.value.trim();
  if (!expression) { showPreview('0', true); return; }
  try {
    const resp = await fetch('/api/calculate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expression})
    });
    const data = await resp.json();
    if (data.ok) {
      showPreview(data.formatted, true);
      pushHistory(expression, data.formatted);
      exprEl.value = data.formatted;
    } else {
      showPreview(data.error || 'Ошибка', false);
    }
  } catch (e) {
    // Запасной вариант: грубая клиентская проверка, если нет связи с сервером
    showPreview('Нет связи с сервером', false);
  }
}
document.getElementById('keys').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.act === 'clear') { exprEl.value = ''; showPreview('0', true); exprEl.focus(); }
  else if (b.dataset.act === 'back') { exprEl.value = exprEl.value.slice(0, -1); exprEl.focus(); }
  else if (b.dataset.act === 'eq') { doEquals(); }
  else if (b.dataset.ins) {
    const s = exprEl.selectionStart ?? exprEl.value.length;
    const en = exprEl.selectionEnd ?? exprEl.value.length;
    exprEl.value = exprEl.value.slice(0, s) + b.dataset.ins + exprEl.value.slice(en);
    exprEl.focus();
  }
});
exprEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); doEquals(); }
  else if (e.key === 'Escape') {
    if (drawer.classList.contains('open')) { closeHistoryPanel(); }
    else { exprEl.value = ''; showPreview('0', true); }
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && drawer.classList.contains('open')) { closeHistoryPanel(); }
});
document.getElementById('clearHistory').onclick = () => {
  history = []; saveHistory(); renderHistory();
};
document.getElementById('openHistory').onclick = openHistory;
document.getElementById('closeHistory').onclick = closeHistoryPanel;
overlay.addEventListener('click', () => closeHistoryPanel());
// Свайп влево по drawer закрывает его (жест как в видео)
let _touchX = null;
drawer.addEventListener('touchstart', (e) => {
  if (e.touches && e.touches.length === 1) { _touchX = e.touches[0].clientX; }
}, {passive: true});
drawer.addEventListener('touchend', (e) => {
  if (_touchX === null) return;
  const endX = (e.changedTouches && e.changedTouches[0].clientX) ?? _touchX;
  if (_touchX - endX > 40) { closeHistoryPanel(); }
  _touchX = null;
}, {passive: true});
exprEl.focus();
renderHistory();
</script>
</body>
</html>
"""


class CalculatorHandler(BaseHTTPRequestHandler):
    """HTTP-обработчик GUI калькулятора."""

    server_version = "CalcGUI/1.0"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html()
        elif path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"ok": False, "error": "Не найдено"}, status=404)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/calculate":
            self._send_json({"ok": False, "error": "Не найдено"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "error": "Ошибка ввода: некорректный JSON."}, status=400)
            return
        expression = data.get("expression", "")
        self._send_json(evaluate_expression(expression))

    def log_message(self, fmt, *args):  # тихий лог в консоль
        print("  [http] " + fmt % args)


def run_gui(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    """Запускает графический интерфейс (веб-сервер)."""
    # Если порт занят — пробуем следующие
    server = None
    last_err = None
    for p in range(port, port + 10):
        try:
            server = HTTPServer((host, p), CalculatorHandler)
            port = p
            break
        except OSError as exc:
            last_err = exc
            continue
    if server is None:
        raise RuntimeError(f"Не удалось занять порт {port}: {last_err}")
    url = f"http://{host}:{port}"
    print("=" * 46)
    print("           🧮  КАЛЬКУЛЯТОР (GUI)")
    print("=" * 46)
    print(f"  Откройте в браузере: {url}")
    print("  На телефоне в той же Wi-Fi сети — адрес punti LAN-IP:порт")
    print("  Остановка: Ctrl+C")
    print("-" * 46)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Сервер остановлен. До свидания!")
    finally:
        server.server_close()


def start_local_calc_server(host: str = "127.0.0.1", port: int = 0):
    """Запускает CalculatorHandler на loopback в фоновом потоке.

    Используется Flet entry point (main.py): WebView открывает
    ``http://127.0.0.1:<port>/``, а ``fetch('/api/calculate')`` из
    существующего HTML_PAGE продолжает работать БЕЗ интернета —
    запросы идут на localhost внутри устройства.

    Существующие run_gui()/CalculatorHandler/HTML_PAGE НЕ меняются.

    Returns:
        tuple (server, port): HTTPServer в daemon-потоке и реальный порт.
    """
    import threading

    server = HTTPServer((host, port), CalculatorHandler)
    real_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, real_port


def run_cli() -> None:
    """Простой консольный REPL (режим --cli)."""
    print("Калькулятор (консольный режим). Введите выражение или 'выход'.")
    history = CalculatorHistory()
    while True:
        try:
            expr = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nДо свидания!")
            break
        if expr.lower() in ("выход", "exit", "q", "quit"):
            print("До свидания!")
            break
        if not expr:
            continue
        res = evaluate_expression(expr)
        if res["ok"]:
            print(f"= {res['formatted']}")
            history.add(expr, res["formatted"])
        else:
            print(f"❌ {res['error']}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Графический калькулятор")
    parser.add_argument("--port", type=int, default=8000, help="Порт веб-интерфейса (по умолчанию 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="Хост (по умолчанию 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически")
    parser.add_argument("--cli", action="store_true", help="Консольный режим вместо GUI")
    args = parser.parse_args(argv)
    if args.cli:
        run_cli()
    else:
        run_gui(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
