# MonGAI の共通操作。コマンドはすべてコンテナ内で実行する（ホストに必要なのは Docker と make だけ）。

.PHONY: setup

setup: ## git フックを設定（AI を共同著者とするコミットを拒否）
	git config core.hooksPath .githooks
	@echo "core.hooksPath を .githooks に設定しました"
