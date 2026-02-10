#!/bin/sh
set -eu

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        0|false|FALSE|no|NO|off|OFF|"") return 1 ;;
        *)
            echo "GIT_GPG_SIGNING_ENABLED must be true or false" >&2
            exit 1
            ;;
    esac
}

git config --global user.name "${GIT_USER_NAME:-OpenCode Web}"
git config --global user.email "${GIT_USER_EMAIL:-opencode-web@localhost}"

if is_true "${GIT_GPG_SIGNING_ENABLED:-false}"; then
    : "${GIT_GPG_KEY_ID:?GIT_GPG_KEY_ID must be set when GPG signing is enabled}"

    private_key_file="${GIT_GPG_PRIVATE_KEY_FILE:-/app/.secrets/gpg/private.asc}"
    passphrase_file="${GIT_GPG_PASSPHRASE_FILE:-/app/.secrets/gpg/passphrase}"
    export GIT_GPG_PASSPHRASE_FILE="$passphrase_file"
    export GNUPGHOME="${GNUPGHOME:-/app/data/gnupg}"

    if [ ! -r "$private_key_file" ]; then
        echo "GPG private key is not readable: $private_key_file" >&2
        exit 1
    fi
    if [ ! -r "$passphrase_file" ]; then
        echo "GPG passphrase file is not readable: $passphrase_file" >&2
        exit 1
    fi

    mkdir -p "$GNUPGHOME"
    chmod 0700 "$GNUPGHOME"
    gpg --batch --import "$private_key_file"
    if ! gpg --batch --list-secret-keys "$GIT_GPG_KEY_ID" >/dev/null 2>&1; then
        echo "GPG secret key is unavailable: $GIT_GPG_KEY_ID" >&2
        exit 1
    fi

    git config --global gpg.format openpgp
    git config --global gpg.program /usr/local/bin/git-gpg-sign
    git config --global user.signingKey "$GIT_GPG_KEY_ID"
    git config --global commit.gpgSign true
    git config --global tag.gpgSign true
else
    git config --global commit.gpgSign false
    git config --global tag.gpgSign false
    git config --global --unset-all user.signingKey 2>/dev/null || true
    git config --global --unset-all gpg.format 2>/dev/null || true
    git config --global --unset-all gpg.program 2>/dev/null || true
fi

exec "$@"
