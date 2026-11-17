#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")/.."
umask 077
secret_file=inventory/group_vars/all/secrets.yml

if [[ -e "$secret_file" ]]; then
    chmod 600 "$secret_file"
    if [[ "$(head -n 1 "$secret_file")" == '$ANSIBLE_VAULT;'* ]]; then
        printf 'Use ansible-vault edit to add missing values to the encrypted secrets file.\n' >&2
        exit 1
    fi
fi

for spec in mongo_dr_password:32 mongo_dr_key:48 dr_s3_root_secret:32 dr_s3_secret:32; do
    key=${spec%:*}
    bytes=${spec#*:}
    if [[ -f "$secret_file" ]] && grep -q "^$key:" "$secret_file"; then
        continue
    fi
    value=$(openssl rand -hex "$bytes")
    printf '\n%s: "%s"\n' "$key" "$value" >> "$secret_file"
done

printf 'Secrets are ready in %s\n' "$secret_file"
