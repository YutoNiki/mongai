# MongAI の共通操作。コマンドはすべてコンテナ内で実行する（ホストに必要なのは Docker と make だけ）。

COMPOSE      := docker compose -f compose.yaml
COMPOSE_DEV  := $(COMPOSE) -f compose.dev.yaml
COMPOSE_TEST := $(COMPOSE) -f compose.test.yaml --profile test
RUN_TEST     := $(COMPOSE_TEST) run --rm --no-deps test

ROLE   ?= logic_check
MODELS ?=

.PHONY: help setup build up down dev logs test test-integration test-e2e test-security lint format eval demo-paper check-highlights check-api bench-concurrency config clean clean-data

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

logs: ## ログ
	$(COMPOSE) logs -f --tail=200

test: ## 単体テスト（Python・フロントエンド。ネットワークなしのコンテナで実行）
	$(RUN_TEST) pytest tests/unit
	$(COMPOSE_TEST) build -q frontend
	$(COMPOSE_TEST) run --rm --no-deps frontend sh -c "npm test"

test-integration: ## GROBID を使う結合テスト（internal_net 内で実行）
	$(COMPOSE_TEST) run --rm integration

test-e2e: ## 画面の E2E テスト（Playwright、ネットワークなしのコンテナ。API は保存した応答で差し替える）
	$(COMPOSE_TEST) build -q e2e
	$(COMPOSE_TEST) run --rm --no-deps e2e

test-security: ## 検索ゲートウェイの検査（ネットワークなし）と、ネットワーク分離・プロキシ・マウントのテスト（実際の構成で検証）
	$(RUN_TEST) pytest tests/security
	$(COMPOSE) up -d --build --wait
	sh tests/security/run.sh

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
	$(COMPOSE_TEST) config -q
	@echo "compose の設定は有効です"

clean: ## コンテナとイメージの削除（data_private などのボリュームは消さない）
	$(COMPOSE_TEST) down --rmi local --remove-orphans

clean-data: ## ボリューム（アップロードした論文・解析結果を含む）の削除。確認あり
	@printf "data_private・lancedb・app_db・audit_log をすべて削除します。よろしいですか？ [y/N] "; \
	read ans; [ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || (echo "中止しました"; exit 1)
	$(COMPOSE_TEST) down -v --remove-orphans
