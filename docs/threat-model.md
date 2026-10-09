# Модель угроз

Недоверенная команда выполняется в Linux-песочнице bubblewrap. Хостовый код запуска,
политика, гейт и журналы считаются доверенными. Прямые вызовы MCP сами по себе не включают
песочницу. Защита относится к командам, запущенным через `run_task`.

| Угроза | Механизм защиты | Подтверждающий тест |
| --- | --- | --- |
| Запись за пределы задачи | Единственный writable bind — workspace; остальные хостовые пути отсутствуют или read-only. Неудачная запись отклоняется G01; известные внешние изменения — G03. | `tests/test_demo_e2e.py::test_demo_e2e_accepts_scan_and_rejects_outside_write`; `tests/test_sandbox_runner.py::test_sandbox_negative_absolute_write_outside_workspace`; `tests/test_gate.py::test_g03_rejects_every_known_outside_change` |
| Побег через симлинк | Внешние цели недоступны в песочнице; G04 проверяет новые ссылки, patch использует дескрипторы каталогов и `O_NOFOLLOW`. | `tests/test_sandbox_runner.py::test_sandbox_negative_symlink_escape`; `tests/test_gate.py::test_g04_detects_new_escaping_links_omitted_by_runner`; `tests/test_apply_scoped_patch.py::test_symlink_escape_is_refused_without_touching_outside` |
| Сетевой доступ | Изолированный сетевой namespace; `network=True` запрещён до запуска; G06 проверяет политику и индикаторы stderr. | `tests/test_sandbox_runner.py::test_sandbox_negative_network_connect`; `tests/test_sandbox_policy.py::test_sandbox_policy_network_true_is_recorded_but_execution_rejected`; `tests/test_gate.py::test_g06_rejects_network_policy_or_stderr_evidence` |
| Утечка переменных окружения | `--clearenv` с явным allowlist; в демо allowlist пуст. Секреты нельзя включать в разрешённые переменные. | `tests/test_sandbox_runner.py::test_sandbox_negative_host_environment_leak` |
| Враждебный или битый вход инструмента | MCP проверяет типы и лишние поля до вызова; scan отказывает при неверной структуре; ошибочные hunks не применяются частично. | `tests/test_tool_registry.py::test_tool_registry_contracts_and_validation_before_handler`; `tests/test_scan_asset_usage.py::test_malformed_fixture_fails_instead_of_silently_skipping`; `tests/test_apply_scoped_patch.py::test_one_failed_hunk_rolls_back_all_hunks` |
| Обрезанный вывод как ложный успех | Runner ограничивает и дренирует вывод, отмечает truncation; G05 отклоняет неполный результат. | `tests/test_sandbox_runner.py::test_sandbox_runner_output_is_bounded_and_drained`; `tests/test_gate.py::test_g05_rejects_truncated_output` |
| Самоотчёт агента вместо факта | G02 использует снимки и реальный новый непустой обычный файл; текст stdout не доказывает успех. | `tests/test_gate.py::test_g02_self_report_is_not_new_nonempty_artifact` |
| Неполный или подделанный артефакт | Пустой, отсутствующий, старый файл или заявленный через `extra_paths` путь не заменяет новый артефакт; artifact glob требует подходящего файла. Сквозной тест проверяет содержимое отчёта демо. | `tests/test_gate.py::test_g02_self_report_is_not_new_nonempty_artifact`; `tests/test_gate.py::test_extra_path_cannot_fabricate_artifact`; `tests/test_loop.py::test_live_artifact_glob_requires_a_matching_new_artifact`; `tests/test_demo_e2e.py::test_demo_e2e_accepts_scan_and_rejects_outside_write` |

## Что вне границ модели угроз

— Уязвимости ядра, bubblewrap или Python, враждебный администратор хоста и подмена доверенного кода.
— Семантически ложное содержимое нового непустого файла: G02 проверяет факт появления,
  тип и размер, а не смысл, подпись, полноту JSON или соответствие задаче. Проверка содержимого
  в тесте демо покрывает только его фиксированный сценарий.
— Секреты в явно разрешённых переменных, workspace, read-only roots или аргументах команды.
  Журнал `run_started` содержит аргументы и политику; это не защищённое хранилище секретов.
— Гонки при внешнем изменении workspace во время запуска и враждебная подмена хостовых журналов.
  Гейт рассчитан на стабильный workspace; его снимки не наблюдают весь хост.
— Полная защита от исчерпания ресурсов: есть timeout, лимит захвата вывода и размер tmpfs,
  но нет общего лимита CPU, памяти, числа процессов или объёма записи в workspace.
— Автоматический откат отказанных изменений и изоляция прямого MCP-вызова без task loop.