# syntax=docker/dockerfile:1.7
# core / worker / gateway / searcher と、テスト用の dev イメージを作るマルチステージ Dockerfile。
# ビルドコンテキストはリポジトリのルート。各サービスのイメージには自分のコードだけを入れる
# （searcher のイメージには core のコードを含めない）。

ARG PYTHON_IMAGE=python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

# ---------------------------------------------------------------- base
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # テレメトリ・外部ダウンロードの無効化（SPEC 2.5）
    DO_NOT_TRACK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1 \
    ANONYMIZED_TELEMETRY=False

# ---------------------------------------------------------------- 依存のインストール（ビルド専用）
# HTTP_PROXY / HTTPS_PROXY / NO_PROXY は Docker の定義済みビルド引数として渡す。
# 宣言しなくても RUN で使え、イメージの履歴・実行時の環境変数には残らない。
FROM base AS deps
COPY requirements/ /tmp/requirements/
RUN python -m venv --without-pip /opt/venv

FROM deps AS deps-core
RUN pip --python /opt/venv/bin/python install -r /tmp/requirements/core.txt

FROM deps AS deps-gateway
RUN pip --python /opt/venv/bin/python install -r /tmp/requirements/gateway.txt

FROM deps AS deps-searcher
RUN pip --python /opt/venv/bin/python install -r /tmp/requirements/searcher.txt

FROM deps AS deps-dev
RUN pip --python /opt/venv/bin/python install -r /tmp/requirements/dev.txt

# ---------------------------------------------------------------- 実行用の共通部分
FROM base AS runtime
# 実行イメージに pip を残さない
RUN python -m pip uninstall -y pip \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/private /data/lancedb /data/db /data/audit \
 && chown -R app:app /data
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app

# ---------------------------------------------------------------- core / worker
FROM runtime AS core
COPY --from=deps-core /opt/venv /opt/venv
COPY core/ /app/core/
COPY config/models.yaml /app/config/models.yaml
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "core.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- gateway
FROM runtime AS gateway
COPY --from=deps-gateway /opt/venv /opt/venv
COPY gateway/ /app/gateway/
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- searcher
FROM runtime AS searcher
COPY --from=deps-searcher /opt/venv /opt/venv
COPY searcher/ /app/searcher/
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "searcher.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- テスト・lint（ソースはバインドマウント）
FROM runtime AS dev
COPY --from=deps-dev /opt/venv /opt/venv
ENV MYPY_CACHE_DIR=/tmp/.mypy_cache \
    RUFF_CACHE_DIR=/tmp/.ruff_cache
USER 10001:10001
CMD ["pytest"]
