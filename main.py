"""Flet Android entry point для существующего HTML/JS калькулятора.

НЕ переписывает калькулятор: интерфейс берётся из calculator.HTML_PAGE
без изменений, математика — из calculator.evaluate_expression().

Как это работает офлайн (без интернета):
  1. Внутри приложения стартует локальный loopback HTTP-сервер
     (127.0.0.1, случайный свободный порт) на базе существующего
     CalculatorHandler из calculator.py.
  2. Flet WebView (flet-webview) открывает http://127.0.0.1:<port>/
  3. Существующий JS делает fetch('/api/calculate') -> тот же
     localhost -> calculator.evaluate_expression() локально.
  4. Никаких внешних URL и интернета не требуется.

Обычный запуск в Termux сохраняется:
  python calculator.py            # веб-GUI через run_gui()
  python calculator.py --cli      # консольный режим
Этот файл (main.py) используется ТОЛЬКО для Flet/APK-сборки.
"""

import flet as ft

try:
    import flet_webview as fwv
except ImportError:  # понятная ошибка при локальном запуске без зависимостей
    fwv = None

from calculator import start_local_calc_server


def main(page: ft.Page) -> None:
    """Точка входа Flet: сразу показывает экран калькулятора."""
    page.title = "Калькулятор"
    page.padding = 0
    page.bgcolor = "#0f172a"

    if fwv is None:
        page.add(
            ft.SafeArea(
                expand=True,
                content=ft.Column(
                    expand=True,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            "Нет пакета flet-webview.\n"
                            "Установите: pip install flet flet-webview",
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                ),
            )
        )
        return

    # Локальный офлайн-сервер: существующий HTML_PAGE + /api/calculate.
    _, port = start_local_calc_server(host="127.0.0.1", port=0)
    url = f"http://127.0.0.1:{port}/"
    print(f"  [flet] локальный калькулятор: {url} (офлайн, loopback)")

    webview = fwv.WebView(
        url=url,
        expand=True,
        bgcolor="#0f172a",
        on_page_started=lambda _: print("  [flet] страница калькулятора загружается..."),
        on_page_ended=lambda e: print(f"  [flet] страница загружена: {e.data}"),
        on_web_resource_error=lambda e: print(f"  [flet] WebView error: {e.data}"),
    )

    page.add(
        ft.SafeArea(
            expand=True,
            content=webview,
        )
    )


if __name__ == "__main__":
    ft.run(main)
