#!/bin/sh
# ネットワーク分離・プロキシの許可リスト・マウントのセキュリティテスト（SPEC 2.7）。
# 起動中の compose.yaml の構成に対して、各コンテナの中から実際に通信して確かめる。
#
# 使い方：make test-security（全サービスを起動してから実行される）
# 必要なもの：Docker と Docker Compose v2 のみ（コンテナ内の Python 標準ライブラリで通信する）

set -u

cd "$(dirname "$0")/../.." || exit 1

COMPOSE="docker compose -f compose.yaml"
OITOMA_MODELS_URL="https://ironical.okayama-u.ac.jp/ai/v1/models"
OUTSIDE_URL="https://example.com/"
ALLOWED_URL="https://api.crossref.org/works?rows=0"

passed=0
failed=0
warned=0

pass() { passed=$((passed + 1)); echo "PASS  $1"; }
fail() { failed=$((failed + 1)); echo "FAIL  $1"; [ -n "${2:-}" ] && echo "      $2"; }
warn() { warned=$((warned + 1)); echo "WARN  $1"; [ -n "${2:-}" ] && echo "      $2"; }

# コンテナ内で URL を開く。成功（2xx/3xx）なら終了コード0。
# 引数：サービス名 URL プロキシ（空文字なら直接接続）
PROBE='
import sys, urllib.request as u
url, proxy = sys.argv[1], sys.argv[2]
handler = u.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
try:
    resp = u.build_opener(handler).open(url, timeout=8)
    print("HTTP", resp.status)
except Exception as exc:
    print(type(exc).__name__, str(exc)[:160])
    sys.exit(1)
'

probe() {
    $COMPOSE exec -T "$1" python -c "$PROBE" "$2" "$3" 2>&1
}

expect_blocked() {
    desc="$1"; shift
    if out=$(probe "$@"); then
        fail "$desc" "到達できてしまった：$out"
    else
        pass "$desc"
    fi
}

expect_reachable() {
    desc="$1"; shift
    if out=$(probe "$@"); then
        pass "$desc"
    else
        fail "$desc" "$out"
    fi
}

container_id() {
    $COMPOSE ps -q "$1"
}

echo "== 稼働確認"
for svc in web core worker gateway searcher redis grobid oitoma_proxy egress_proxy; do
    if [ -n "$(container_id "$svc")" ]; then
        pass "$svc が起動している"
    else
        fail "$svc が起動していない（make up を確認）"
    fi
done

echo "== core 側（学外に出られない。おいとまAPIだけに到達できる）"
for svc in core worker gateway; do
    expect_blocked "$svc から example.com に直接到達できない" "$svc" "$OUTSIDE_URL" ""
    expect_blocked "$svc から oitoma_proxy 経由で example.com に到達できない" \
        "$svc" "$OUTSIDE_URL" "http://oitoma_proxy:3128"
    expect_blocked "$svc から oitoma_proxy 経由で書誌API（crossref）に到達できない" \
        "$svc" "$ALLOWED_URL" "http://oitoma_proxy:3128"
done
expect_blocked "core から おいとまAPIに直接（プロキシなしで）到達できない" \
    core "$OITOMA_MODELS_URL" ""
expect_reachable "core から oitoma_proxy 経由で おいとまAPIに到達できる" \
    core "$OITOMA_MODELS_URL" "http://oitoma_proxy:3128"
expect_blocked "core から oitoma_proxy 経由で おいとまAPIに平文HTTPで到達できない" \
    core "http://ironical.okayama-u.ac.jp/ai/v1/models" "http://oitoma_proxy:3128"

echo "== core と searcher の直接通信の禁止"
expect_blocked "core から searcher に直接到達できない" core "http://searcher:8000/healthz" ""
expect_blocked "worker から searcher に直接到達できない" worker "http://searcher:8000/healthz" ""
expect_blocked "searcher から core に到達できない" searcher "http://core:8000/api/healthz" ""
expect_blocked "searcher から oitoma_proxy に到達できない" \
    searcher "$OITOMA_MODELS_URL" "http://oitoma_proxy:3128"
expect_reachable "gateway から searcher に到達できる" gateway "http://searcher:8000/healthz" ""

echo "== searcher 側（許可した書誌APIだけに出られる）"
expect_blocked "searcher から example.com に直接到達できない" searcher "$OUTSIDE_URL" ""
expect_blocked "egress_proxy が許可外のドメイン（example.com）を拒否する" \
    searcher "$OUTSIDE_URL" "http://egress_proxy:3128"
expect_blocked "egress_proxy が おいとまAPIを拒否する（searcher から おいとまに出さない）" \
    searcher "$OITOMA_MODELS_URL" "http://egress_proxy:3128"
expect_blocked "egress_proxy が許可ドメインへの平文HTTPを拒否する" \
    searcher "http://api.crossref.org/works?rows=0" "http://egress_proxy:3128"
if out=$(probe searcher "$ALLOWED_URL" "http://egress_proxy:3128"); then
    pass "searcher から egress_proxy 経由で許可ドメイン（crossref）に到達できる"
else
    warn "searcher から egress_proxy 経由で crossref に到達できない（ホストの外部接続を確認）" "$out"
fi

echo "== searcher に論文データがマウントされていない"
sid=$(container_id searcher)
if [ -n "$sid" ]; then
    mounts=$(docker inspect -f '{{range .Mounts}}{{.Type}}:{{.Name}}:{{.Source}}->{{.Destination}} {{end}}' "$sid")
    if echo "$mounts" | grep -E -q 'data_private|lancedb|app_db|audit_log|/data'; then
        fail "searcher に論文データ・DBのボリュームがマウントされていない" "$mounts"
    else
        pass "searcher に論文データ・DBのボリュームがマウントされていない"
    fi
    if $COMPOSE exec -T searcher python -c 'import os,sys; sys.exit(0 if not os.path.exists("/data") else 1)'; then
        pass "searcher に /data が存在しない"
    else
        fail "searcher に /data が存在しない"
    fi
fi

echo "== ポート公開（web の 127.0.0.1:8080 のみ）"
for svc in web core worker gateway searcher redis grobid oitoma_proxy egress_proxy; do
    cid=$(container_id "$svc")
    [ -z "$cid" ] && continue
    bindings=$(docker inspect -f '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}}:{{.HostPort}}->{{$p}} {{end}}{{end}}' "$cid" | xargs)
    if [ "$svc" = "web" ]; then
        if [ "$bindings" = "127.0.0.1:8080->8080/tcp" ]; then
            pass "web は 127.0.0.1:8080 だけを公開している"
        else
            fail "web は 127.0.0.1:8080 だけを公開している" "$bindings"
        fi
    elif [ -n "$bindings" ]; then
        fail "$svc はポートを公開していない" "$bindings"
    else
        pass "$svc はポートを公開していない"
    fi
done

echo "== 非root・読み取り専用"
for svc in web core worker gateway searcher redis grobid oitoma_proxy egress_proxy; do
    cid=$(container_id "$svc")
    [ -z "$cid" ] && continue
    uid=$($COMPOSE exec -T "$svc" id -u 2>/dev/null | tr -d '\r')
    if [ -n "$uid" ] && [ "$uid" != "0" ]; then
        pass "$svc は非root（uid=$uid）で動いている"
    else
        fail "$svc は非rootで動いている" "uid=$uid"
    fi
    ro=$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$cid")
    if [ "$ro" = "true" ]; then
        pass "$svc のルートファイルシステムは読み取り専用"
    else
        fail "$svc のルートファイルシステムは読み取り専用"
    fi
done

echo
echo "結果：PASS $passed / FAIL $failed / WARN $warned"
[ "$failed" -eq 0 ]
