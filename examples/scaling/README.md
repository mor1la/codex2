# Сравнение баз объёмом 1–5 ГиБ

Один прогон каждого размера: начальный объём документов BSON 1, 2, 3, 4 и 5 ГиБ, полный логический снимок и три цикла изменений по 300 операций. Нагрузка — 5 операций/с в течение 60 секунд, веса insert/update/delete — 25/60/15. Каждый результат проверяется восстановлением и SHA-256.

## Изоляция

На двух уже существующих лабораторных ВМ создаются отдельные экземпляры MongoDB: порт `27217`, каталог `/srv/mongo-dr-benchmark`, replica set `drbench`, служба `mongo-dr-benchmark`. Для S3 выделяется отдельный префикс. Новые ВМ и S3 Gateway не создаются; существующие базы и архивы не удаляются. Требуются уже установленные Percona Server 8.0.38 и PBM 2.17.0, как в основной лаборатории.

Перед каждым размером генератор пересоздаёт только `dr_benchmark` и только при имени replica set `drbench`. Остальные базы этого экземпляра должны быть служебными: иначе полный бэкап включит и их данные. Получатель перед каждым восстановлением создаётся пустым; прежний каталог данных сохраняется. Учитывайте его объём при проверке свободного места.

Во время начального наполнения тестового экземпляра PITR выключен, после полного снимка включается. Поэтому измеряются изменения после базовой копии, а создание исходного набора не заполняет архив лишним oplog. Это режим сравнительного эксперимента, а не рекомендация выключать PITR на рабочей базе.

## Запуск

Все команды выполняются на Ansible-контроллере из корня проекта. ВМ должны иметь SSH-доступ и доступ к S3. Генерация выполняется на ВМ с PyMongo и boto3 из основной установки.

```bash
# Подготовить отдельный inventory с адресами тех же лабораторных ВМ.
cp -n examples/scaling/hosts.example.yml inventory/benchmark.yml
mkdir -p artifacts
# Подготовить настройки существующего S3 с новым уникальным префиксом.
cp -n examples/scaling/config.example.yml artifacts/scaling-runtime.yml
```

Укажите адреса ВМ в inventory. В конфигурации архива задайте действующие endpoint, регион и реквизиты S3; имя региона должно соответствовать настройке уже работающего gateway. Секрет берётся из `inventory/group_vars/all/secrets.yml`, как в основном проекте. Не используйте префикс другой серии экспериментов.

```bash
# Создать отдельные экземпляры и агенты; установленные пакеты не меняются.
ansible-playbook -i inventory/benchmark.yml playbooks/scaling-setup.yml \
  -e @artifacts/scaling-runtime.yml
```

Для каждого размера выполните команды ниже вручную. Начните с `size_gib=1`; после успешной проверки повторите блок со значениями 2, 3, 4 и 5. Для новой серии выберите новый префикс S3 и неиспользованный каталог отчётов.

```bash
# Выбрать объём BSON и отдельный каталог отчётов на контроллере.
size_gib=1
report_dir="$(pwd)/artifacts/manual-${size_gib}gib"
mkdir -p "$report_dir"

# Проверить свободное место на всех трёх ВМ перед прогоном.
ansible -i inventory/benchmark.yml -e @artifacts/scaling-runtime.yml \
  all -m ansible.builtin.command -a 'df -h /srv'

# Пересоздать тестовую БД нужного объёма, сохранить полный снимок и 900 изменений.
ansible-playbook -i inventory/benchmark.yml playbooks/experiment.yml \
  -e @artifacts/scaling-runtime.yml \
  -e experiment_host=benchmark-source -e experiment_db=dr_benchmark \
  -e "experiment_target_gib=$size_gib" -e experiment_reset_benchmark_database=true \
  -e experiment_payload=4096 -e experiment_seed=42 \
  -e experiment_rate=5 -e experiment_duration=60 -e experiment_cycles=3 \
  -e '{"experiment_weights":[25,60,15]}' \
  -e "experiment_report_path=$report_dir/experiment.json"

# Подготовить пустой тестовый получатель, восстановить точку и проверить данные.
ansible-playbook -i inventory/benchmark.yml playbooks/restore.yml \
  -e @artifacts/scaling-runtime.yml \
  -e restore_target=benchmark-target -e restore_confirm=benchmark-target \
  -e restore_archive=scaling -e restore_reinitialize=true \
  -e "restore_manifest=$report_dir/experiment.json" \
  -e "restore_report_path=$report_dir/restore.json"

# Проверить итог перед переходом к следующему размеру.
cat "$report_dir/restore.json"
```

Успешный результат: `success: true`, `verification_status: passed`. В отчёте `experiment.json` находятся время и объём полной копии, а в `restore.json` — время PITR и проверки. Автоматического перехода к следующему размеру нет.

Пять полных копий потребуют около 15 ГиБ S3 сверх существующего архива; оставьте запас, например 8 ГиБ. На получателе накапливаются сохранённые каталоги предыдущих восстановлений. При нехватке места остановитесь и освободите его перед продолжением. Сохранённый прогон измерял место после удаления старых тестовых каталогов сверх одного предыдущего; повторяемые здесь команды их автоматически не удаляют.

При прерывании Ansible сначала проверьте удалённую операцию: асинхронный генератор может продолжать работать. Если генерация закончилась, а восстановление нет, повторите только `restore.yml` с готовым отчётом. При `running: true` в отчёте восстановления проверьте PBM перед повторной подготовкой получателя.

После серии остановите только тестовые службы, чтобы они не продолжали занимать ресурсы и архивировать oplog:

```bash
ansible -i inventory/benchmark.yml -e @artifacts/scaling-runtime.yml mongodb \
  -m ansible.builtin.systemd_service -a 'name=mongo-dr-benchmark-pbm state=stopped enabled=false'
ansible -i inventory/benchmark.yml -e @artifacts/scaling-runtime.yml mongodb \
  -m ansible.builtin.systemd_service -a 'name=mongo-dr-benchmark state=stopped enabled=false'
```

## Графики и ограничения

Таблицы и графики проведённой серии приведены в [README проекта](../../README.md#практические-результаты-и-проверка-корректности). Изображения находятся в `images/`. Новые запуски сохраняют отдельные отчёты в выбранном каталоге `artifacts/`; общая сводка автоматически не собирается.

- `timings.png`: длительность полной копии, команды PITR и проверки данных. Подготовка пустого получателя не входит во время команды PBM; вся операция отдельно записана как `restore_total_seconds`.
- `archive-size.png`: объём полной копии и новых сегментов oplog на отдельных панелях с разными единицами.

Объём по X — реально измеренная сумма BSON документов; размер файлов WiredTiger отличается. Целевой объём может быть превышен на размер одного документа. Данные — псевдослучайные байты, сжатие `s2`; это не модель степени сжатия любого приложения. Один прогон на размер не позволяет оценить статистический разброс. Хосты используются и другими службами, глобальные кэши ОС не сбрасываются.
