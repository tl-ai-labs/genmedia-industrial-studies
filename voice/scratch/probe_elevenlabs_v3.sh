#!/usr/bin/env bash
# Probe the ElevenLabs account for v3 readiness.
#
#   FREE by default  — only GET metadata endpoints, no characters spent.
#   --paid           — also fires up to 4 tiny (~8 char) generate calls to
#                      learn whether eleven_v3 / eleven_v3_conversational work
#                      on the route the adapter uses. ~32 characters total.
#
# Run:   bash voice/scratch/probe_elevenlabs_v3.sh [--paid]
# Reads the key from voice/.env (never printed in full).

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/../.env"
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a
[ -n "${ELEVENLABS_API_KEY:-}" ] || { echo "ELEVENLABS_API_KEY not set in voice/.env"; exit 1; }

API=https://api.elevenlabs.io/v1
PY=python3
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "key: ${ELEVENLABS_API_KEY:0:6}...  (${#ELEVENLABS_API_KEY} chars)"
echo "curl: $(curl --version | head -1)"
echo

# get URL -> prints HTTP status, byte count, then pretty JSON (or raw on error)
getj () {
  local url="$1" body="$TMP/body" hdr="$TMP/hdr"
  local code
  code=$(curl -sS -m 40 --compressed -D "$hdr" -o "$body" -w '%{http_code}' \
           -H "xi-api-key: $ELEVENLABS_API_KEY" -H 'Accept: application/json' \
           "$url" 2>"$TMP/err") || {
    echo "  curl failed: $(cat "$TMP/err")"; return 1; }
  local n; n=$(wc -c < "$body" | tr -d ' ')
  echo "  HTTP $code, ${n} bytes"
  if [ "$n" -eq 0 ]; then
    echo "  (empty body) response headers:"; sed 's/^/    /' "$hdr"; return 1
  fi
  "$PY" -c "
import sys, json
raw = open('$body','rb').read()
try:
    d = json.loads(raw)
except Exception as e:
    print('  not JSON:', e); print('  raw[:600]:', raw[:600]); sys.exit()
open('$TMP/last.json','w').write(json.dumps(d))
print('  parsed ok')
"
}

echo "==================== GET /v1/user/subscription ===================="
if getj "$API/user/subscription"; then
  "$PY" -c "
import json
d = json.load(open('$TMP/last.json'))
for k in ('tier','status','character_count','character_limit',
          'can_extend_character_limit','allowed_to_extend_character_limit',
          'next_character_count_reset_unix'):
    if k in d: print(f'  {k:38} {d[k]}')
if 'character_limit' in d:
    print(f'  {\"characters remaining\":38} {d[\"character_limit\"]-d.get(\"character_count\",0)}')
"
fi
echo

echo "==================== GET /v1/models ===================="
if getj "$API/models"; then
  "$PY" -c "
import json
d = json.load(open('$TMP/last.json'))
if not isinstance(d, list):
    print('  unexpected:', json.dumps(d)[:600]); raise SystemExit
by = {m.get('model_id'): m for m in d}
print('  model_ids on this account:')
for mid in sorted(by): print('    -', mid)
print()
for mid in ('eleven_v3','eleven_v3_conversational','eleven_flash_v2_5',
            'eleven_multilingual_v2','eleven_turbo_v2_5'):
    m = by.get(mid)
    if not m:
        print(f'  {mid:28} NOT LISTED'); continue
    fl = [k for k in ('can_do_text_to_speech','can_stream','can_use_style',
                      'can_do_voice_conversion','serves_pro_voices',
                      'requires_alpha_access') if m.get(k)]
    print(f'  {mid:28} {m.get(\"name\",\"\")}')
    print(f'  {\"\":28} {\", \".join(fl) or \"-\"}   langs={len(m.get(\"languages\") or [])}')
"
fi
echo

echo "==================== GET /v1/voices  (voice_map ids) ===================="
if getj "$API/voices"; then
  "$PY" -c "
import json
d = json.load(open('$TMP/last.json'))
vs = d.get('voices', d if isinstance(d,list) else [])
have = {v.get('voice_id') for v in vs}
want = {
 'XrExE9yKIg1WjnnlVkGX':'Matilda (female_mid_warm)',
 'cjVigY5qzO86Huf0OWal':'Eric (male_mid_neutral)',
 'pqHfZKP75CvOlQylNhV4':'Bill (npc_quartermaster)',
 'FGY2WhTYpPnrIDTdsKH5':'Laura (npc_scout)',
 'pNInz6obpgDQGcFmaJgB':'Adam (npc_handler)',
 'EXAVITQu4vr4xnSDxMaL':'Sarah (npc_medic)',
 'SAz9YHcvj6GT2YYXdXww':'River (npc_engineer)',
 'cgSgspJ2msm6clMCkdW9':'Jessica (npc_trader)',
}
print(f'  account has {len(have)} voices')
for vid,label in want.items():
    print(f'  [{\"ok  \" if vid in have else \"MISS\"}] {vid}  {label}')
"
fi
echo

if [ "${1:-}" != "--paid" ]; then
  echo "free probe done. re-run with --paid for the generate-route test (~32 chars)."
  exit 0
fi

echo "==================== PAID: generate-route test ===================="
VOICE=XrExE9yKIg1WjnnlVkGX
for MODEL in eleven_v3 eleven_v3_conversational; do
  for ROUTE in "" "/stream"; do
    code=$(curl -sS -m 60 --compressed -o "$TMP/g" -w '%{http_code}' \
      -H "xi-api-key: $ELEVENLABS_API_KEY" -H 'Content-Type: application/json' \
      -X POST "$API/text-to-speech/$VOICE$ROUTE?output_format=pcm_24000" \
      -d "{\"text\":\"Testing.\",\"model_id\":\"$MODEL\"}") || code="curl-fail"
    n=$(wc -c < "$TMP/g" | tr -d ' ')
    if [ "$code" = "200" ]; then note="audio ${n}B"; else note="$(head -c 200 "$TMP/g")"; fi
    printf '  %-26s POST ...text-to-speech/{voice}%-8s -> %s  %s\n' \
      "$MODEL" "${ROUTE:-/}" "$code" "$note"
  done
done
echo
echo "200 = usable there | 400/422 = model invalid on that route | 401/403 = plan lacks access"
