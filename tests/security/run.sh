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
GOOGLE_DISCOVERY_URL="https://accounts.google.com/.well-known/openid-configuration"
SERVICES="web core worker gateway searcher redis grobid oitoma_proxy egress_proxy authn auth_proxy"

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
for svc in $SERVICES; do
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

echo "== 認証サービス（authn）と認証専用のプロキシ（auth_proxy）"
expect_blocked "authn から example.com に直接到達できない" authn "$OUTSIDE_URL" ""
expect_blocked "authn から Google に直接（プロキシなしで）到達できない" authn "$GOOGLE_DISCOVERY_URL" ""
expect_blocked "auth_proxy が許可外のドメイン（example.com）を拒否する" \
    authn "$OUTSIDE_URL" "http://auth_proxy:3128"
expect_blocked "auth_proxy が書誌API（crossref）を拒否する（書誌検索用のプロキシとは分ける）" \
    authn "$ALLOWED_URL" "http://auth_proxy:3128"
expect_blocked "auth_proxy が おいとまAPIを拒否する" \
    authn "$OITOMA_MODELS_URL" "http://auth_proxy:3128"
expect_blocked "auth_proxy が Google 以外の googleapis.com を拒否する（ワイルドカードで許可しない）" \
    authn "https://storage.googleapis.com/" "http://auth_proxy:3128"
expect_blocked "auth_proxy が許可ドメインへの平文HTTPを拒否する" \
    authn "http://accounts.google.com/.well-known/openid-configuration" "http://auth_proxy:3128"
if out=$(probe authn "$GOOGLE_DISCOVERY_URL" "http://auth_proxy:3128"); then
    pass "authn から auth_proxy 経由で Google の OIDC の設定に到達できる"
else
    warn "authn から auth_proxy 経由で Google に到達できない（ホストの外部接続を確認）" "$out"
fi
for target in "http://core:8000/api/healthz" "http://gateway:8000/healthz" "http://searcher:8000/healthz" \
        "http://redis:6379/" "http://oitoma_proxy:3128/" "http://egress_proxy:3128/"; do
    expect_blocked "authn から $target に到達できない" authn "$target" ""
done
for svc in core worker gateway searcher; do
    expect_blocked "$svc から authn に到達できない" "$svc" "http://authn:8000/healthz" ""
    expect_blocked "$svc から auth_proxy 経由で Google に到達できない" \
        "$svc" "$GOOGLE_DISCOVERY_URL" "http://auth_proxy:3128"
done
expect_blocked "searcher から egress_proxy 経由で Google の認証に到達できない" \
    searcher "$GOOGLE_DISCOVERY_URL" "http://egress_proxy:3128"

aid=$(container_id authn)
if [ -n "$aid" ]; then
    mounts=$(docker inspect -f '{{range .Mounts}}{{.Name}}->{{.Destination}} {{end}}' "$aid")
    if echo "$mounts" | grep -E -q 'data_private|lancedb|app_db|audit_log|devauth'; then
        fail "authn に論文データ・DB・監査ログのボリュームがマウントされていない" "$mounts"
    else
        pass "authn に論文データ・DB・監査ログのボリュームがマウントされていない"
    fi
    if $COMPOSE exec -T authn python -c 'import os,sys; sys.exit(0 if not any(os.path.exists(p) for p in ("/data/private","/data/db","/data/audit","/data/lancedb")) else 1)'; then
        pass "authn から未公開論文・DB・監査ログのディレクトリが見えない"
    else
        fail "authn から未公開論文・DB・監査ログのディレクトリが見えない"
    fi
    perm=$($COMPOSE exec -T authn stat -c '%a' /data/authn/signing_key.json 2>/dev/null | tr -d '\r')
    if [ "$perm" = "600" ]; then
        pass "authn の署名の秘密鍵は所有者だけが読める（600）"
    else
        fail "authn の署名の秘密鍵は所有者だけが読める（600）" "perm=$perm"
    fi
fi
for svc in core worker gateway searcher web; do
    cid=$(container_id "$svc")
    [ -z "$cid" ] && continue
    if docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$cid" | grep -q 'authn_data'; then
        fail "$svc に認証サービスの秘密鍵・セッションのボリュームがマウントされていない"
    else
        pass "$svc に認証サービスの秘密鍵・セッションのボリュームがマウントされていない"
    fi
done
if $COMPOSE exec -T core python -c '
import pathlib, sys
files = [p for p in pathlib.Path("/keys").rglob("*") if p.is_file()]
bad = [str(p) for p in files if "private_key" in p.read_text(errors="ignore")]
sys.exit(1 if bad or not pathlib.Path("/keys/authn/authn.json").exists() else 0)'; then
    pass "core には authn の公開鍵だけがあり、秘密鍵は無い"
else
    fail "core には authn の公開鍵だけがあり、秘密鍵は無い"
fi
if $COMPOSE exec -T core sh -c 'touch /keys/authn/x 2>/dev/null'; then
    fail "core は authn の公開鍵を書き換えられない（読み取り専用）"
else
    pass "core は authn の公開鍵を書き換えられない（読み取り専用）"
fi
if [ -z "$(docker ps -q --filter label=com.docker.compose.project=mongai --filter label=com.docker.compose.service=devauth)" ]; then
    pass "本番相当の構成で開発用の devauth が動いていない"
else
    fail "本番相当の構成で開発用の devauth が動いていない"
fi

echo "== web（nginx）の認証の中継"
# internal_net の core のコンテナから web（nginx）に要求し、ブラウザからの要求と同じ経路を確かめる。
# 引数：メソッド パス [ヘッダー名=値 ...]。出力：ステータス 本文の先頭
WEBPROBE='
import sys, urllib.request as u, urllib.error as e
method, path, *pairs = sys.argv[1:]
headers = dict(p.split("=", 1) for p in pairs)
req = u.Request("http://web:8080" + path, method=method, headers=headers,
                data=b"x" if method == "POST" else None)
class NoRedirect(u.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
try:
    resp = u.build_opener(u.ProxyHandler({}), NoRedirect).open(req, timeout=8)
    status, body, location = resp.status, resp.read(300), ""
except e.HTTPError as err:
    status, body, location = err.code, err.read(300), err.headers.get("Location", "")
print(status, body.decode(errors="replace"), location)
'
web_req() {
    $COMPOSE exec -T core python -c "$WEBPROBE" "$@" 2>&1
}
# 動いている authn の設定（リダイレクト URI）から、設定したオリジンのホストと Cookie の名前を決める。
# 既定は http://localhost:8080（Cookie は mongai_session）。HTTPS で公開する構成では __Host- が付く
redirect_uri=$($COMPOSE exec -T authn printenv GOOGLE_OAUTH_REDIRECT_URI 2>/dev/null | tr -d '\r')
redirect_uri=${redirect_uri:-http://localhost:8080/auth/callback}
origin_host=$(printf '%s' "$redirect_uri" | sed -E 's#^[a-z]+://([^/]+).*#\1#')
case "$redirect_uri" in
    https://*) session_cookie="__Host-mongai_session" ;;
    *) session_cookie="mongai_session" ;;
esac
out=$(web_req GET /api/me "X-MongAI-Assertion=forged.forged.forged")
case "$out" in
    "200 "*'"authenticated":false'*) pass "ブラウザが送った X-MongAI-Assertion は捨てられ、ゲストとして扱われる" ;;
    *) fail "ブラウザが送った X-MongAI-Assertion は捨てられ、ゲストとして扱われる" "$out" ;;
esac
out=$(web_req GET /api/analyses "Cookie=$session_cookie=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
case "$out" in
    "401 "*invalid_session*) pass "無効なセッションの Cookie は 401（invalid_session）で、ゲストにならない" ;;
    *) fail "無効なセッションの Cookie は 401（invalid_session）で、ゲストにならない" "$out" ;;
esac
out=$(web_req GET /auth/verify)
case "$out" in
    "404 "*) pass "/auth/verify はブラウザから直接呼べない（404）" ;;
    *) fail "/auth/verify はブラウザから直接呼べない（404）" "$out" ;;
esac
out=$(web_req GET /api/healthz)
case "$out" in
    "200 "*'"status":"ok"'*) pass "GET /api/healthz はゲストで使える" ;;
    *) fail "GET /api/healthz はゲストで使える" "$out" ;;
esac
out=$(web_req GET /api/analyses)
case "$out" in
    "200 "*) pass "Cookie の無い要求はゲストとして共通デモを閲覧できる" ;;
    *) fail "Cookie の無い要求はゲストとして共通デモを閲覧できる" "$out" ;;
esac
out=$(web_req POST /auth/logout "Origin=https://evil.example")
case "$out" in
    "403 "*) pass "ほかのサイトからのログアウトの要求は拒否される（CSRF 対策）" ;;
    *) fail "ほかのサイトからのログアウトの要求は拒否される（CSRF 対策）" "$out" ;;
esac
# 設定と違う名前（web:8080）で開くと、要求の Host ではなく設定したオリジンに移す（オープンリダイレクトにしない）
out=$(web_req GET /auth/login)
case "$out" in
    *web:8080*) fail "/auth/login は authn に中継され、設定したオリジンにだけ移す" "$out" ;;
    "303 "*"/auth/login") pass "/auth/login は authn に中継され、設定したオリジンにだけ移す" ;;
    *) fail "/auth/login は authn に中継され、設定したオリジンにだけ移す" "$out" ;;
esac
# 設定したオリジン（既定は localhost:8080）で開くと、Google の画面へ移るか、未設定のエラーで画面に戻る（ループしない）
out=$(web_req GET /auth/login "Host=$origin_host")
case "$out" in
    "303 "*"https://accounts.google.com/"*|"303 "*"/?auth_error=not_configured") pass "設定したオリジンの /auth/login はリダイレクトのループにならない" ;;
    *) fail "設定したオリジンの /auth/login はリダイレクトのループにならない" "$out" ;;
esac
if $COMPOSE exec -T web nginx -t >/dev/null 2>&1; then
    pass "nginx の設定が有効（nginx -t）"
else
    fail "nginx の設定が有効（nginx -t）" "$($COMPOSE exec -T web nginx -t 2>&1 | tail -3)"
fi

echo "== ポート公開（web の 127.0.0.1:8080 のみ）"
for svc in $SERVICES; do
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
for svc in $SERVICES; do
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
