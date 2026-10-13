# syntax=docker/dockerfile:1.7
# web：フロントエンド（React + Vite）をビルドし、nginx（非root版）で配信する。/api は core に中継する。
# フォント・PDF.js・ECharts はすべてバンドルし、外部 CDN を使わない。

ARG NODE_IMAGE=node:22.23.3-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86
# E2E テスト用（ブラウザ入り）。frontend/package.json の @playwright/test と同じ版にする
ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27

# ---------------------------------------------------------------- フロントエンドのビルド（ビルド専用）
# HTTP_PROXY 等は Docker の定義済みビルド引数として渡す（イメージには残らない）
FROM ${NODE_IMAGE} AS frontend-build
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    DO_NOT_TRACK=1
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- lint・型検査・テスト（make lint / make test）
FROM frontend-build AS frontend-check
USER node
CMD ["sh", "-c", "npm run typecheck && npm run lint && npm test"]

# ---------------------------------------------------------------- E2E テスト（make test-e2e。ネットワークなしで実行）
# ブラウザはイメージに入っているものを使い、npm からは取得しない
FROM ${PLAYWRIGHT_IMAGE} AS frontend-e2e
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    DO_NOT_TRACK=1
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
# PDF ビューアの表示に使う論文は、デモ用の合成論文（API の応答もこの論文を解析したもの）
COPY demo/demo_paper_ja.pdf e2e/fixtures/paper.pdf
RUN npm run build
USER pwuser
CMD ["npx", "playwright", "test"]

# ---------------------------------------------------------------- 配信
FROM ${NGINX_IMAGE} AS web
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist/ /usr/share/nginx/html/
EXPOSE 8080
