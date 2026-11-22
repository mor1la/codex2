# Регулярная проверка на готовом Ansible-контроллере

Это пример для уже подготовленного контроллера, без роли его развёртывания. На нём должны быть Ansible, Python 3, проект в `/opt/mongo-dr-lab`, настроенные inventory/секреты и SSH-доступ к узлам. Каталоги проекта, сценарии и секреты должны принадлежать оператору; если сервис запускается от root, код и inventory не должны быть доступны другим пользователям для записи.

Сначала **на контроллере из корня проекта** проверьте сценарий вручную:

```bash
# Проверить SSH к узлам из inventory.
ansible all -m ping
# Подготовить компоненты при первой установке новой копии проекта.
ansible-playbook playbooks/setup.yml
# Выполнить учение с блокировкой повторного запуска и записью результата.
python3 scripts/run-drill.py
```

Runner запускает `playbooks/drill.yml`. Сценарий использует зарезервированную базу `dr_scheduled_check` и пересоздаёт данные только на явно выбранном управляемом получателе. `experiment_host`, `drill_target` и `drill_archive` задаются в inventory. При ошибке runner возвращает код 1 и записывает неуспех; длительность и время последнего успеха сохраняются в JSON и textfile.

Для установки примера systemd **на том же контроллере**:

```bash
# Скопировать два готовых unit-файла; при другом пути сначала измените их.
sudo install -m 0644 examples/scheduler/mongo-dr-drill.service /etc/systemd/system/
sudo install -m 0644 examples/scheduler/mongo-dr-drill.timer /etc/systemd/system/
# Перечитать unit-файлы и включить проверку по воскресеньям в 06:30 МСК (03:30 UTC).
sudo systemctl daemon-reload
sudo systemctl enable --now mongo-dr-drill.timer
# Посмотреть ближайший запуск.
systemctl list-timers mongo-dr-drill.timer --no-pager
# Запустить тот же сервис сейчас и дождаться результата.
sudo systemctl start mongo-dr-drill.service
```

Чтение результатов **на контроллере**:

```bash
# Результат и время всего учения.
cat artifacts/drill.json
# Проверка документов и время PITR.
cat artifacts/restore.json
# Подробные шаги Ansible для диагностики ошибки.
tail -n 80 artifacts/drill.log
```

Если локальные секреты зашифрованы Vault, автоматическому запуску нужен защищённый способ получения пароля Vault — например менеджер секретов CI или `ANSIBLE_VAULT_PASSWORD_FILE` с правами 0600. Не храните пароль Vault в Git и не используйте интерактивный запрос в systemd. Код, запускаемый таким сервисом от root, должен контролироваться администратором.

В своём CI или cron достаточно запускать `python3 scripts/run-drill.py` из корня проекта. Установка планировщика и SSH-ключей в роли PBM не входит.
