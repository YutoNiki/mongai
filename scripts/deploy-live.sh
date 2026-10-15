#!/bin/sh
# 運用中のサーバー（make live）に、CI が通った main の版（origin/deploy/live）を取り込む（docs/deploy.md）。
# systemd のタイマー（make deploy-timer）から数分おきに実行する。手動では make deploy-live。
#
# - 実行中・待機中の解析や抽出があれば、何もせずに次の回に回す（worker の再起動で失敗にしないため）
# - スキーマの版が上がる場合は、core・worker を止めてから取り込み、今のソースで移行してから起動する
# - 依存関係・Dockerfile・compose を変えた場合は、イメージを作り直して起動し直す
# - それ以外は取り込むだけ（ホットリロードで反映される）
# ログには版（コミット）と件数だけを出し、論文・解析の内容は出さない。
#
# 環境変数：DRY_RUN=1 で、取り込まずに何をするかだけを表示する。

set -eu

cd "$(dirname "$0")/.."

DEPLOY_BRANCH=deploy/live
COMPOSE_LIVE="docker compose -f compose.yaml -f compose.live.yaml"
DRY_RUN=${DRY_RUN:-0}

log() { echo "deploy-live: $*"; }

# 二重に実行しない
lock="${XDG_RUNTIME_DIR:-/tmp}/si-nsa-deploy-live.lock"
exec 9>"$lock"
flock -n 9 || { log "別の取り込みが実行中です"; exit 0; }

branch=$(git symbolic-ref --quiet --short HEAD || true)
if [ "$branch" != main ]; then
    log "チェックアウトが main ではない（${branch:-detached}）ため取り込みません"
    exit 1
fi

if ! git fetch -q origin "$DEPLOY_BRANCH"; then
    log "origin/$DEPLOY_BRANCH を取得できません（CI がまだ一度も通っていないか、通信できません）"
    exit 1
fi
target=$(git rev-parse FETCH_HEAD)
current=$(git rev-parse HEAD)

if [ "$target" = "$current" ] || git merge-base --is-ancestor "$target" "$current"; then
    exit 0
fi
if ! git merge-base --is-ancestor "$current" "$target"; then
    log "手元の main（$(git rev-parse --short HEAD)）に $DEPLOY_BRANCH に無いコミットがあるため取り込みません"
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    log "作業ツリーに未コミットの変更があるため取り込みません"
    exit 1
fi

# 実行中・待機中のジョブ（解析・ルール抽出・専門知識の抽出・提出要件の抽出）
jobs=$($COMPOSE_LIVE exec -T redis sh -c \
    'echo $(( $(redis-cli --scan --pattern "arq:in-progress:*" | wc -l) + $(redis-cli zcard arq:queue) ))') || {
    log "redis に接続できないため取り込みません（make live で起動しているか確認してください）"
    exit 1
}
if [ "$jobs" -gt 0 ]; then
    log "実行中・待機中のジョブが ${jobs} 件あるため、次の回に取り込みます"
    exit 0
fi

changed=$(git diff --name-only "$current" "$target")
schema_version() { git show "$1:core/store/schema.py" | sed -n 's/^SCHEMA_VERSION = //p'; }
migrate=0
[ "$(schema_version "$current")" != "$(schema_version "$target")" ] && migrate=1
rebuild=0
echo "$changed" | grep -Eq '^(requirements/|docker/|compose[^/]*\.yaml$|pyproject\.toml$|\.dockerignore$|frontend/(package(-lock)?\.json|\.npmrc|index\.html|vite\.config\.ts)$)' && rebuild=1

log "$(git rev-parse --short "$current") → $(git rev-parse --short "$target")（$(echo "$changed" | wc -l) ファイル、移行=${migrate}、再ビルド=${rebuild}）"
if [ "$DRY_RUN" = 1 ]; then
    log "DRY_RUN のため取り込みません"
    exit 0
fi

if [ "$migrate" = 1 ]; then
    $COMPOSE_LIVE stop core worker
fi
git merge -q --ff-only "$target"
if [ "$rebuild" = 1 ]; then
    $COMPOSE_LIVE build
fi
if [ "$migrate" = 1 ]; then
    $COMPOSE_LIVE run --rm --no-deps core python -m core.store.migrate
fi
if [ "$migrate" = 1 ] || [ "$rebuild" = 1 ]; then
    $COMPOSE_LIVE up -d --wait
fi
log "$(git rev-parse --short HEAD) を取り込みました"
