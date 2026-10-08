"""Автоматические тесты для calculator.py (математика + GUI-хелперы)."""

import json
import threading
import unittest
import urllib.request
from http.server import HTTPServer

from calculator import (
    HTML_PAGE,
    CalculatorHandler,
    CalculatorHistory,
    add,
    calculate,
    divide,
    evaluate_expression,
    expand_percent,
    format_result,
    multiply,
    normalize_expression,
    subtract,
)


class TestBasicOperations(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
        self.assertAlmostEqual(add(2.5, 3.2), 5.7)

    def test_subtract(self):
        self.assertEqual(subtract(5, 3), 2)
        self.assertAlmostEqual(subtract(2.5, 5), -2.5)

    def test_multiply(self):
        self.assertEqual(multiply(3, 4), 12)
        self.assertAlmostEqual(multiply(2.5, 4), 10.0)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5)
        self.assertAlmostEqual(divide(7, 2), 3.5)

    def test_divide_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            divide(5, 0)


class TestCalculate(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(calculate("2 + 3"), 5)
        self.assertEqual(calculate("5 - 8"), -3)
        self.assertEqual(calculate("3 * 4"), 12)
        self.assertEqual(calculate("10 / 2"), 5)

    def test_priority(self):
        # Умножение/деление имеют приоритет над сложением
        self.assertEqual(calculate("2 + 3 * 4"), 14)
        self.assertEqual(calculate("10 - 8 / 2"), 6)

    def test_parentheses(self):
        self.assertEqual(calculate("(2 + 3) * 4"), 20)
        self.assertEqual(calculate("((1 + 2) * (3 + 4)) / 7"), 3)
        self.assertEqual(calculate("((2))"), 2)

    def test_decimals(self):
        self.assertAlmostEqual(calculate("2.5 + 3.2"), 5.7)
        self.assertAlmostEqual(calculate("10 / 4"), 2.5)
        self.assertAlmostEqual(calculate("2,5 + 1"), 3.5)  # запятая как разделитель

    def test_unary(self):
        self.assertEqual(calculate("-5 + 3"), -2)
        self.assertEqual(calculate("+-5"), -5)
        self.assertEqual(calculate("--5"), 5)

    def test_spaces(self):
        self.assertEqual(calculate("  2   +   3 "), 5)

    def test_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            calculate("5 / 0")
        with self.assertRaises(ZeroDivisionError):
            calculate("(2 + 3) / (4 - 4)")
        with self.assertRaises(ZeroDivisionError):
            calculate("1 / (2 - 2)")

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            calculate("")
        with self.assertRaises(ValueError):
            calculate("   ")
        with self.assertRaises(ValueError):
            calculate("2 + abc")
        with self.assertRaises(ValueError):
            calculate("2 ** 3")  # степень запрещена
        with self.assertRaises(ValueError):
            calculate("__import__('os')")
        with self.assertRaises(ValueError):
            calculate("(2 + 3")  # незакрытая скобка
        with self.assertRaises(ValueError):
            calculate("2 + * 3")


class TestNormalizeExpression(unittest.TestCase):
    def test_gui_symbols(self):
        self.assertEqual(normalize_expression("2 × 3"), "2 * 3")
        self.assertEqual(normalize_expression("8 ÷ 2"), "8 / 2")
        self.assertEqual(normalize_expression("5 − 3"), "5 - 3")  # U+2212
        self.assertEqual(normalize_expression("2,5"), "2.5")

    def test_none_gives_empty(self):
        self.assertEqual(normalize_expression(None), "")


class TestUnicodeOperators(unittest.TestCase):
    """Кнопки GUI × ÷ − должны вычисляться так же, как * / -."""

    def test_multiply_sign(self):
        self.assertEqual(calculate("3 × 4"), 12)
        self.assertEqual(calculate("(2 + 3) × 4"), 20)

    def test_divide_sign(self):
        self.assertEqual(calculate("8 ÷ 2"), 4)
        self.assertAlmostEqual(calculate("10 ÷ 4"), 2.5)

    def test_minus_sign(self):
        self.assertEqual(calculate("5 − 3"), 2)

    def test_mixed_gui_expression(self):
        self.assertEqual(calculate("(8 ÷ 2) × (5 − 3)"), 8)

    def test_gui_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            calculate("5 ÷ 0")


class TestPercent(unittest.TestCase):
    """Полноценная поддержка процентов."""

    def test_single_percent(self):
        self.assertAlmostEqual(calculate("50%"), 0.5)
        self.assertAlmostEqual(calculate("100%"), 1.0)
        self.assertAlmostEqual(calculate("12.5%"), 0.125)

    def test_multiply_percent(self):
        self.assertAlmostEqual(calculate("200*10%"), 20)
        self.assertAlmostEqual(calculate("200×10%"), 20)

    def test_divide_percent(self):
        self.assertAlmostEqual(calculate("200/10%"), 2000)
        self.assertAlmostEqual(calculate("200÷10%"), 2000)

    def test_add_percent(self):
        # 10% от 200 = 20, итого 220
        self.assertAlmostEqual(calculate("200+10%"), 220)
        self.assertAlmostEqual(calculate("50+10%"), 55)

    def test_subtract_percent(self):
        self.assertAlmostEqual(calculate("200-10%"), 180)
        self.assertAlmostEqual(calculate("200−10%"), 180)

    def test_percent_of_parentheses(self):
        self.assertAlmostEqual(calculate("(20+30)%"), 0.5)
        self.assertAlmostEqual(calculate("(2+3)*10%"), 0.5)

    def test_sum_of_percents(self):
        self.assertAlmostEqual(calculate("10%+20%"), 0.3)

    def test_percent_with_spaces(self):
        self.assertAlmostEqual(calculate("200 + 10%"), 220)

    def test_percent_with_decimals(self):
        self.assertAlmostEqual(calculate("200+12.5%"), 225)

    def test_invalid_percent(self):
        with self.assertRaises(ValueError):
            calculate("%")
        with self.assertRaises(ValueError):
            calculate("200+%")
        with self.assertRaises(ValueError):
            calculate("++%")

    def test_expand_percent_unit(self):
        self.assertEqual(expand_percent("50%"), "(50/100)")
        self.assertIn("200*10/100", expand_percent("200+10%"))
        # Скобки: (20+30)% -> (20+30)/100
        self.assertEqual(expand_percent("(20+30)%"), "(20+30)/100")

    def test_evaluate_percent(self):
        res = evaluate_expression("200+10%")
        self.assertTrue(res["ok"])
        self.assertEqual(res["formatted"], "220")
        bad = evaluate_expression("%")
        self.assertFalse(bad["ok"])


class TestEvaluateExpression(unittest.TestCase):
    """Обёртка для GUI: не бросает исключений, возвращает dict."""

    def test_ok(self):
        res = evaluate_expression("2 + 3 * 4")
        self.assertTrue(res["ok"])
        self.assertEqual(res["formatted"], "14")

    def test_ok_gui_symbols(self):
        res = evaluate_expression("3 × (4 − 1) ÷ 3")
        self.assertTrue(res["ok"])
        self.assertEqual(res["formatted"], "3")

    def test_division_by_zero(self):
        res = evaluate_expression("5 / 0")
        self.assertFalse(res["ok"])
        self.assertIn("ноль", res["error"].lower())

    def test_invalid(self):
        res = evaluate_expression("2 + * 3")
        self.assertFalse(res["ok"])
        self.assertIn("Ошибка", res["error"])

    def test_empty(self):
        res = evaluate_expression("   ")
        self.assertFalse(res["ok"])

    def test_never_raises(self):
        for bad in ["", None, "abc", "1/0", "(1+", "__import__('os')"]:
            res = evaluate_expression(bad)
            self.assertIn("ok", res)


class TestCalculatorHistory(unittest.TestCase):
    def test_add_and_order(self):
        h = CalculatorHistory(limit=3)
        h.add("2+2", "4")
        h.add("3*3", "9")
        items = h.get_all()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], {"expression": "3*3", "result": "9"})

    def test_limit(self):
        h = CalculatorHistory(limit=3)
        for i in range(5):
            h.add(str(i), str(i))
        self.assertEqual(len(h), 3)

    def test_clear(self):
        h = CalculatorHistory()
        h.add("1+1", "2")
        h.clear()
        self.assertEqual(len(h), 0)
        self.assertEqual(h.get_all(), [])


class TestFormatResult(unittest.TestCase):
    def test_integer(self):
        self.assertEqual(format_result(5.0), "5")
        self.assertEqual(format_result(5), "5")

    def test_float(self):
        self.assertEqual(format_result(2.5), "2.5")

    def test_float_artifacts(self):
        # 0.1 + 0.2 = 0.30000000000000004 без округления
        self.assertEqual(format_result(0.1 + 0.2), "0.3")

    def test_negative(self):
        self.assertEqual(format_result(-3.0), "-3")


class TestHttpApi(unittest.TestCase):
    """Интеграционный тест веб-GUI: сервер из стандартной библиотеки."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), CalculatorHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, expression):
        url = f"http://127.0.0.1:{self.port}/api/calculate"
        body = json.dumps({"expression": expression}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_api_ok(self):
        data = self._post("2 + 3 × 4")
        self.assertTrue(data["ok"])
        self.assertEqual(data["formatted"], "14")

    def test_api_division_by_zero(self):
        data = self._post("1 ÷ 0")
        self.assertFalse(data["ok"])

    def test_api_invalid(self):
        data = self._post("2 + * 3")
        self.assertFalse(data["ok"])

    def test_index_served(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("Калькулятор", html)
        self.assertIn('data-ins="÷"', html)
        self.assertIn('data-act="back"', html)
        # Новая вёрстка: кнопка %, маленькая иконка часов слева, LEFT DRAWER
        self.assertIn('data-ins="%"', html)
        self.assertIn('id="openHistory"', html)
        self.assertIn('id="historyDrawer"', html)
        self.assertIn('id="historyOverlay"', html)
        self.assertIn('id="closeHistory"', html)
        self.assertIn('id="clearHistory"', html)
        self.assertIn("Очистить журнал", html)

    def test_api_percent(self):
        data = self._post("200+10%")
        self.assertTrue(data["ok"])
        self.assertEqual(data["formatted"], "220")
        data2 = self._post("50%")
        self.assertTrue(data2["ok"])
        self.assertEqual(data2["formatted"], "0.5")


class TestHistoryPanel(unittest.TestCase):
    """Поведение истории как LEFT DRAWER (HTML/JS): маленькая иконка часов
    слева, выезд слева поверх кнопок, отображение, выбор, очистка,
    закрытие. Математика и проценты не затрагиваются."""

    def test_history_button_compact_on_top(self):
        # Маленькая иконка часов в верхней служебной строке СЛЕВА,
        # а не большая прямоугольная кнопка «История».
        self.assertIn('id="openHistory"', HTML_PAGE)
        topbar_pos = HTML_PAGE.find('class="topbar"')
        btn_pos = HTML_PAGE.find('id="openHistory"')
        card_pos = HTML_PAGE.find('class="card"')
        self.assertGreaterEqual(topbar_pos, 0)
        self.assertLess(topbar_pos, btn_pos)
        self.assertLess(btn_pos, card_pos)
        # Кнопка — иконка: внутри нет слова «История» как прямоугольник.
        btn_start = HTML_PAGE.find('id="openHistory"')
        btn_end = HTML_PAGE.find('</button>', btn_start)
        btn_html = HTML_PAGE[btn_start:btn_end]
        self.assertNotIn('>🕘 История<', HTML_PAGE[btn_start:btn_end + 20])
        self.assertIn('🕘', btn_html)
        self.assertIn('aria-label="Открыть историю"', btn_html)
        # Кнопка слева от бренда (идёт раньше .brand в topbar).
        brand_pos = HTML_PAGE.find('class="brand"')
        self.assertLess(btn_pos, brand_pos)
        # Компактный размер иконки в CSS (маленькая, не прямоугольник).
        self.assertIn('#openHistory', HTML_PAGE)
        self.assertIn('width: 38px', HTML_PAGE)
        self.assertIn('height: 38px', HTML_PAGE)

    def test_open_history(self):
        # Drawer существует как левая панель + отдельный scrim.
        self.assertIn('id="historyDrawer"', HTML_PAGE)
        self.assertIn('id="historyOverlay"', HTML_PAGE)
        self.assertIn('class="drawer"', HTML_PAGE)
        self.assertIn('class="scrim hidden"', HTML_PAGE)
        # Выезд СЛЕВА НАПРАВО: скрыт translateX(-105%), открыт translateX(0).
        self.assertIn('transform: translateX(-105%)', HTML_PAGE)
        self.assertIn('.drawer.open', HTML_PAGE)
        self.assertIn("transform: translateX(0)", HTML_PAGE)
        self.assertIn('transition: transform', HTML_PAGE)
        # Поверх кнопок калькулятора: fixed, left 0, z-index выше scrim.
        self.assertIn('position: fixed', HTML_PAGE)
        self.assertIn('left: 0', HTML_PAGE)
        self.assertIn('z-index: 50', HTML_PAGE)
        self.assertIn('z-index: 40', HTML_PAGE)
        # Занимает левую часть (не весь экран) — правая колонка видна.
        self.assertIn('width: min(78vw, 320px)', HTML_PAGE)
        # Открытие: рендер + drawer.open + показать scrim, .app не трогаем.
        self.assertIn("function openHistory()", HTML_PAGE)
        start = HTML_PAGE.find("function openHistory()")
        open_fn = HTML_PAGE[start:start + 300]
        self.assertIn("renderHistory()", open_fn)
        self.assertIn("drawer.classList.add('open')", open_fn)
        self.assertIn("overlay.classList.remove('hidden')", open_fn)
        self.assertIn('class="app"', HTML_PAGE)
        self.assertNotIn("app.classList", open_fn)

    def test_close_history(self):
        # Маленькая кнопка закрытия есть, большой верхней панели нет.
        self.assertIn('id="closeHistory"', HTML_PAGE)
        self.assertIn("function closeHistoryPanel()", HTML_PAGE)
        start = HTML_PAGE.find("function closeHistoryPanel()")
        fn_body = HTML_PAGE[start:start + 600]
        # Закрытие возвращает drawer влево и прячет scrim.
        self.assertIn("drawer.classList.remove('open')", fn_body)
        self.assertIn("overlay.classList.add('hidden')", fn_body)
        # X только закрывает: нет очистки ввода/истории.
        self.assertNotIn("exprEl.value = ''", fn_body)
        self.assertNotIn("history = []", fn_body)
        # Калькулятор остаётся работоспособным: фокус возвращается.
        self.assertIn("exprEl.focus()", fn_body)
        # Кнопка закрытия wired на closeHistoryPanel.
        self.assertIn("getElementById('closeHistory').onclick = closeHistoryPanel", HTML_PAGE)
        # Клик по scrim закрывает.
        self.assertIn("overlay.addEventListener('click'", HTML_PAGE)
        self.assertIn("closeHistoryPanel()", HTML_PAGE)
        # Esc закрывает drawer.
        self.assertIn("drawer.classList.contains('open')", HTML_PAGE)
        # Свайп-жест закрытия влево.
        self.assertIn("touchstart", HTML_PAGE)
        self.assertIn("touchend", HTML_PAGE)
        # Запрещённая большая панель «История / X / Очистить / Закрыть»:
        # нет history-head, нет второй кнопки закрытия, нет <h2>История</h2>.
        self.assertNotIn('history-head', HTML_PAGE)
        self.assertNotIn('closeHistoryBtn', HTML_PAGE)
        self.assertNotIn('<h2>', HTML_PAGE)

    def test_history_display(self):
        # Тёмный drawer в стиле калькулятора.
        self.assertIn('background: #1c2942', HTML_PAGE)
        # Список вертикальный и прокручиваемый.
        self.assertIn('id="historyList"', HTML_PAGE)
        self.assertIn('flex-direction: column', HTML_PAGE)
        self.assertIn('overflow-y: auto', HTML_PAGE)
        # Каждая запись: выражение + =результат.
        self.assertIn("className = 'hexpr'", HTML_PAGE)
        self.assertIn("className = 'hres'", HTML_PAGE)
        self.assertIn("s1.textContent = h.expression", HTML_PAGE)
        self.assertIn("s2.textContent = '= ' + h.result", HTML_PAGE)
        # Внизу кнопка «Очистить журнал».
        self.assertIn('id="clearHistory"', HTML_PAGE)
        self.assertIn('Очистить журнал', HTML_PAGE)
        foot_pos = HTML_PAGE.find('class="drawer-foot"')
        clear_pos = HTML_PAGE.find('id="clearHistory"')
        list_pos = HTML_PAGE.find('id="historyList"')
        self.assertLess(list_pos, foot_pos)
        self.assertLess(foot_pos, clear_pos)

    def test_empty_history_compact(self):
        # Компактное сообщение внутри drawer, drawer не на весь экран.
        self.assertIn('id="historyEmpty"', HTML_PAGE)
        self.assertIn('Журнал пуст', HTML_PAGE)
        self.assertIn("historyEmpty.style.display", HTML_PAGE)
        self.assertIn("history.length ? 'none' : 'block'", HTML_PAGE)
        # Drawer фиксированной левой ширины (см. test_open_history),
        # пустое состояние не растягивает его: список прячется.
        self.assertIn("historyList.style.display", HTML_PAGE)
        self.assertIn("history.length ? 'flex' : 'none'", HTML_PAGE)

    def test_clear_history(self):
        # Кнопка «Очистить журнал» обязана быть.
        self.assertIn('id="clearHistory"', HTML_PAGE)
        self.assertIn('Очистить журнал', HTML_PAGE)
        # Очистка удаляет все записи и перерисовывает, drawer остаётся открытым.
        self.assertIn("getElementById('clearHistory').onclick", HTML_PAGE)
        idx = HTML_PAGE.find("getElementById('clearHistory').onclick")
        end = HTML_PAGE.find("};", idx) + 2
        clear_body = HTML_PAGE[idx:end]
        self.assertIn("history = []", clear_body)
        self.assertIn("renderHistory()", clear_body)
        self.assertNotIn("closeHistoryPanel()", clear_body)
        # Python-модель очистки.
        h = CalculatorHistory()
        h.add("1+1", "2")
        h.clear()
        self.assertEqual(len(h), 0)
        self.assertEqual(h.get_all(), [])
        # После очистки — пустой вид без сломанной разметки.
        self.assertIn('id="historyEmpty"', HTML_PAGE)

    def test_add_history_entry(self):
        # Python-модель: добавление сохраняет выражение и результат, новые сверху
        h = CalculatorHistory(limit=5)
        h.add("2+2", "4")
        h.add("200+10%", "220")
        items = h.get_all()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], {"expression": "200+10%", "result": "220"})
        self.assertEqual(items[1], {"expression": "2+2", "result": "4"})
        # JS: pushHistory кладёт запись наверх, режет до 50, сохраняет и рисует
        self.assertIn("function pushHistory(expression, result)", HTML_PAGE)
        idx = HTML_PAGE.find("function pushHistory(expression, result)")
        js_body = HTML_PAGE[idx:idx + 400]
        self.assertIn("history.unshift({expression, result})", js_body)
        self.assertIn("slice(0, 50)", js_body)
        self.assertIn("saveHistory()", js_body)
        self.assertIn("renderHistory()", js_body)

    def test_select_history_entry(self):
        # Клик по записи: выражение возвращается в ввод, drawer закрывается
        self.assertIn("li.onclick", HTML_PAGE)
        idx = HTML_PAGE.find("li.onclick")
        click_body = HTML_PAGE[idx:idx + 400]
        self.assertIn("exprEl.value = h.expression", click_body)
        self.assertIn("closeHistoryPanel()", click_body)
        # После выбора фокус/каретка возвращаются в калькулятор для продолжения
        self.assertIn("setSelectionRange(exprEl.value.length", HTML_PAGE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
