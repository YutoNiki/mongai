# SI-NSA の共通操作。コマンドはすべてコンテナ内で実行する（ホストに必要なのは Docker と make だけ）。

COMPOSE      := docker compose -f compose.yaml
COMPOSE_DEV  := $(COMPOSE) -f compose.dev.yaml
COMPOSE_LIVE := $(COMPOSE) -f compose.live.yaml
COMPOSE_TEST := $(COMPOSE) -f compose.test.yaml --profile test
RUN_TEST     := $(COMPOSE_TEST) run --rm --no-deps test

ROLE   ?= logic_check
MODELS ?=

.PHONY: help setup build up down dev live logs migrate migrate-live deploy-live deploy-timer deploy-timer-remove claim-legacy authn-rotate-key authn-revoke-sessions test test-integration test-e2e test-security test-security-offline lint format eval demo-paper check-highlights check-api bench-concurrency config clean clean-data

help: ## このヘルプを表示
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

setup: ## git フックを設定（AI を共同著者とするコミットを拒否）
	git config core.hooksPath .githooks
	@echo "core.hooksPath を .githooks に設定しました"

build: ## イメージのビルド
	$(COMPOSE) build
	$(COMPOSE_TEST) build test frontend

up: ## 起動（本番相当、全サービスが healthy になるまで待つ）
	$(COMPOSE) up -d --wait

down: ## 停止
	$(COMPOSE) down

dev: ## 開発モード（ホットリロード）
	$(COMPOSE_DEV) up --build

live: ## 運用中のサーバー向けの起動（本番相当の構成で、ソースの変更を自動で反映。worker の再起動で実行中の解析は失敗になる）
	$(COMPOSE_LIVE) up -d --build --wait

logs: ## ログ
	$(COMPOSE) logs -f --tail=200

migrate: ## データベースを今の版に移行（core・worker を止めてから実行。最初にバックアップを作る。make live で動かしているときは migrate-live）
	$(COMPOSE) run --rm --no-deps core python -m core.store.migrate

# make live ではソースをマウントして動かすため、イメージの中のコードは古いことがある。
# migrate はイメージのコードで移行するので、新しい版を知らずに「移行済み」と表示してしまう。こちらはマウントした今のソースで移行する
migrate-live: ## 運用中のサーバー（make live）のデータベースを移行（core・worker を止め、今のソースで移行してから起動し直す。実行中の解析は失敗になる）
	$(COMPOSE_LIVE) stop core worker
	$(COMPOSE_LIVE) run --rm --no-deps core python -m core.store.migrate
	$(COMPOSE_LIVE) up -d --wait core worker

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user

deploy-live: ## CI が通った main の版（origin/deploy/live）を運用中のサーバーに取り込む（実行中の解析があれば何もしない。DRY_RUN=1 で確認だけ）。docs/deploy.md
	DRY_RUN=$(DRY_RUN) sh scripts/deploy-live.sh

deploy-timer: ## deploy-live を5分おきに実行する systemd（ユーザー）のタイマーを入れて有効にする
	mkdir -p $(SYSTEMD_USER_DIR)
	for f in si-nsa-deploy-live.service si-nsa-deploy-live.timer; do \
		sed 's|@REPO@|$(CURDIR)|g' deploy/systemd/$$f > $(SYSTEMD_USER_DIR)/$$f; \
	done
	systemctl --user daemon-reload
	systemctl --user enable --now si-nsa-deploy-live.timer
	@echo "ログアウト中も動かすには loginctl enable-linger $$USER を実行してください（docs/deploy.md）"

deploy-timer-remove: ## deploy-live のタイマーを止めて削除する
	-systemctl --user disable --now si-nsa-deploy-live.timer
	rm -f $(SYSTEMD_USER_DIR)/si-nsa-deploy-live.service $(SYSTEMD_USER_DIR)/si-nsa-deploy-live.timer
	systemctl --user daemon-reload

claim-legacy: ## 移行で隔離したデータの持ち主を決める（例：make claim-legacy ARGS="--user U-… --doc <doc_id>"）
	$(COMPOSE) run --rm --no-deps core python -m core.store.claim $(ARGS)

authn-rotate-key: ## 認証サービスの署名の鍵を新しくする（数秒 API が 503 になる。ログイン中のセッションはそのまま）
	$(COMPOSE) stop authn
	$(COMPOSE) run --rm --no-deps authn python -m authn.manage rotate-key
	$(COMPOSE) start authn

authn-revoke-sessions: ## すべてのログインのセッションを無効にする（全員がゲストに戻る）
	$(COMPOSE) run --rm --no-deps authn python -m authn.manage revoke-sessions

test: ## 単体テスト（Python・フロントエンド。ネットワークなしのコンテナで実行）
	$(RUN_TEST) pytest tests/unit
	$(COMPOSE_TEST) build -q frontend
	$(COMPOSE_TEST) run --rm --no-deps frontend sh -c "npm test"

test-integration: ## GROBID を使う結合テスト（internal_net 内で実行）
	$(COMPOSE_TEST) build -q test
	$(COMPOSE_TEST) run --rm integration

test-e2e: ## 画面の E2E テスト（Playwright、ネットワークなしのコンテナ。API は保存した応答で差し替える）
	$(COMPOSE_TEST) build -q e2e
	$(COMPOSE_TEST) run --rm --no-deps e2e

test-security: test-security-offline ## 検索ゲートウェイの検査（ネットワークなし）と、ネットワーク分離・プロキシ・マウントのテスト（実際の構成で検証）
	$(COMPOSE) up -d --build --wait
	sh tests/security/run.sh

test-security-offline: ## 検索ゲートウェイの検査だけ（ネットワークなしのコンテナ。サービスを起動しない。CI でも実行）
	$(RUN_TEST) pytest tests/security

lint: ## ruff / mypy / eslint / tsc
	$(RUN_TEST) sh -c "ruff check . && ruff format --check . && mypy"
	$(COMPOSE_TEST) build -q frontend
	$(COMPOSE_TEST) run --rm --no-deps frontend sh -c "npm run typecheck && npm run lint"

format: ## ruff format で整形（ソースは読み取り専用マウントなので差分の表示のみ）
	$(RUN_TEST) ruff format --diff .

eval: ## モデル評価（例：make eval ROLE=logic_check MODELS=qwen3.8-27b-it,Ternary-Bonsai-2-27B EVAL_ARGS="--lang ja --limit 5"）。結果は eval/results/
	@test -n "$(MODELS)" || (echo "MODELS を指定してください" >&2; exit 1)
	$(COMPOSE) run --rm -u "$$(id -u):$$(id -g)" -v ./eval:/app/eval worker python -m eval.run --role $(ROLE) --models $(MODELS) $(EVAL_ARGS)

demo-paper: ## デモ用の合成論文（問題を仕込んだもの）を demo/demo_paper_ja.pdf に作り直す（仕込んだ問題は docs/demo.md）
	$(COMPOSE_TEST) run --rm --no-deps -u "$$(id -u):$$(id -g)" -v ./scripts:/app/scripts:ro -v ./demo:/app/demo test python scripts/make_demo_paper.py

check-highlights: ## 指摘の位置の診断（例：make check-highlights ANALYSIS=A-xxxx。FIX=1 で位置を保存）。本文は表示しない
	@test -n "$(ANALYSIS)" || (echo "ANALYSIS を指定してください" >&2; exit 1)
	$(COMPOSE) exec -T core python -m core.annotate.check $(ANALYSIS) $(if $(FIX),--fix,)

check-api: ## おいとまAPIへの疎通と機能の確認（oitoma_proxy 経由）
	$(COMPOSE_TEST) run --rm api_check

LEVELS ?= 1,4,8,16,32
bench-concurrency: ## 同時リクエスト数ごとの おいとまAPI の処理量を測る（合成データのみ。LEVELS=1,4,8,16,32 THINKING=1）
	$(COMPOSE_TEST) build api_check
	$(COMPOSE_TEST) run --rm api_check python -m core.llm.bench --levels $(LEVELS) $(if $(THINKING),--thinking,)

config: ## compose ファイルの検証
	$(COMPOSE) config -q
	$(COMPOSE_DEV) config -q
	$(COMPOSE_LIVE) config -q
	$(COMPOSE_TEST) config -q
	@echo "compose の設定は有効です"

clean: ## コンテナとイメージの削除（data_private などのボリュームは消さない）
	$(COMPOSE_TEST) down --rmi local --remove-orphans

clean-data: ## ボリューム（アップロードした論文・解析結果を含む）の削除。確認あり
	@printf "data_private・lancedb・app_db・audit_log をすべて削除します。よろしいですか？ [y/N] "; \
	read ans; [ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || (echo "中止しました"; exit 1)
	$(COMPOSE_TEST) down -v --remove-orphans
