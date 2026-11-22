# mongodb

Ansible-роль для установки Percona Server for MongoDB и подготовки отдельного экземпляра с одноузловым replica set. Подходит для воспроизведения лаборатории на чистых ВМ.

## Требования

- Ubuntu 24.04 amd64, Python 3 и systemd.
- Права root через SSH или `become: true`.
- Доступ к репозиторию пакетов Percona.
- Свободные порт и каталог данных, выбранные для экземпляра.

Роль создаёт независимый replica set на каждом хосте. Запуск на трёх ВМ не объединяет их в один replica set. Для существующей базы приложения подключайте отдельно роль `pbm`.

## Зависимости

Другие роли и внешние коллекции Ansible не требуются. Роль устанавливает пакет сервера, `percona-mongodb-mongosh` и `python3-pymongo`.

## Переменные

Значения по умолчанию приведены из [defaults/main.yml](defaults/main.yml). Лаборатория переопределяет часть значений в [group_vars/mongodb.yml](../../../inventory/group_vars/mongodb.yml).

| Переменная | Тип | По умолчанию | Назначение |
|---|---|---|---|
| `mongodb_admin_password` | string | Обязательная | Пароль первого администратора, не менее 16 символов |
| `mongodb_key` | string | Обязательная | Ключ внутренней аутентификации replica set: 6–1024 символа из `A–Z`, `a–z`, `0–9`, `+`, `/`, `=` |
| `mongodb_home` | string | `/srv/mongodb` | Каталог экземпляра; данные находятся в подкаталоге `data/` |
| `mongodb_service` | string | `mongodb` | Имя службы systemd без `.service` |
| `mongodb_user` | string | `mongodb` | Пользователь и группа Linux для процесса MongoDB |
| `mongodb_uid` | integer или null | `null` | Фиксированные UID/GID; при `null` назначаются системой |
| `mongodb_port` | integer | `27017` | Порт MongoDB |
| `mongodb_bind_ip` | string | `127.0.0.1` | Адрес прослушивания |
| `mongodb_replica` | string | `rs0` | Имя одноузлового replica set |
| `mongodb_admin_user` | string | `admin` | Первый администратор MongoDB, создаётся в базе `admin` |
| `mongodb_cache_gb` | number | `0.5` | Размер кэша WiredTiger в ГиБ |
| `mongodb_oplog_mb` | integer | `2048` | Размер oplog в МиБ |
| `mongodb_package` | string | `percona-server-mongodb-server=8.0.38-16.noble` | Закреплённая версия пакета сервера |

В лаборатории `mongodb_admin_password` и `mongodb_key` подставляются из локальных секретов. Их создаёт `scripts/init-secrets.sh`; файл секретов исключён из Git. Для переноса роли передавайте собственные значения через Ansible Vault или другой защищённый источник переменных.

## Пример использования

Playbook для группы `mongodb` с параметрами из inventory этого проекта:

```yaml
- name: Prepare MongoDB instances
  hosts: mongodb
  become: true
  roles:
    - mongodb
```

При переносе роли скопируйте каталог `mongodb` в `playbooks/roles/` своего проекта и задайте обязательные переменные. В лабораторном inventory используются порт `27117`, каталог `/srv/mongo-dr`, служба и пользователь `mongo-dr`, replica set `drlab`.

## Что делает роль

1. Проверяет ОС, архитектуру и формат секретов; подключает репозиторий Percona и устанавливает пакеты.
2. Создаёт системного пользователя, каталог данных, конфигурацию `mongod.yml`, ключ и службу systemd.
3. Запускает MongoDB, инициализирует одноузловой replica set и создаёт первого администратора.
4. Записывает маркер `.ansible-mongodb`, по которому лабораторные сценарии распознают управляемый экземпляр.

Первичная инициализация не сбрасывает данные при повторном запуске. Конфигурационные изменения могут перезапустить службу. Роль не выполняет ротацию пароля существующего администратора; при несовпадении заданных учётных данных с существующими bootstrap завершится ошибкой. Параметр `mongodb_bind_ip` сам по себе не настраивает TLS, firewall и адреса участников replica set: bootstrap использует `localhost:<порт>`.

## Проверка результата

На ВМ лаборатории:

```bash
# Убедиться, что служба MongoDB работает.
sudo systemctl is-active mongo-dr.service
```

После настройки PBM командой `sudo /srv/mongo-dr/drctl pbm status` можно проверить подключение агента к MongoDB. Имена и пути выше относятся к inventory лаборатории; при переносе используйте свои значения.
