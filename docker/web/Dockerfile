# syntax=docker/dockerfile:1.7
# web：nginx（非root版）。フロントエンドのビルド成果物を配信し、/api を core に中継する。
# M5 で Node のビルドステージ（React + Vite、依存はすべてバンドル）を追加する。

ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86

FROM ${NGINX_IMAGE} AS web
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/web/static/ /usr/share/nginx/html/
EXPOSE 8080
