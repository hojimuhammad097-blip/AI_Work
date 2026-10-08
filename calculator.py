"""Графический калькулятор.

Математическое ядро (безопасное вычисление выражений через AST) сохранено
из консольной версии. Вместо консольного меню теперь запускается
графический интерфейс: современная тёмная веб-страница, удобная на телефоне.

Запуск:
    python calculator.py            # сервер на http://127.0.0.1:8000
    python calculator.py --port 8080
    python calculator.py --cli      # старый консольный REPL (для тестов/SSH)

Кнопки интерфейса (стиль Samsung, эталоны Ali2/Ali3):
  дисплей: выражение крупно (операторы зелёные), живой результат ниже
  считается автоматически до "="; внизу дисплея иконки: история,
  память, доп. функции, Backspace (зелёный, справа).
Раскладка сетки 5x4:
  C () % ÷
  7 8 9 ×
  4 5 6 −
  1 2 3 +
  +/− 0 , =
Одна кнопка «()» (умный выбор скобки), запятая как разделитель,
+/− меняет знак последнего числа, "=" в правом нижнем углу.
Одна широкая кнопка «История» сразу под дисплеем.
Память: MC/MR/M+/M− (панель, хранится локально).
Доп. функции: π, x², √, 1/x (панель).
История — панель поверх цифровой части (правая колонка операторов
÷ × − + остаётся видимой и нажимаемой).
Есть обработка ошибок (деление на ноль, неправильные выражения).
Неполное выражение живого результата не показывает.

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
        'π' -> '(3.14159265358979)',
        запятая -> точка (десятичный разделитель).
    Символ '%' сохраняется как есть — его раскрывает expand_percent().
    """
    if expression is None:
        return ""
    text = str(expression)
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-")
    text = text.replace("π", "(3.14159265358979)")
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
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Калькулятор</title>
<style>
  :root {
    --bg: #0f172a;
    --card: #1e293b;
    --display: #060d1d;
    --btn: #2b3649;
    --btn-hover: #3b4a63;
    --btn-op: #22c55e;
    --btn-op-hover: #4ade80;
    --op-bright-bg: #14532d;
    --op-bright-bg-hover: #166534;
    --op-dim-bg: #0f2a1f;
    --op-dim-bg-hover: #143a2a;
    --eq-bg: #2bff7e;
    --eq-bg-hover: #5cff96;
    --grey-op-bg: #a6abb2;
    --grey-op-bg-hover: #bcc1c7;
    --grey-op-ink: #14181f;
    --btn-danger: #ef4444;
    --btn-danger-hover: #f87171;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #22d3ee;
    --radius: 18px;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html { height: 100%; }
  body {
    margin: 0; min-height: 100vh; min-height: 100dvh; height: 100dvh; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    display: flex; justify-content: center; align-items: stretch;
    padding: 8px;
    padding-top: max(8px, env(safe-area-inset-top));
    padding-bottom: max(8px, env(safe-area-inset-bottom));
    padding-left: max(8px, env(safe-area-inset-left));
    padding-right: max(8px, env(safe-area-inset-right));
    touch-action: manipulation;
    overscroll-behavior: none;
  }
  .app { width: 100%; max-width: 430px; margin: 0 auto; display: flex; flex-direction: column; gap: 8px;
         height: 100%; min-height: 0; justify-content: flex-start; flex: 1 1 auto; }
  #openHistory {
    width: 100%; min-height: 50px;
    display: flex; align-items: center; justify-content: center; gap: 10px;
    background: #1a2438; border: 1px solid #334155; color: var(--text);
    border-radius: 14px; font-size: 1.1rem; font-weight: 700; line-height: 1;
    cursor: pointer; touch-action: manipulation; padding: 12px;
    position: relative; z-index: 45; flex: 0 0 auto;
  }
  #openHistory:hover { border-color: var(--accent); }
  #openHistory:active { transform: scale(.99); }
  #openHistory .hico {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%;
    background: #f1f5f9; color: #0f172a; font-size: 1rem; line-height: 1; flex: 0 0 auto;
  }
  /* Внешняя карточка убрана (как в Ali): прозрачная, без подложки */
  .card { background: transparent; border-radius: 0; padding: 0;
          box-shadow: none;
          flex: 1 1 auto; display: flex; flex-direction: column; gap: 8px; min-height: 0; height: 100%; }
  .display {
    background: var(--display); border-radius: 20px;
    padding: 16px 16px 10px; min-height: 128px;
    display: flex; flex-direction: column; justify-content: flex-end; gap: 2px;
    overflow: hidden; flex-shrink: 1; flex: 1 1 auto;
  }
  /* Строка выражения (Ali2): крупная, операторы зелёные, курсор справа */
  #exprView {
    width: 100%; color: var(--text); font-size: 3rem; font-weight: 700;
    text-align: right; min-height: 1.25em; line-height: 1.15;
    word-break: break-all; user-select: none; -webkit-user-select: none;
  }
  #exprView .placeholder { color: #475569; font-weight: 500; }
  #exprView .tok-op { color: #2bff7e; }
  #exprView .caret::after {
    content: ''; display: inline-block; width: 3px; height: 0.95em;
    background: #2bff7e; margin-left: 3px; vertical-align: -0.12em;
    animation: blink 1.1s steps(1) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
  /* Живой результат под выражением (Ali2: серый, меньше) */
  #result { font-size: 1.5rem; font-weight: 500; text-align: right; color: var(--muted);
            min-height: 1.4em; word-break: break-all; line-height: 1.2; }
  #result.ok { color: var(--muted); }
  #result.err { color: var(--btn-danger-hover); font-size: 1.05rem; font-weight: 600; }
  /* Иконки функций внизу дисплея (Ali3): история, память, доп. функции, Backspace */
  .display-tools {
    display: flex; align-items: center; gap: 4px;
    padding-top: 6px; margin-top: 2px;
  }
  button.tool {
    border: none; background: transparent; color: #e2e8f0;
    width: 48px; height: 40px; min-width: 48px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.45rem; font-weight: 500; cursor: pointer;
    border-radius: 12px; touch-action: manipulation; user-select: none;
    -webkit-user-select: none; padding: 0;
  }
  button.tool:hover { background: rgba(148,163,184,.12); }
  button.tool:active { transform: scale(.93); }
  button.tool .fx-ico { font-size: 1.05rem; font-weight: 700; letter-spacing: -0.5px; }
  button.tool.back {
    margin-left: auto; color: #2bff7e;
    border: 2px solid #2bff7e; border-radius: 12px;
    width: 52px; height: 38px; min-width: 52px; font-size: 1.3rem;
  }
  button.tool.back:hover { background: rgba(43,255,126,.12); }
  #memBadge {
    color: #0f172a; background: #e2e8f0; border-radius: 6px;
    font-size: 0.75rem; font-weight: 800; padding: 1px 6px; margin-left: 2px;
  }
  #memBadge[hidden] { display: none; }
  /* Зона клавиатуры: естественная высота (кнопки НЕ растягиваются),
     прижата к низу; свободную высоту забирает дисплей выше.
     Порядок как в Ali: дисплей -> History -> отступ 8px -> C() % ... -> = внизу. */
  .board {
    position: relative; flex: 0 0 auto;
    display: flex; flex-direction: column;
  }
  .grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    grid-template-rows: repeat(5, auto);
    gap: 8px; margin-top: 0; padding-top: 0;
    position: relative; z-index: 45;
    flex: 0 0 auto; height: auto;
  }
  button.key {
    border: none; border-radius: 32px; background: var(--btn); color: var(--text);
    font-size: 1.65rem; font-weight: 700; cursor: pointer;
    height: 64px; min-height: 64px; touch-action: manipulation; user-select: none;
    -webkit-user-select: none; -webkit-touch-callout: none;
    transition: transform .05s ease, background .15s ease;
    display: flex; align-items: center; justify-content: center;
    padding: 0;
  }
  button.key:hover { background: var(--btn-hover); }
  button.key:active { transform: scale(.96); }
  button.op, button.op-bright { background: var(--op-bright-bg); color: #4ade80; font-size: 1.65rem; }
  button.op:hover, button.op-bright:hover { background: var(--op-bright-bg-hover); }
  button.op-dim { background: var(--op-dim-bg); color: #e6f4ec; font-size: 1.5rem; }
  button.op-dim:hover { background: var(--op-dim-bg-hover); }
  /* Правая колонка Ali2 (÷ × − +): светло-серая, тёмный текст */
  button.op-grey { background: var(--grey-op-bg); color: var(--grey-op-ink); font-size: 1.7rem; }
  button.op-grey:hover { background: var(--grey-op-bg-hover); }
  /* = в правом нижнем углу (Ali2): обычная ячейка, ярко-зелёная */
  button.eq { background: var(--eq-bg); color: #ffffff; font-size: 1.9rem; font-weight: 800;
              height: 64px; min-height: 64px; border-radius: 32px; }
  button.eq:hover { background: var(--eq-bg-hover); }
  button.danger { color: #ff8fa0; }
  /* История — плавающая панель НИЖЕ дисплея: закрывает цифровую часть,
     правая колонка % ÷ × − + остаётся видимой и кликабельной.
     Сетка (.grid) поднята над затемнением, drawer уже ~75%. */
  .scrim {
    position: absolute; inset: 0; background: rgba(2,6,23,.25);
    z-index: 40; border-radius: 16px;
  }
  .scrim.hidden { display: none; }
  .drawer {
    position: absolute; top: 0; left: 0; bottom: 0;
    width: calc(75% - 2px);
    background: #1e2c47; border: 1px solid rgba(148,163,184,.25);
    border-radius: 18px;
    padding: 12px;
    box-shadow: 20px 0 60px rgba(0,0,0,.55), 2px 0 12px rgba(0,0,0,.4);
    z-index: 50;
    transform: translateX(-108%);
    visibility: hidden; pointer-events: none;
    transition: transform .25s ease, visibility .25s ease;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .drawer.open { transform: translateX(0); visibility: visible; pointer-events: auto; }
  /* Всплывающие панели памяти и доп. функций (Ali3): поверх клавиатуры */
  .sheet {
    position: absolute; top: 0; left: 0; right: 0;
    background: #1e2c47; border: 1px solid rgba(148,163,184,.25);
    border-radius: 18px; padding: 12px;
    box-shadow: 0 18px 50px rgba(0,0,0,.55);
    z-index: 60;
    display: flex; flex-direction: column; gap: 8px;
  }
  .sheet.hidden { display: none; }
  .sheet-title { color: var(--muted); font-size: 0.85rem; font-weight: 700;
                 display: flex; align-items: center; justify-content: space-between; }
  .sheet-title button {
    border: 1px solid var(--btn-hover); background: #0b1220; color: var(--text);
    border-radius: 10px; width: 32px; height: 32px; font-size: 1rem; font-weight: 700;
    cursor: pointer; touch-action: manipulation;
  }
  .sheet-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  button.fnkey {
    border: none; border-radius: 16px; background: var(--btn); color: var(--text);
    font-size: 1.15rem; font-weight: 700; cursor: pointer;
    height: 52px; min-height: 52px; touch-action: manipulation; user-select: none;
    -webkit-user-select: none; display: flex; align-items: center; justify-content: center;
  }
  button.fnkey:hover { background: var(--btn-hover); }
  button.fnkey:active { transform: scale(.96); }
  button.fnkey.hl { background: var(--op-bright-bg); color: #4ade80; }
  #closeHistory {
    align-self: flex-end;
    width: 34px; height: 34px; min-width: 34px;
    display: flex; align-items: center; justify-content: center;
    background: #0b1220; border: 1px solid var(--btn-hover); color: var(--text);
    border-radius: 10px; font-size: 1.05rem; font-weight: 700; line-height: 1;
    cursor: pointer; touch-action: manipulation; padding: 0; margin-bottom: 8px;
  }
  #closeHistory:hover { border-color: var(--accent); }
  #closeHistory:active { transform: scale(.94); }
  .drawer-foot { display: flex; gap: 10px; margin-top: 12px; }
  #clearHistory {
    flex: 1 1 0; min-height: 50px;
    border-radius: 14px; font-size: 1rem; font-weight: 700; cursor: pointer;
    touch-action: manipulation;
    display: flex; align-items: center; justify-content: center; gap: 8px;
    background: transparent; border: 1px solid #475569; color: var(--text);
  }
  #clearHistory:hover { border-color: var(--muted); }
  #historyList { list-style: none; margin: 2px 0 0; padding: 2px;
                 display: flex; flex-direction: column; gap: 8px;
                 overflow-y: auto; overscroll-behavior: contain;
                 -webkit-overflow-scrolling: touch; touch-action: pan-y;
                 flex: 1 1 auto; min-height: 0; }
  #historyList li {
    background: #0b1220; border: 1px solid rgba(148,163,184,.14);
    border-radius: 14px; padding: 10px 12px;
    font-size: 1rem; cursor: pointer;
    display: flex; flex-direction: row; align-items: center; justify-content: space-between; gap: 10px;
    white-space: nowrap;
  }
  #historyList li:hover { border-color: var(--btn-hover); }
  #historyList li:active { background: #16213a; transform: scale(.99); }
  #historyList .hexpr { color: #cbd5e1; font-size: 1rem; font-weight: 500; overflow: hidden; text-overflow: ellipsis; flex: 1 1 auto; text-align: left; }
  #historyList .hres { color: var(--text); font-size: 1.15rem; font-weight: 800; white-space: nowrap; flex: 0 0 auto; text-align: right; }
  .empty { color: var(--muted); text-align: center; font-size: 0.85rem; padding: 12px 8px; }
  @media (max-width: 380px) {
    button.key { font-size: 1.45rem; height: 58px; min-height: 58px; }
    #exprView { font-size: 2.5rem; }
    .display { min-height: 112px; }
  }
  @media (min-height: 700px) {
    button.key { height: 68px; min-height: 68px; }
  }
  @media (max-height: 640px) {
    button.key { height: 54px; min-height: 54px; font-size: 1.4rem; }
    .display { min-height: 96px; padding: 12px 14px 8px; }
    #exprView { font-size: 2.2rem; }
    #result { font-size: 1.2rem; }
  }
</style>
</head>
<body>
<div class="app">
  <div class="card">
    <div class="display">
      <div id="exprView" aria-label="Выражение"><span class="placeholder">0</span><span class="caret"></span></div>
      <input type="hidden" id="expr" value="">
      <div id="result" class="ok"></div>
      <div class="display-tools">
        <button class="tool" id="toolHistory" aria-label="История" title="История">◷</button>
        <button class="tool" id="toolMemory" aria-label="Память" title="Память (MC, MR, M+, M−)">&#9646;</button>
        <button class="tool" id="toolFx" aria-label="Дополнительные функции" title="Дополнительные функции"><span class="fx-ico">x&#960;</span></button>
        <span id="memBadge" hidden>M</span>
        <button class="tool back" id="toolBack" data-act="back" aria-label="Удалить символ" title="Backspace">⌫</button>
      </div>
    </div>
    <button id="openHistory" aria-label="Открыть историю" aria-haspopup="dialog" title="История"><span class="hico">◷</span> История</button>
    <div class="board">
      <div class="grid" id="keys">
        <button class="key danger" data-act="clear">C</button>
        <button class="key op-dim" data-act="parens">()</button>
        <button class="key op-bright" data-ins="%">%</button>
        <button class="key op-grey" data-ins="÷">÷</button>
        <button class="key" data-ins="7">7</button>
        <button class="key" data-ins="8">8</button>
        <button class="key" data-ins="9">9</button>
        <button class="key op-grey" data-ins="×">×</button>
        <button class="key" data-ins="4">4</button>
        <button class="key" data-ins="5">5</button>
        <button class="key" data-ins="6">6</button>
        <button class="key op-grey" data-ins="−">−</button>
        <button class="key" data-ins="1">1</button>
        <button class="key" data-ins="2">2</button>
        <button class="key" data-ins="3">3</button>
        <button class="key op-grey" data-ins="+">+</button>
        <button class="key" data-act="neg">+/−</button>
        <button class="key" data-ins="0">0</button>
        <button class="key" data-ins=",">,</button>
        <button class="key eq" data-act="eq">=</button>
      </div>
      <div id="historyOverlay" class="scrim hidden"></div>
      <div id="historyDrawer" class="drawer" role="dialog" aria-modal="true" aria-label="История вычислений">
        <button id="closeHistory" aria-label="Закрыть историю">✕</button>
        <ul id="historyList"></ul>
        <div id="historyEmpty" class="empty">Журнал пуст</div>
        <div class="drawer-foot">
          <button id="clearHistory">🗑 Очистить историю</button>
        </div>
      </div>
      <div id="memPanel" class="sheet hidden" role="dialog" aria-label="Память">
        <div class="sheet-title"><span>Память (MC, MR, M+, M−)</span><button id="closeMem" aria-label="Закрыть память">✕</button></div>
        <div class="sheet-grid">
          <button class="fnkey" data-mem="mc">MC</button>
          <button class="fnkey" data-mem="mr">MR</button>
          <button class="fnkey hl" data-mem="mplus">M+</button>
          <button class="fnkey hl" data-mem="mminus">M−</button>
        </div>
      </div>
      <div id="fxPanel" class="sheet hidden" role="dialog" aria-label="Дополнительные функции">
        <div class="sheet-title"><span>Функции: π, x², √, 1/x</span><button id="closeFx" aria-label="Закрыть функции">✕</button></div>
        <div class="sheet-grid">
          <button class="fnkey hl" data-ins="π">π</button>
          <button class="fnkey" data-fx="square">x²</button>
          <button class="fnkey" data-fx="sqrt">√</button>
          <button class="fnkey" data-fx="recip">1/x</button>
        </div>
      </div>
    </div>
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

function openHistory() { try { closeSheets(); } catch(e) {} renderHistory(); drawer.classList.add('open'); overlay.classList.remove('hidden'); }
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
const exprView = document.getElementById('exprView');
let freshResult = false; // после "=" цифры начинают новое выражение (как Samsung)

/* Дисплей Ali2: выражение крупно, операторы зелёные, курсор справа */
function renderExpr() {
  const v = exprEl.value;
  exprView.innerHTML = '';
  if (!v) {
    const ph = document.createElement('span'); ph.className = 'placeholder'; ph.textContent = '0';
    exprView.appendChild(ph);
  } else {
    for (const ch of v) {
      const s = document.createElement('span');
      if ('+-*/×÷−%'.includes(ch)) { s.className = 'tok-op'; }
      s.textContent = ch;
      exprView.appendChild(s);
    }
  }
  const c = document.createElement('span'); c.className = 'caret';
  exprView.appendChild(c);
}

/* Живой результат ДО "=" (Ali2: 45+45 -> 90). Неполное/ошибочное
   выражение показывает пусто (без ложного результата), в историю не пишется. */
let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 120);
}
async function updatePreview() {
  const expression = exprEl.value.trim();
  if (!expression) { resultEl.textContent = ''; resultEl.className = 'ok'; return; }
  try {
    const resp = await fetch('/api/calculate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expression})
    });
    const data = await resp.json();
    if (exprEl.value.trim() !== expression) { return; } // ввод уже изменился
    if (data.ok) { resultEl.textContent = data.formatted; resultEl.className = 'ok'; }
    else { resultEl.textContent = ''; resultEl.className = 'ok'; }
  } catch (e) { /* офлайн: оставляем старый preview */ }
}
function afterInput() { renderExpr(); schedulePreview(); }

/* Вычисление значения текущего выражения через сервер (для M+/M−/√) */
async function evalCurrent() {
  const expression = exprEl.value.trim();
  if (!expression) { return null; }
  try {
    const resp = await fetch('/api/calculate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expression})
    });
    const data = await resp.json();
    if (data.ok) { return data.result; }
  } catch (e) {}
  const n = parseFloat(expression.replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}
function fmtNum(v) { return String(parseFloat(Number(v).toPrecision(12))); }

async function doEquals() {
  const expression = exprEl.value.trim();
  if (!expression) { showPreview('', true); return; }
  try {
    const resp = await fetch('/api/calculate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expression})
    });
    const data = await resp.json();
    if (data.ok) {
      // В историю пишется только по "="; живой preview туда не попадает.
      pushHistory(expression, data.formatted);
      exprEl.value = data.formatted;
      freshResult = true;
      renderExpr();
      resultEl.textContent = ''; resultEl.className = 'ok';
    } else {
      showPreview(data.error || 'Ошибка', false);
    }
  } catch (e) {
    // Запасной вариант: грубая клиентская проверка, если нет связи с сервером
    showPreview('Нет связи с сервером', false);
  }
}
/* Одна кнопка "()" (Ali2): умный выбор скобки по балансу */
function smartParens() {
  const v = exprEl.value;
  const open = (v.match(/\(/g) || []).length;
  const close = (v.match(/\)/g) || []).length;
  const last = v.slice(-1);
  if (v === '' || '+-*/×÷−%('.includes(last)) { exprEl.value = v + '('; }
  else if (open > close) { exprEl.value = v + ')'; }
  else { exprEl.value = v + '('; }
}
/* +/−: смена знака последнего числа (45+45 -> 45+−45 = 0) */
function toggleSign() {
  const v = exprEl.value;
  if (v === '') { exprEl.value = '-'; return; }
  const m = v.match(/(-?\d+[.,]?\d*)$/);
  if (!m) {
    if (/[+\-*/×÷−%(]$/.test(v)) { exprEl.value = v + '-'; }
    return;
  }
  const num = m[1];
  if (num.startsWith('-')) { exprEl.value = v.slice(0, -num.length) + num.slice(1); }
  else { exprEl.value = v.slice(0, -num.length) + '-' + num; }
}
/* Ввод с учётом freshResult: после "=" цифра начинает новое выражение */
function appendText(t) {
  if (freshResult && /^[0-9,(π]/.test(t)) { exprEl.value = t; }
  else { exprEl.value = exprEl.value + t; }
  freshResult = false;
}
function doBackspace() {
  exprEl.value = exprEl.value.slice(0, -1);
  freshResult = false;
  afterInput();
}
function hideKeyboard() {
  // Убираем фокус с кнопок/поля, чтобы Android спрятал soft keyboard,
  // если он был открыт. Дисплей readonly, поэтому focus() сам по себе
  // клавиатуру не открывает, но blur — надёжная страховка.
  try { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); } catch(e) {}
}
document.getElementById('keys').addEventListener('click', (e) => {
  // Нативный клик без preventDefault: цепочка tap (touchstart->touchend->click)
  // в Android WebView должна доходить до обработчика целиком.
  // Фокус тут же снимаем через blur(), системная клавиатура подавлена
  // (состояние в скрытом поле, ввод программный), поэтому перехват touchstart не нужен.
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.act === 'clear') { exprEl.value = ''; freshResult = false; resultEl.textContent = ''; resultEl.className = 'ok'; renderExpr(); }
  else if (b.dataset.act === 'parens') { smartParens(); freshResult = false; afterInput(); }
  else if (b.dataset.act === 'neg') { toggleSign(); freshResult = false; afterInput(); }
  else if (b.dataset.act === 'eq') { doEquals(); }
  else if (b.dataset.ins) {
    appendText(b.dataset.ins); afterInput();
  }
  try { b.blur(); } catch(err) {}
  hideKeyboard();
});
// ВАЖНО (Android WebView): здесь НЕ должно быть preventDefault() на
// 'mousedown'/'touchstart' для кнопок — отмена touchstart гасит весь tap
// (touchend -> click не наступает) и кнопки перестают нажиматься пальцем.
// Раньше такой перехват стоял и был причиной мёртвых кнопок в APK.
// Фокус/клавиатура уже подавлены: состояние в скрытом поле, после клика делаем blur().
;
exprEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); doEquals(); }
  else if (e.key === 'Escape') {
    if (drawer.classList.contains('open')) { closeHistoryPanel(); }
    else if (!memPanel.classList.contains('hidden') || !fxPanel.classList.contains('hidden')) { closeSheets(); }
    else { exprEl.value = ''; freshResult = false; renderExpr(); resultEl.textContent = ''; resultEl.className = 'ok'; }
  }
});
// Физ. клавиатура: символы не печатаются сами (состояние в скрытом поле),
// поэтому вставляем вручную. Soft keyboard не открывается.
document.addEventListener('keydown', (e) => {
  if (drawer.classList.contains('open')) { return; }
  if (!memPanel.classList.contains('hidden') || !fxPanel.classList.contains('hidden')) { return; }
  if (e.ctrlKey || e.metaKey || e.altKey) { return; }
  const t = e.target;
  if (t && (t.tagName === 'TEXTAREA' || t.closest?.('#historyDrawer'))) { return; }
  if (e.key === 'Enter') { e.preventDefault(); doEquals(); return; }
  if (e.key === 'Backspace') { e.preventDefault(); doBackspace(); return; }
  if (e.key === 'Escape') { return; } // уже обработан выше
  if (e.key && e.key.length === 1 && '0123456789+-*/%.(),×÷− '.includes(e.key)) {
    e.preventDefault();
    appendText(e.key); afterInput();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && drawer.classList.contains('open')) { closeHistoryPanel(); }
  else if (e.key === 'Escape' && (!memPanel.classList.contains('hidden') || !fxPanel.classList.contains('hidden'))) { closeSheets(); }
});
document.getElementById('clearHistory').onclick = () => {
  history = []; saveHistory(); renderHistory();
};
document.getElementById('openHistory').onclick = openHistory;
document.getElementById('closeHistory').onclick = closeHistoryPanel;
overlay.addEventListener('click', () => closeHistoryPanel());

/* --- Функциональные кнопки дисплея (Ali3): история, память, доп. функции --- */
const memPanel = document.getElementById('memPanel');
const fxPanel = document.getElementById('fxPanel');
const memBadge = document.getElementById('memBadge');
function closeSheets() {
  memPanel.classList.add('hidden');
  fxPanel.classList.add('hidden');
}
function toggleSheet(panel) {
  const wasHidden = panel.classList.contains('hidden');
  closeSheets();
  closeHistoryPanel();
  if (wasHidden) { panel.classList.remove('hidden'); }
  try { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); } catch(e) {}
}
document.getElementById('toolHistory').onclick = () => { openHistory(); };
document.getElementById('toolMemory').onclick = () => toggleSheet(memPanel);
document.getElementById('toolFx').onclick = () => toggleSheet(fxPanel);
document.getElementById('closeMem').onclick = closeSheets;
document.getElementById('closeFx').onclick = closeSheets;
document.getElementById('toolBack').onclick = (e) => {
  doBackspace();
  try { e.currentTarget.blur(); } catch(err) {}
  hideKeyboard();
};

/* --- Память MC/MR/M+/M− (Ali3), хранится локально --- */
let memVal = null;
try { const m = JSON.parse(localStorage.getItem('calc_mem')); if (typeof m === 'number' && Number.isFinite(m)) { memVal = m; } } catch(e) { memVal = null; }
function saveMem() { try { localStorage.setItem('calc_mem', JSON.stringify(memVal)); } catch(e) {} }
function renderMem() { memBadge.hidden = (memVal === null); }
async function memOp(op) {
  if (op === 'mc') { memVal = null; saveMem(); renderMem(); return; }
  if (op === 'mr') {
    if (memVal !== null) {
      const t = fmtNum(memVal);
      if (freshResult || !exprEl.value) { exprEl.value = t; } else { exprEl.value = exprEl.value + t; }
      freshResult = false; afterInput();
    }
    closeSheets(); return;
  }
  const v = await evalCurrent();
  if (v === null || !Number.isFinite(v)) { return; }
  if (op === 'mplus') { memVal = (memVal || 0) + v; }
  else { memVal = (memVal || 0) - v; }
  saveMem(); renderMem();
}
memPanel.addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.id === 'closeMem') { return; }
  if (b.dataset.mem) { memOp(b.dataset.mem); }
  try { b.blur(); } catch(err) {}
});

/* --- Доп. функции π, x², √, 1/x (Ali3) --- */
async function fxOp(op) {
  if (op === 'square') {
    const ex = exprEl.value.trim();
    if (!ex) { return; }
    exprEl.value = '(' + ex + ')*(' + ex + ')';
    freshResult = false; afterInput();
  } else if (op === 'recip') {
    const ex = exprEl.value.trim();
    if (!ex) { return; }
    exprEl.value = '(1)/(' + ex + ')';
    freshResult = false; afterInput();
  } else if (op === 'sqrt') {
    const v = await evalCurrent();
    if (v === null || !Number.isFinite(v)) { return; }
    if (v < 0) { showPreview('Ошибка ввода: корень из отрицательного числа', false); return; }
    exprEl.value = fmtNum(Math.sqrt(v));
    freshResult = false; afterInput();
  }
  closeSheets();
}
fxPanel.addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.id === 'closeFx') { return; }
  if (b.dataset.ins) { appendText(b.dataset.ins); afterInput(); closeSheets(); }
  else if (b.dataset.fx) { fxOp(b.dataset.fx); }
  try { b.blur(); } catch(err) {}
});
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
// Стартовый фокус НЕ ставим: WebView не должен автофокусировать
// текстовое поле при запуске, иначе Android сразу покажет keyboard.
// Дисплей и так readonly, но отсутствие autofocus — требование задачи.
if (document.activeElement && document.activeElement.blur) { try { document.activeElement.blur(); } catch(e) {} }
renderExpr();
renderMem();
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
