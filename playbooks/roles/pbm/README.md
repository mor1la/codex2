# pbm

Самостоятельная Ansible-роль для резервного копирования существующей MongoDB с помощью Percona Backup for MongoDB. Настраивает логические копии в S3, архивирование oplog для PITR, расписание, ротацию и локальные проверки состояния. Отдельные входы роли выполняют backup и restore.

## Требования

- Работающий совместимый MongoDB replica set и пользователь с правами PBM.
- Агент на каждом узле replica set с данными; URI агента указывает на его локальный `mongod`.
- Доступный S3-бакет и права на перечисление, чтение, запись и удаление объектов архива.
- Linux с Python 3, systemd и правами root.
- Для автоматической установки пакетов: Ubuntu 24.04 amd64 и доступ к репозиторию Percona.

Существующая MongoDB может быть подготовлена любым способом. Роль не создаёт базу, её пользователей, S3 и сеть. Для другого Linux заранее установите совместимые `pbm`, `pbm-agent`, Python-модули `pymongo`, `boto3`, `yaml` и задайте `pbm_install_packages: false`. Такой перенос требует отдельной проверки. Текущая роль использует systemd и предназначена для ВМ; готовой установки в Kubernetes Pod в ней нет.

## Зависимости

В [meta/main.yml](meta/main.yml) нет зависимостей от других ролей. Внешние коллекции Ansible не требуются. При `pbm_install_packages: true` роль устанавливает PBM, `python3-boto3`, `python3-yaml` и `python3-pymongo`.

## Переменные

### Обязательные параметры подключения

| Переменная | Тип | Назначение |
|---|---|---|
| `pbm_uri` | string | MongoDB URI для CLI: вход в целевой replica set |
| `pbm_agent_uri` | string | MongoDB URI для локального агента, обычно с `directConnection=true` |
| `pbm_s3_endpoint` | string | URL S3, начинающийся с `http://` или `https://` |
| `pbm_s3_bucket` | string | Существующий бакет |
| `pbm_s3_access` | string | Идентификатор учётной записи S3 |
| `pbm_s3_secret` | string | Её секретный ключ |

Для использования роли задайте параметры из таблицы выше в своём inventory или playbook.

В нашей лаборатории адреса и реквизиты S3 хранятся в списке `backup_archives`. Переменная `pbm_archive: lab` выбирает из него хранилище `lab`, а файл [group_vars/pbm_agents.yml](../../../inventory/group_vars/pbm_agents.yml) подставляет его настройки и параметры подключения к MongoDB в переменные роли `pbm_*`. Так не приходится повторно указывать одни и те же значения.

В другом проекте можно сразу задать `pbm_uri`, `pbm_agent_uri` и параметры `pbm_s3_*`. Копировать наш список `backup_archives` и использовать `pbm_archive` для этого не требуется.

### Настройки по умолчанию

Точные значения находятся в [defaults/main.yml](defaults/main.yml). В таблице показаны исходные значения роли; inventory лаборатории часть из них переопределяет.

| Переменная | Тип | По умолчанию | Назначение |
|---|---|---|---|
| `pbm_home` | string | `/srv/mongodb-backup` | Конфигурация, программы и каталог `state/` |
| `pbm_user` | string | `mongodb-backup` | Пользователь Linux для агента и проверок |
| `pbm_service` | string | `mongodb` | Префикс служб, например `mongodb-pbm.service` и `mongodb-backup.timer` |
| `pbm_database_service` | string | Пустая строка | Имя службы MongoDB без `.service`, если нужен порядок запуска агента после неё |
| `pbm_install_packages` | boolean | `true` | Установить пакеты из репозитория Percona |
| `pbm_package` | string | `percona-backup-mongodb=2.17.0-1.noble` | Закреплённая версия пакета |
| `pbm_configure` | boolean | `true` | Применять общую конфигурацию PBM к replica set |
| `pbm_schedule_enabled` | boolean | `true` | Включить таймер полных копий |
| `pbm_calendar` | string | `*-*-* 02:15:00 UTC` | Расписание systemd: ежедневно в 05:15 МСК (02:15 UTC) |
| `pbm_retention_days` | integer | `7` | Срок хранения для `pbm cleanup` после успешной копии; не менее 2 дней |
| `pbm_pitr_enabled` | boolean | `true` | Включить архивирование oplog |
| `pbm_slice_minutes` | number | `1` | Интервал нарезки oplog в минутах, больше нуля |
| `pbm_s3_region` | string | `msk` | Имя региона для подписи запросов S3; для внешнего S3 укажите значение провайдера |
| `pbm_s3_prefix` | string | `pbm` | Префикс объектов внутри бакета |
| `pbm_metrics_enabled` | boolean | `true` | Включить таймер локальных проверок |
| `pbm_rpo_seconds` | integer | `180` | Максимальный возраст доступной точки для проверки здоровья; это порог, а не гарантия фактического RPO |
| `pbm_restore_confirm` | string | Пустая строка | Для restore: должно совпасть с `inventory_hostname` получателя |
| `pbm_restore_point` | string | Пустая строка | Для restore: UTC `YYYY-MM-DDTHH:MM:SS` из доступного диапазона PITR |
| `pbm_restore_namespace` | string | Пустая строка | Для restore: база и коллекция или маска, например `myapp.*` |
| `pbm_restore_list_only` | boolean | `false` | При вызове restore только импортировать и показать каталог, без восстановления данных |
| `pbm_restore_report_path` | string | `{{ playbook_dir }}/../artifacts/restore-{{ inventory_hostname }}.json` | JSON на контроллере; рядом автоматически создаётся файл `.prom` |
| `pbm_restore_report_metadata` | dictionary | `{}` | Дополнительные несекретные сведения для отчёта, например имя архива |
| `pbm_restore_prepare_tasks` | string | Пустая строка | Путь к файлу задач подготовки получателя на контроллере; выполняется после проверок безопасности |
| `pbm_restore_verify_tasks` | string | Пустая строка | Путь к задачам прикладной проверки; они должны завершаться ошибкой при несовпадении данных |
| `pbm_restore_cleanup_tasks` | string | Пустая строка | Путь к завершающим задачам, выполняемым и после ошибки; например возврат источника после учения |

Для нескольких агентов **одного replica set** задавайте `pbm_configure: true` и `pbm_schedule_enabled: true` только на одном выбранном хосте. На остальных агентах оба флага должны быть `false`. PITR — настройка всего replica set. В лаборатории два независимых replica set: источник архивирует oplog, на получателе PITR и расписание выключены.

## Примеры использования

### Установка и настройка

```yaml
- name: Configure backups for existing MongoDB
  hosts: pbm_agents
  become: true
  roles:
    - pbm
```

Пример предполагает, что обязательные переменные уже заданы в inventory и защищённом файле секретов. Готовые [inventory](../../../examples/backup/hosts.example.yml) и [playbook](../../../examples/backup/playbook.yml) показывают подключение к существующему replica set из трёх узлов. Команды запуска и подготовка секретов описаны в [инструкции интеграции](../../../docs/integration.md).

Обычный вызов роли устанавливает и настраивает агент; он не выполняет restore и не создаёт первый полный снимок отдельной задачей. Включённый таймер затем запускает копии по расписанию. Начальную копию можно явно создать через вход `backup`.

### Полная копия

```yaml
- name: Back up one selected replica set
  hosts: "{{ backup_target }}"
  become: true
  tasks:
    - name: Create a full logical backup and apply retention
      ansible.builtin.include_role:
        name: pbm
        tasks_from: backup
```

Операция требует уже настроенного PBM. В лаборатории готовый `playbooks/backup.yml` дополнительно проверяет, что `backup_target` — один известный хост из `pbm_agents`.

### Восстановление

```yaml
- name: Restore onto an isolated existing MongoDB
  hosts: "{{ restore_target }}"
  become: true
  tasks:
    - name: Restore the selected namespace to a UTC point
      ansible.builtin.include_role:
        name: pbm
        tasks_from: restore
      vars:
        pbm_pitr_enabled: false
        pbm_schedule_enabled: false
        pbm_restore_confirm: "{{ restore_confirm }}"
        pbm_restore_namespace: "{{ app_namespace }}"
        pbm_restore_point: "{{ target_utc }}"
```

Получатель и PBM на нём должны быть подготовлены заранее с выключенными PITR и расписанием. Его URI должны указывать на получателя, а параметры S3 — на нужный архив. `restore_confirm` задаётся отдельно и должен точно совпасть с именем получателя в inventory. Переданное значение `false` не выключает фактически работающий PITR в этом примере: роль проверяет реальную конфигурацию и при включённом PITR откажется выполнять restore.

Роль запрещает восстановление системных баз `admin`, `config`, `local`, импортирует каталог S3 и запускает `pbm restore --ns … --time … --wait --yes`. Восстановление перезаписывает выбранные данные. По умолчанию роль не подключается к источнику, не создаёт пустую MongoDB и не выполняет прикладную проверку данных. Лабораторный [restore.yml](../../restore.yml) передаёт задачи подготовки и проверки через соответствующие параметры; в своём проекте их можно оставить пустыми или передать свои файлы задач.

### Результат и уведомления

Каждое начатое восстановление сохраняет JSON-отчёт и метрики на Ansible-контроллере, в том числе при ошибке. Программа `files/restore_report.py` входит в роль и использует только стандартную библиотеку Python контроллера. Реквизиты MongoDB, S3 и необработанный текст ошибки в отчёт не передаются.

`success` показывает итог всего запрошенного сценария, `restore_success` — результат команды PBM. Проверка данных имеет отдельный `verification_status`: `not_requested`, `not_run`, `passed` или `failed`. Без задач проверки успешное восстановление не объявляется проверенным. Прикладные задачи при успехе могут положить подробности в факт `pbm_restore_verification`; лаборатория использует для него результаты SHA-256, индексов и `validate`.

При ошибке `failed_stage` содержит её этап: проверка параметров получателя, подготовка, настройка, импорт каталога, восстановление, проверка данных или завершающие задачи. Ошибка сохраняется, затем роль возвращает неуспешный результат Ansible. Просмотр каталога и `--check` не обновляют отчёт восстановления.

Подключите соседний файл `.prom` к существующему Node Exporter на контроллере и правило `RestoreFailed` из [примера мониторинга](../../../examples/monitoring/README.md). Тогда одинаково отслеживаются отдельное восстановление и восстановление внутри полного учения. У разных получателей должны быть отдельные пути отчётов. При оборванном SSH или Ansible итог может не записаться: начатую операцию отслеживает правило `RestoreStuck`.

## Что делает роль

| Вход | Действие |
|---|---|
| `main` — обычное подключение роли | Установка, URI агента и CLI, конфигурация S3/PITR, службы, расписание и локальные метрики |
| `backup` | Полная логическая копия с компрессией `s2`, затем очистка по сроку хранения; результат записывается в `state/backup.json` |
| `restore` | Проверка получателя, выбор архива, импорт каталога, PITR, запрошенная проверка данных и запись результата на контроллере |

Агент использует `agent.env`, команды — `pbm.env`; оба файла находятся в `pbm_home`. Конфигурация S3 хранится в `pbm.yml`. Файлы с реквизитами имеют режим `0640`; задачи с секретами используют `no_log`. Для исходных переменных используйте Ansible Vault или другой защищённый источник.

Проверки создают `state/health.json` и `state/metrics.prom`. Роль не устанавливает Prometheus, Grafana, Alertmanager и не отправляет Telegram-сообщения самостоятельно. Для существующего мониторинга есть [примеры правил и дашборда](../../../examples/monitoring/README.md).

## Проверка результата

На ВМ с PBM при значениях роли по умолчанию:

```bash
# Проверить процесс агента.
sudo systemctl is-active mongodb-pbm.service
# Проверить видимость MongoDB и агентов PBM.
sudo /srv/mongodb-backup/drctl pbm status
# Показать полные копии и интервалы, доступные для PITR.
sudo /srv/mongodb-backup/drctl pbm list
# Прочитать результат последней локальной проверки, если её таймер включён.
sudo cat /srv/mongodb-backup/state/health.json
```

При первом запуске проверка PITR может быть неуспешной до появления базовой копии и выгруженных сегментов. В лаборатории пути начинаются с `/srv/mongo-dr`, службы — с `mongo-dr-`. Успешный статус агента сам по себе не доказывает пригодность архива: выполните пробное восстановление и проверку данных.
